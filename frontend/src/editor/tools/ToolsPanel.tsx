'use client'

/**
 * The AI tools, for the selected clip.
 *
 * Three rules from the charter and the contract, all of them about telling the
 * user the truth before they spend anything:
 *
 * 1. **The price is on the button, before the click.** `POST /jobs/estimate`
 *    runs the same function the charge does, so the number here is exact rather
 *    than indicative (contract §6.1). A tool that cannot be afforded says so on
 *    the button instead of after a failed press.
 * 2. **Editing continues while a job runs.** Nothing here blocks the timeline.
 *    A captions run on twenty minutes of speech takes minutes, and an editor
 *    that freezes for it is an editor nobody will start.
 * 3. **The result is an ordinary undoable edit.** No preview mode, no "apply?"
 *    dialog — it lands, and ⌘Z takes it back.
 */

import { useEffect } from 'react'

import { selectSelectedAnyClip, useEditor } from '@/editor/state/store'
import { estimateKey, isRunning, useTools } from '@/editor/tools/jobs-store'
import type { ToolName } from '@/editor/tools/jobs-store'
import type { AnyClip, MediaClip } from '@/editor/state/timeline-document'

const TOOLS: { name: ToolName; label: string; blurb: string }[] = [
  { name: 'captions', label: 'Captions', blurb: 'One clip per word, timed to the speech' },
  { name: 'smart_trim', label: 'Smart trim', blurb: 'Find silence, filler and repeats' },
  { name: 'color_analysis', label: 'Colour', blurb: 'Suggest a grade from the picture' },
]

const LANGUAGES = [
  { value: 'auto', label: 'Detect' },
  { value: 'en', label: 'English' },
  { value: 'fr', label: 'Français' },
  { value: 'hi', label: 'हिन्दी' },
]

const STRENGTHS = ['light', 'medium', 'aggressive'] as const

export function ToolsPanel({ projectId }: { projectId: string | null }) {
  const clip = useEditor(selectSelectedAnyClip)
  const runs = useTools((state) => state.runs)
  const lastError = useTools((state) => state.lastError)
  const connect = useTools((state) => state.connect)
  const disconnect = useTools((state) => state.disconnect)
  const estimate = useTools((state) => state.estimate)

  // One socket for the session, not one per panel. It also re-syncs on every
  // reconnect, which is how a job that finished while the laptop slept is
  // found rather than waited for.
  useEffect(() => {
    if (!projectId) return
    connect(projectId)
    return () => disconnect()
  }, [projectId, connect, disconnect])

  // Price everything the moment a clip is selected, so the numbers are already
  // there when the user looks at them.
  const clipId = clip && isMedia(clip) ? clip.id : null
  useEffect(() => {
    if (!clipId) return
    for (const tool of TOOLS) void estimate(tool.name, clipId)
  }, [clipId, estimate])

  const active = Object.values(runs).filter((run) => isRunning(run))

  return (
    <aside
      className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l px-4 py-3 text-xs"
      style={{ borderColor: 'var(--color-rule)', color: 'var(--color-ink-2)' }}
      data-testid="tools-panel"
    >
      <h2 className="text-[11px] tracking-wide uppercase" style={{ color: 'var(--color-ink-3)' }}>
        Tools
      </h2>

      {!clipId ? (
        <p style={{ color: 'var(--color-ink-3)' }}>Select a clip to run a tool on it.</p>
      ) : (
        TOOLS.map((tool) => <ToolRow key={tool.name} tool={tool} clipId={clipId} />)
      )}

      {lastError && (
        <p role="alert" style={{ color: 'var(--color-danger, #f87171)' }}>
          {lastError}
        </p>
      )}

      {active.length > 0 && (
        <div className="mt-2 flex flex-col gap-2">
          <h3 className="text-[11px] tracking-wide uppercase" style={{ color: 'var(--color-ink-3)' }}>
            Running
          </h3>
          {active.map((run) => (
            <RunRow key={run.jobId} jobId={run.jobId} />
          ))}
        </div>
      )}
    </aside>
  )
}

function ToolRow({
  tool,
  clipId,
}: {
  tool: { name: ToolName; label: string; blurb: string }
  clipId: string
}) {
  const key = estimateKey(tool.name, clipId)
  const quote = useTools((state) => state.estimates[key])
  const busy = useTools((state) => state.busy[key] ?? false)
  const run = useTools((state) => state.run)
  const running = useTools((state) =>
    Object.values(state.runs).some(
      (each) => each.clipId === clipId && each.tool === tool.name && isRunning(each),
    ),
  )

  const [language, setLanguage] = useLocal('auto')
  const [strength, setStrength] = useLocal<'light' | 'medium' | 'aggressive'>('medium')

  // `blockedBy` is the code `POST /jobs` *would* return. Putting it on the
  // button is the whole reason the estimate endpoint exists (contract §6.1).
  const blocked = quote?.blockedBy ?? null
  const disabled = busy || running || blocked !== null

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span style={{ color: 'var(--color-ink)' }}>{tool.label}</span>
        {quote && (
          <span className="tnum" style={{ color: 'var(--color-ink-faint)' }}>
            {quote.credits} cr · ~{formatSeconds(quote.estimatedSeconds)}
          </span>
        )}
      </div>
      <p style={{ color: 'var(--color-ink-3)' }}>{tool.blurb}</p>

      {tool.name === 'captions' && (
        <Choice
          label="Language"
          value={language}
          options={LANGUAGES}
          onChange={setLanguage}
        />
      )}
      {tool.name === 'smart_trim' && (
        <Choice
          label="Strength"
          value={strength}
          options={STRENGTHS.map((value) => ({ value, label: value }))}
          onChange={(value) => setStrength(value as 'light' | 'medium' | 'aggressive')}
        />
      )}

      <button
        type="button"
        disabled={disabled}
        onClick={() => void run(tool.name, clipId, { language, strength })}
        className="rounded px-2 py-1 text-left disabled:opacity-50"
        style={{
          background: 'var(--color-surface-2, rgba(255,255,255,0.06))',
          color: 'var(--color-ink)',
        }}
        data-testid={`run-${tool.name}`}
      >
        {running ? 'Running…' : blocked ? reasonFor(blocked) : `Run · ${quote?.credits ?? '…'} cr`}
      </button>
    </div>
  )
}

function RunRow({ jobId }: { jobId: string }) {
  const run = useTools((state) => state.runs[jobId])
  const cancel = useTools((state) => state.cancel)
  if (!run) return null

  return (
    <div className="flex flex-col gap-1" data-testid={`run-row-${run.tool}`}>
      <div className="flex items-baseline justify-between">
        <span style={{ color: 'var(--color-ink-2)' }}>{run.tool.replace('_', ' ')}</span>
        <button
          type="button"
          onClick={() => void cancel(jobId)}
          style={{ color: 'var(--color-ink-faint)' }}
        >
          Cancel
        </button>
      </div>
      {/* A bar that moves at real checkpoints, not on a timer — a bar advancing
          on a clock is a bar that lies about what is happening. */}
      <div
        className="h-1 overflow-hidden rounded"
        style={{ background: 'var(--color-rule)' }}
        role="progressbar"
        aria-valuenow={run.progress}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full transition-[width] duration-300"
          style={{ width: `${run.progress}%`, background: 'var(--color-accent-line)' }}
        />
      </div>
    </div>
  )
}

function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: string; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <label className="flex items-center justify-between gap-2">
      <span style={{ color: 'var(--color-ink-3)' }}>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
        className="rounded px-1 py-0.5"
        style={{ background: 'transparent', color: 'var(--color-ink-2)' }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function reasonFor(code: string): string {
  if (code === 'INSUFFICIENT_CREDITS') return 'Not enough credits'
  if (code === 'FAIR_USE_EXCEEDED') return 'Fair-use ceiling reached'
  if (code === 'PLAN_LIMIT_EXCEEDED') return 'Not on this plan'
  return 'Unavailable'
}

function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  return `${Math.round(seconds / 60)}min`
}

function isMedia(clip: AnyClip): clip is MediaClip {
  return 'assetId' in clip
}

/** Local state without pulling `useState` into every row's props. */
function useLocal<T>(initial: T): [T, (value: T) => void] {
  const [value, setValue] = useReactState<T>(initial)
  return [value, setValue]
}

import { useState as useReactState } from 'react'
