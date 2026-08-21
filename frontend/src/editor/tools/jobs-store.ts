/**
 * Jobs in flight.
 *
 * **Deliberately not in the editor store.** A job is not part of the document:
 * it has no place in the undo history, it must not make the project dirty, and
 * it outlives the panel that started it — deselecting a clip while captions run
 * cannot be allowed to stop watching them. So it lives here, beside the
 * document store rather than inside it.
 *
 * What *is* an edit is the result, and that goes through `useEditor.commit`
 * like anything a user typed. The whole design of the AI tools is that their
 * output is an ordinary undoable change, not a special mode — a suggestion the
 * user dislikes costs one press of ⌘Z, not a dialog asking whether they are
 * sure.
 */

import { create } from 'zustand'

import { useEditor } from '@/editor/state/store'
import { locateClip } from '@/editor/state/timeline-document'
import { isFinished, watchJobs } from '@/editor/tools/job-watch'
import { placeRemovals, placeWords } from '@/editor/tools/results'
import type { CaptionsResult, SmartTrimResult } from '@/editor/tools/results'
import { ApiError, getAccessToken } from '@/lib/api/client'
import { cancelJob, createJob, estimateJob, readJobResult } from '@/lib/api/endpoints'
import type { CreateJobRequest, EstimateResponse, JobResponse } from '@/lib/api/endpoints'

export type ToolName = 'captions' | 'smart_trim' | 'color_analysis'

export interface ColorAnalysisResult {
  lut: string
  strength: number
  scene: { exposure: string; whiteBalance: string; contrast: string }
  alternatives: { lut: string; strength: number }[]
}

export interface ToolRun {
  jobId: string
  tool: ToolName
  /** The clip the result applies to — echoed back by the server, not tracked here. */
  clipId: string
  status: JobResponse['status']
  progress: number
  error: { code: string; message: string } | null
  /** Set once the result has been read and written into the document. */
  applied: boolean
}

interface ToolsState {
  /** Keyed by job id. One clip can have more than one tool running at once. */
  runs: Record<string, ToolRun>
  /**
   * Caption clips the recogniser was unsure about — contract §6.2: *"`c` below
   * 0.7 is worth flagging in the UI so the user checks it."*
   *
   * **Session state, not document state, and deliberately so.** The contract's
   * `TextClip` has no confidence field, because confidence describes how a word
   * was *produced* rather than what it is; adding one would put a number in
   * every saved project that means nothing after the first correction. So the
   * flag lives here and goes away on reload — by which point the run has been
   * reviewed, which is the only moment it was useful.
   */
  uncertain: ReadonlySet<string>
  /** Keyed by `${tool}:${clipId}` — what the panel puts on the button. */
  estimates: Record<string, EstimateResponse>
  busy: Record<string, boolean>
  lastError: string | null

  estimate: (tool: ToolName, clipId: string) => Promise<void>
  run: (
    tool: ToolName,
    clipId: string,
    options?: { strength?: string; language?: string },
  ) => Promise<void>
  cancel: (jobId: string) => Promise<void>
  ingest: (job: JobResponse) => void
  connect: (projectId: string) => void
  disconnect: () => void
}

export const estimateKey = (tool: ToolName, clipId: string): string => `${tool}:${clipId}`

let stream: { close(): void } | null = null

export const useTools = create<ToolsState>((set, get) => ({
  runs: {},
  uncertain: new Set<string>(),
  estimates: {},
  busy: {},
  lastError: null,

  /**
   * Price the job before the user commits — contract §6.1.
   *
   * Exact rather than indicative: both endpoints run the same function on the
   * server, so what the button says is what the click charges.
   */
  estimate: async (tool, clipId) => {
    const request = buildRequest(tool, clipId)
    if (!request) return
    const key = estimateKey(tool, clipId)
    set((state) => ({ busy: { ...state.busy, [key]: true } }))
    try {
      const quote = await estimateJob(request)
      set((state) => ({ estimates: { ...state.estimates, [key]: quote } }))
    } catch {
      // A price we cannot fetch is a button without a number on it, which is
      // recoverable. Failing the whole panel over it would not be.
    } finally {
      set((state) => ({ busy: { ...state.busy, [key]: false } }))
    }
  },

  run: async (tool, clipId, options = {}) => {
    const request = buildRequest(tool, clipId, options)
    if (!request) return
    const key = estimateKey(tool, clipId)
    set((state) => ({ busy: { ...state.busy, [key]: true }, lastError: null }))

    try {
      // One key per *intention*: generated at the press and reused by every
      // retry of it, so a network timeout cannot charge the user twice
      // (contract §1).
      const job = await createJob(request, crypto.randomUUID())
      get().ingest(job)
    } catch (error) {
      set({
        lastError:
          error instanceof ApiError ? messageFor(error) : 'Something went wrong starting that.',
      })
    } finally {
      set((state) => ({ busy: { ...state.busy, [key]: false } }))
    }
  },

  cancel: async (jobId) => {
    try {
      get().ingest(await cancelJob(jobId))
    } catch {
      // Already finished, most likely. The next read will say so.
    }
  },

  /**
   * Take a job the server has told us about — from the create call, a poll, or
   * the reconnect re-sync — and apply it if it has finished.
   *
   * **One place decides a job is done.** The socket only ever prompts a read;
   * this is what acts on one, so a duplicate event cannot apply a result twice.
   */
  ingest: (job) => {
    const existing = get().runs[job.id]
    if (existing?.applied) return

    const run: ToolRun = {
      jobId: job.id,
      tool: job.tool as ToolName,
      clipId: job.clipId ?? existing?.clipId ?? '',
      status: job.status,
      progress: job.progress,
      error: job.error ?? null,
      applied: false,
    }
    set((state) => ({ runs: { ...state.runs, [job.id]: run } }))

    if (job.status === 'succeeded') {
      void applyResult(job, run).then(() => {
        set((state) => ({ runs: { ...state.runs, [job.id]: { ...run, applied: true } } }))
      })
    }
  },

  connect: (projectId) => {
    const token = getAccessToken()
    if (!token || stream) return
    stream = watchJobs({ token, projectId, onUpdate: (job) => get().ingest(job) })
  },

  disconnect: () => {
    stream?.close()
    stream = null
  },
}))

/**
 * Write a finished job's result into the document, as one undoable commit.
 *
 * Reading the result handles both shapes the contract defines: inline under
 * 256 KB, and a signed URL above it. A caption run on twenty minutes of speech
 * takes the second path, so it is not an edge case to skip (§6.3).
 */
async function applyResult(job: JobResponse, run: ToolRun): Promise<void> {
  const editor = useEditor.getState()
  const found = run.clipId ? locateClip(editor.timeline, run.clipId) : null
  if (!found || !('assetId' in found.clip)) return
  const clip = found.clip

  if (job.tool === 'captions') {
    const result = await readJobResult<CaptionsResult>(job)
    if (!result) return
    const placed = placeWords(result.words, clip)
    const before = new Set(captionIds(editor.timeline))
    editor.applyCaptions({ clipId: clip.id, words: placed, sourceJobId: job.id })

    // The ids the operation just created, matched to the words it was given.
    // Both lists are in the same order, which is the operation's own contract.
    const created = captionIds(useEditor.getState().timeline).filter((id) => !before.has(id))
    const flagged = new Set(
      created.filter((_id, index) => placed[index]?.uncertain === true),
    )
    useTools.setState({ uncertain: flagged })
    return
  }

  if (job.tool === 'smart_trim') {
    const result = await readJobResult<SmartTrimResult>(job)
    if (!result) return
    const removals = placeRemovals(result.removals, clip)
    if (removals.length > 0) editor.applySmartTrim(clip.id, removals)
    return
  }

  if (job.tool === 'color_analysis') {
    const result = await readJobResult<ColorAnalysisResult>(job)
    if (!result) return
    editor.applyColorGrade(clip.id, {
      lut: result.lut,
      strength: result.strength,
      sourceJobId: job.id,
    })
  }
}

/** Every caption clip id, in track order. */
function captionIds(timeline: ReturnType<typeof useEditor.getState>['timeline']): string[] {
  const text = timeline.tracks.find((track) => track.kind === 'text')
  if (!text) return []
  return text.clips
    .filter((clip) => 'kind' in clip && clip.kind === 'caption')
    .map((clip) => clip.id)
}

/** The request body for a tool, or null when the clip cannot be worked on. */
function buildRequest(
  tool: ToolName,
  clipId: string,
  options: { strength?: string; language?: string } = {},
): CreateJobRequest | null {
  const editor = useEditor.getState()
  const found = locateClip(editor.timeline, clipId)
  if (!found || !('assetId' in found.clip)) return null
  const clip = found.clip

  const input: Record<string, unknown> = {
    assetId: clip.assetId,
    clipId: clip.id,
    // The window the *clip* shows, not the whole file. Analysing footage the
    // clip has trimmed away would charge the user for frames nobody can see —
    // and the server prices exactly what it is asked to analyse.
    rangeMs: {
      startMs: clip.sourceInMs,
      endMs: clip.sourceInMs + Math.round(clip.durationMs * clip.speed),
    },
  }
  if (tool === 'smart_trim') input.strength = options.strength ?? 'medium'
  if (tool === 'captions') input.language = options.language ?? 'auto'

  const body: Record<string, unknown> = { tool, input }
  if (editor.projectId) body.projectId = editor.projectId
  return body as unknown as CreateJobRequest
}

/** Turn an API error code into a sentence a person can act on. */
function messageFor(error: ApiError): string {
  switch (error.code) {
    case 'INSUFFICIENT_CREDITS':
      return 'Not enough credits for that one.'
    case 'FAIR_USE_EXCEEDED':
      return 'This account has passed its monthly fair-use ceiling. Get in touch and we will sort it out.'
    case 'UNSUPPORTED_MEDIA':
      return 'That clip is not ready yet.'
    default:
      return error.message || 'Something went wrong starting that.'
  }
}

export function isRunning(run: ToolRun): boolean {
  return !isFinished({ status: run.status } as JobResponse)
}
