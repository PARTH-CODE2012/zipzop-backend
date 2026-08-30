'use client'

/**
 * The export dialog — contract §6.2.
 *
 * **A header action, not a rail mode.** The rail is one mode at a time and each
 * mode acts on a selection; an export acts on the whole project, so putting it
 * there would mean a seventh icon that behaves unlike the other six
 * (`docs/15-m5-readiness.md` §5).
 *
 * Three things it has to get right, and each is a way this goes wrong:
 *
 * **The price is on the button before the click.** `POST /jobs/estimate` is the
 * same function `POST /jobs` prices with — contract §6.1 calls it *"exact, not
 * indicative"* — so what is shown is what is charged. A price that appears
 * after the click is the bug users report as theft.
 *
 * **`blockedBy` is shown as a reason, not as a disabled button.** A greyed-out
 * 4K option with no explanation is indistinguishable from a broken one; the
 * server already says *which* plan is needed, so the dialog says it too.
 *
 * **The timeline version goes with the request.** If the document has moved
 * since the dialog opened — an autosave landing mid-thought — the server
 * answers `409` rather than rendering something the user is no longer looking
 * at. The dialog re-reads the version at submit rather than at open, so the
 * ordinary case never trips it, and the conflict that remains is a real one.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createJob,
  estimateJob,
  type EstimateResponse,
  type JobResponse,
} from '@/lib/api/endpoints'
import { useEditor } from '@/editor/state/store'

export const RESOLUTIONS = [
  { value: '720p', label: '720p', height: 720 },
  { value: '1080p', label: '1080p', height: 1080 },
  { value: '2160p', label: '4K', height: 2160 },
] as const

export const ASPECTS = [
  { value: '9:16', label: 'Vertical' },
  { value: '1:1', label: 'Square' },
  { value: '16:9', label: 'Wide' },
] as const

export type Resolution = (typeof RESOLUTIONS)[number]['value']
export type Aspect = (typeof ASPECTS)[number]['value']

export interface ExportDialogProps {
  open: boolean
  onClose: () => void
  /** Called with the created job so the caller can follow its progress. */
  onStarted?: (job: JobResponse) => void
}

/** The sentence for a `blockedBy` code, or `null` when nothing is blocking. */
export function blockedReason(estimate: EstimateResponse | null): string | null {
  if (!estimate || !estimate.blockedBy) return null
  switch (estimate.blockedBy) {
    case 'PLAN_LIMIT_EXCEEDED':
      return 'This resolution is not included in your plan.'
    case 'INSUFFICIENT_CREDITS':
      return 'Not enough credits for this export.'
    case 'FAIR_USE_EXCEEDED':
      return 'This account has passed its monthly fair-use ceiling.'
    default:
      // A code we do not have a sentence for is still shown rather than
      // swallowed: an unexplained disabled button is worse than a bare code.
      return estimate.blockedBy
  }
}

export function ExportDialog({ open, onClose, onStarted }: ExportDialogProps) {
  const projectId = useEditor((state) => state.projectId)
  const [resolution, setResolution] = useState<Resolution>('1080p')
  const [aspect, setAspect] = useState<Aspect>('9:16')
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const request = useCallback(
    (version: number) => ({
      tool: 'export' as const,
      projectId,
      input: {
        timelineVersion: version,
        preset: { resolution, aspectRatio: aspect, quality: 'high' as const, format: 'mp4' as const },
      },
    }),
    [projectId, resolution, aspect],
  )

  // Re-priced whenever the preset changes, because the ceiling depends on it:
  // 1080p may be affordable and allowed where 4K is neither.
  useEffect(() => {
    if (!open || !projectId) return
    let cancelled = false
    setEstimate(null)
    const version = useEditor.getState().version
    estimateJob(request(version) as never)
      .then((result) => {
        if (!cancelled) setEstimate(result)
      })
      .catch(() => {
        if (!cancelled) setEstimate(null)
      })
    return () => {
      cancelled = true
    }
  }, [open, projectId, request])

  const blocked = useMemo(() => blockedReason(estimate), [estimate])

  async function submit() {
    if (!projectId) return
    setBusy(true)
    setError(null)
    try {
      // The version is read *now*, not when the dialog opened: an autosave
      // between the two is an ordinary thing that should not become a 409.
      const version = useEditor.getState().version
      const job = await createJob(request(version) as never, crypto.randomUUID())
      onStarted?.(job)
      onClose()
    } catch (cause) {
      const code = (cause as { code?: string }).code
      setError(
        code === 'VERSION_CONFLICT'
          ? 'This project changed while the dialog was open. Close it and try again.'
          : ((cause as Error).message ?? 'The export could not be started.'),
      )
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      data-testid="export-dialog"
    >
      <div
        className="flex w-80 flex-col gap-4 p-5"
        style={{
          background: 'var(--color-surface-2)',
          border: '1px solid var(--color-rule)',
          borderRadius: 'var(--radius-lg)',
        }}
      >
        <h2 className="text-sm font-semibold">Export</h2>

        <Choice
          label="Resolution"
          options={RESOLUTIONS}
          value={resolution}
          onChange={(next) => setResolution(next as Resolution)}
          testId="export-resolution"
        />
        <Choice
          label="Shape"
          options={ASPECTS}
          value={aspect}
          onChange={(next) => setAspect(next as Aspect)}
          testId="export-aspect"
        />

        {blocked && (
          <p className="text-xs" style={{ color: 'var(--color-warning)' }} data-testid="export-blocked">
            {blocked}
          </p>
        )}
        {error && (
          <p className="text-xs" style={{ color: 'var(--color-danger)' }} data-testid="export-error">
            {error}
          </p>
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-xs"
            style={{ border: '1px solid var(--color-rule)', borderRadius: 'var(--radius-sm)' }}
            data-testid="export-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy || !!blocked || !estimate}
            className="ml-auto px-3 py-1.5 text-xs font-semibold"
            style={{
              background: blocked || !estimate ? 'var(--color-surface-3)' : 'var(--color-accent)',
              color: blocked || !estimate ? 'var(--color-ink-3)' : 'var(--color-accent-ink)',
              borderRadius: 'var(--radius-sm)',
            }}
            data-testid="export-start"
          >
            {/* The price on the button itself, which is the whole point of
                pricing before the click. */}
            {busy ? 'Starting…' : estimate ? `Export · ${estimate.credits} credits` : 'Pricing…'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Choice({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string
  options: readonly { value: string; label: string }[]
  value: string
  onChange: (next: string) => void
  testId: string
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs" style={{ color: 'var(--color-ink-2)' }}>
        {label}
      </span>
      <div className="flex gap-1.5" data-testid={testId}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            aria-pressed={value === option.value}
            className="flex-1 px-2 py-1.5 text-xs"
            style={{
              // Charter rule 3: never hue alone. The active choice changes its
              // fill *and* its edge *and* its weight.
              background: value === option.value ? 'var(--color-accent-soft)' : 'transparent',
              border: `1px solid ${value === option.value ? 'var(--color-accent-line)' : 'var(--color-rule)'}`,
              color: value === option.value ? 'var(--color-ink)' : 'var(--color-ink-2)',
              fontWeight: value === option.value ? 600 : 400,
              borderRadius: 'var(--radius-sm)',
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}
