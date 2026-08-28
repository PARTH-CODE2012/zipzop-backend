'use client'

/**
 * What each rail mode shows.
 *
 * M4.5 items 3 and 4 land together here, because they turned out to be the same
 * change: the toolbar was thin **and** the panels did not scale, and giving the
 * left panel modes is what created somewhere for the missing manual controls to
 * live. Putting a colour picker in the toolbar would have made the toolbar the
 * thing that does not scale.
 *
 * What is deliberately **not** here: any effect the export renderer does not
 * already implement. That was decided on 22 August and it is not a design
 * preference — an effect the browser can draw and FFmpeg cannot is a preview
 * that disagrees with the exported file, which is the one failure this project
 * has been most careful to avoid. Everything below drives machinery that
 * already shipped: the five `.cube` looks M4 generated, and the volume and fade
 * fields the inspector already wrote.
 */

import { useEffect, useState } from 'react'

import { NumberField } from '@/editor/controls/NumberField'
import { IconSparkles, IconTypography, IconVolume } from '@/editor/icons'
import { LUT_NAMES } from '@/editor/playback/lut-catalogue'
import { needsSelection, type Mode, type ModeId } from '@/editor/rail/modes'
import { selectSelectedAnyClip, useEditor } from '@/editor/state/store'
import type { AnyClip, MediaClip } from '@/editor/state/timeline-document'
import { estimateKey, isRunning, useTools, type ToolName } from '@/editor/tools/jobs-store'
import { MediaBin } from '@/media/MediaBin'

// --------------------------------------------------------------------------
// The router
// --------------------------------------------------------------------------

export function ModePanel({ mode }: { mode: Mode }) {
  const clip = useEditor(selectSelectedAnyClip)
  const media = clip && isMedia(clip) ? clip : null

  // Media has its own scroll container and heading; everything else gets the
  // standard shell so the panels cannot drift apart visually.
  if (mode.id === 'media') {
    return (
      <div className="flex h-full min-h-0 flex-col" data-testid="panel-media">
        <MediaBin />
      </div>
    )
  }

  return (
    <PanelShell mode={mode}>
      {needsSelection(mode.id) && !media ? (
        <Empty modeId={mode.id} hasClip={Boolean(clip)} />
      ) : mode.id === 'titles' ? (
        <TitlesPanel />
      ) : mode.id === 'audio' && media ? (
        <AudioPanel clip={media} />
      ) : mode.id === 'colour' && media ? (
        <ColourPanel clip={media} />
      ) : media ? (
        <ToolPanel mode={mode} clipId={media.id} />
      ) : null}
    </PanelShell>
  )
}

function PanelShell({ mode, children }: { mode: Mode; children: React.ReactNode }) {
  return (
    <div
      className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto px-4 py-3 text-xs"
      style={{ color: 'var(--color-ink-2)' }}
      data-testid={`panel-${mode.id}`}
    >
      <div className="flex items-baseline gap-2">
        <h2 style={{ color: 'var(--color-ink)', fontWeight: 600 }}>{mode.label}</h2>
        {mode.costsCredits && (
          <span
            className="flex items-center gap-1"
            style={{ color: 'var(--color-accent)' }}
            title="This tool spends credits"
          >
            <IconSparkles size={11} aria-hidden="true" />
            <span className="text-[10px] uppercase tracking-wide">credits</span>
          </span>
        )}
      </div>
      <p style={{ color: 'var(--color-ink-3)' }}>{mode.hint}</p>
      {children}
    </div>
  )
}

/**
 * The empty state.
 *
 * A sentence rather than a disabled panel, and the mode is **never hidden** — a
 * mode that disappears when nothing is selected is a mode nobody finds, which is
 * the discoverability failure this whole document is about.
 */
function Empty({ modeId, hasClip }: { modeId: ModeId; hasClip: boolean }) {
  return (
    <p style={{ color: 'var(--color-ink-3)' }} data-testid={`empty-${modeId}`}>
      {hasClip
        ? 'This works on a video or audio clip — the selected clip is text.'
        : 'Select a clip on the timeline first.'}
    </p>
  )
}

// --------------------------------------------------------------------------
// Titles
// --------------------------------------------------------------------------

function TitlesPanel() {
  const store = useEditor.getState
  return (
    <>
      <button
        type="button"
        onClick={() => store().addTitle('New title')}
        className="flex items-center justify-center gap-1.5 px-3 py-2"
        style={{
          borderRadius: 'var(--radius-sm)',
          background: 'var(--color-accent)',
          color: 'var(--color-accent-ink)',
          fontWeight: 600,
        }}
        data-testid="add-title"
      >
        <IconTypography size={14} aria-hidden="true" />
        Add a title
      </button>
      <p style={{ color: 'var(--color-ink-faint)' }}>
        A title lands on the text track at the playhead. Select it to edit its words in the
        inspector, and drag its edges on the timeline to retime it.
      </p>
    </>
  )
}

// --------------------------------------------------------------------------
// Audio — moved out of the inspector, M4.5 items 3 and 5
// --------------------------------------------------------------------------

/**
 * *"volume and fades — already in the inspector, arguably in the wrong place"*.
 *
 * They are in the wrong place because the inspector answers *what is this clip*
 * and these three answer *how does it sound* — and because four full-width
 * sliders were most of what filled a 176 px panel. The fields are the compact
 * ones now, and the two fades finally say what they are.
 */
function AudioPanel({ clip }: { clip: MediaClip }) {
  const store = useEditor.getState
  const maxFade = Math.min(5_000, clip.durationMs)

  return (
    <>
      <NumberField
        icon={<IconVolume size={13} />}
        label="Volume"
        value={clip.volume}
        min={0}
        max={2}
        step={0.05}
        scale={100}
        suffix="%"
        format={(v) => `${Math.round(v)}`}
        onChange={(volume) => store().setClipProperties(clip.id, { volume })}
        data-testid="field-volume"
      />
      <NumberField
        label="Fade in"
        // The item's smallest problem and *"the one most likely to be silently
        // confusing a user right now"*: these are the audio ramp, in a product
        // whose transitions are also called fade.
        hint="Audio only — the volume ramps up over this long at the start of the clip. Not the same as a video fade transition."
        value={clip.audioFadeInMs}
        min={0}
        max={maxFade}
        step={50}
        suffix="ms"
        format={(v) => `${Math.round(v)}`}
        onChange={(audioFadeInMs) => store().setClipProperties(clip.id, { audioFadeInMs })}
        data-testid="field-fade-in"
      />
      <NumberField
        label="Fade out"
        hint="Audio only — the volume ramps down over this long at the end of the clip. Not the same as a video fade transition."
        value={clip.audioFadeOutMs}
        min={0}
        max={maxFade}
        step={50}
        suffix="ms"
        format={(v) => `${Math.round(v)}`}
        onChange={(audioFadeOutMs) => store().setClipProperties(clip.id, { audioFadeOutMs })}
        data-testid="field-fade-out"
      />
      <NumberField
        label="Speed"
        hint="Playback rate. The clip's length on the timeline changes with it."
        value={clip.speed}
        min={0.25}
        max={4}
        step={0.05}
        format={(v) => `${v.toFixed(2)}x`}
        onChange={(speed) => store().setClipProperties(clip.id, { speed })}
        data-testid="field-speed"
      />
    </>
  )
}

// --------------------------------------------------------------------------
// Colour — M4.5 item 3, the manual half
// --------------------------------------------------------------------------

/** `cinematic_warm` reads as a filename; a person picking a look wants words. */
function lutLabel(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1).replace(/_/g, ' ')
}

/**
 * Grade by hand, from the five looks that already ship.
 *
 * Before this, *"a user who wants to warm an image slightly has to run a colour
 * analysis job and accept what it recommends"* — a credit-spending job to do
 * something the browser can already do for free, because the picker was never
 * built. The machinery underneath is untouched: the same `.cube` files, the same
 * `applyColorGrade`, the same one-effect-per-clip rule.
 *
 * The analysis job is still here, at the bottom, because *suggest me one* is a
 * real question and the answer lands in exactly this control.
 */
function ColourPanel({ clip }: { clip: MediaClip }) {
  const store = useEditor.getState
  const grade = clip.effects.find((effect) => effect.type === 'color_grade') ?? null

  return (
    <>
      <div className="flex flex-wrap gap-1.5" data-testid="lut-picker">
        <Swatch
          active={grade === null}
          onClick={() => store().clearColorGrade(clip.id)}
          testId="lut-none"
        >
          None
        </Swatch>
        {LUT_NAMES.map((name) => (
          <Swatch
            key={name}
            active={grade?.lut === name}
            onClick={() =>
              store().applyColorGrade(clip.id, {
                lut: name,
                // Keep the strength when swapping looks — someone comparing two
                // grades at 40 % wants to compare them at 40 %, not to have the
                // second one jump back to full.
                strength: grade?.strength ?? 0.75,
                sourceJobId: null,
              })
            }
            testId={`lut-${name}`}
          >
            {lutLabel(name)}
          </Swatch>
        ))}
      </div>

      <NumberField
        label="Strength"
        hint="How much of the look is applied. 0 is the original picture."
        value={grade?.strength ?? 0}
        min={0}
        max={1}
        step={0.01}
        scale={100}
        suffix="%"
        disabled={grade === null}
        format={(v) => `${Math.round(v)}`}
        onChange={(strength) =>
          grade &&
          store().applyColorGrade(clip.id, {
            lut: grade.lut,
            strength,
            sourceJobId: grade.sourceJobId ?? null,
          })
        }
        data-testid="field-strength"
      />

      {grade?.sourceJobId && (
        <p style={{ color: 'var(--color-ink-faint)' }}>Suggested by Colour analysis</p>
      )}

      <div className="mt-1 border-t pt-3" style={{ borderColor: 'var(--color-rule)' }}>
        <ToolRow
          tool="color_analysis"
          label="Suggest a look"
          blurb="Reads the lighting and colour of the picture and picks one of the five above."
          clipId={clip.id}
        />
      </div>
    </>
  )
}

function Swatch({
  active,
  onClick,
  testId,
  children,
}: {
  active: boolean
  onClick: () => void
  testId: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="px-2 py-1"
      style={{
        borderRadius: 'var(--radius-sm)',
        background: active ? 'var(--color-accent-soft)' : 'var(--color-surface-3)',
        color: active ? 'var(--color-accent)' : 'var(--color-ink-2)',
        fontWeight: active ? 600 : 400,
        transition: 'background var(--duration-micro) ease-out',
      }}
      data-testid={testId}
    >
      {children}
    </button>
  )
}

// --------------------------------------------------------------------------
// Captions and Smart trim — the same rows, in the rail instead of a third panel
// --------------------------------------------------------------------------

const LANGUAGES = [
  { value: 'auto', label: 'Detect' },
  { value: 'en', label: 'English' },
  { value: 'fr', label: 'Français' },
  { value: 'hi', label: 'हिन्दी' },
]

const STRENGTHS = ['light', 'medium', 'aggressive'] as const

const TOOL_FOR_MODE: Partial<Record<ModeId, { tool: ToolName; label: string; blurb: string }>> = {
  captions: {
    tool: 'captions',
    label: 'Run captions',
    blurb: 'One clip per word, timed to the speech, every word editable afterwards.',
  },
  trim: {
    tool: 'smart_trim',
    label: 'Find cuts',
    blurb: 'Splits and removals land as one undoable edit, with the gaps closed behind them.',
  },
}

function ToolPanel({ mode, clipId }: { mode: Mode; clipId: string }) {
  const entry = TOOL_FOR_MODE[mode.id]
  if (!entry) return null
  return <ToolRow tool={entry.tool} label={entry.label} blurb={entry.blurb} clipId={clipId} />
}

/**
 * One tool, priced before it is pressed.
 *
 * Unchanged in behaviour from the panel this came out of, and the three rules
 * that made it are unchanged with it: the price is on the button before the
 * click (`POST /jobs/estimate` runs the same function the charge does), editing
 * carries on while it runs, and the result is an ordinary undoable edit.
 */
function ToolRow({
  tool,
  label,
  blurb,
  clipId,
}: {
  tool: ToolName
  label: string
  blurb: string
  clipId: string
}) {
  const key = estimateKey(tool, clipId)
  const quote = useTools((state) => state.estimates[key])
  const busy = useTools((state) => state.busy[key] ?? false)
  const run = useTools((state) => state.run)
  const estimate = useTools((state) => state.estimate)
  const lastError = useTools((state) => state.lastError)
  const running = useTools((state) =>
    Object.values(state.runs).some(
      (each) => each.clipId === clipId && each.tool === tool && isRunning(each),
    ),
  )
  const active = useTools((state) =>
    Object.values(state.runs).find(
      (each) => each.clipId === clipId && each.tool === tool && isRunning(each),
    ),
  )

  const [language, setLanguage] = useState('auto')
  const [strength, setStrength] = useState<'light' | 'medium' | 'aggressive'>('medium')

  // Price it the moment the panel opens, so the number is already there when
  // the user looks at the button rather than appearing under their cursor.
  useEffect(() => {
    void estimate(tool, clipId)
  }, [tool, clipId, estimate])

  const blocked = quote?.blockedBy ?? null
  const disabled = busy || running || blocked !== null

  return (
    <div className="flex flex-col gap-2">
      <p style={{ color: 'var(--color-ink-3)' }}>{blurb}</p>

      {tool === 'captions' && (
        <Choice label="Language" value={language} options={LANGUAGES} onChange={setLanguage} />
      )}
      {tool === 'smart_trim' && (
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
        onClick={() => void run(tool, clipId, { language, strength })}
        className="flex items-center justify-between gap-2 px-2.5 py-1.5 disabled:opacity-50"
        style={{
          borderRadius: 'var(--radius-sm)',
          background: 'var(--color-accent-soft)',
          color: 'var(--color-accent)',
          fontWeight: 600,
        }}
        data-testid={`run-${tool}`}
      >
        <span>{running ? 'Running…' : blocked ? reasonFor(blocked) : label}</span>
        {quote && !blocked && (
          <span className="tnum" style={{ fontWeight: 400 }}>
            {quote.credits} cr · ~{formatSeconds(quote.estimatedSeconds)}
          </span>
        )}
      </button>

      {active && <Progress percent={active.progress} jobId={active.jobId} />}

      {lastError && (
        <p role="alert" style={{ color: 'var(--color-danger, #f87171)' }}>
          {lastError}
        </p>
      )}
    </div>
  )
}

function Progress({ percent, jobId }: { percent: number; jobId: string }) {
  const cancel = useTools((state) => state.cancel)
  return (
    <div className="flex flex-col gap-1" data-testid="tool-progress">
      <div className="flex items-baseline justify-between">
        <span className="tnum" style={{ color: 'var(--color-ink-3)' }}>
          {percent}%
        </span>
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
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full transition-[width] duration-300"
          style={{ width: `${percent}%`, background: 'var(--color-accent-line)' }}
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
        className="px-1 py-0.5"
        style={{
          background: 'var(--color-surface-3)',
          border: '1px solid var(--color-rule)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--color-ink-2)',
        }}
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
