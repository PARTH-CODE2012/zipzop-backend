'use client'

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { parseCubeLut } from '@/spike/compositor/cube'
import { CompositorEngine, type CompositorStats } from '@/spike/compositor/engine'
import { LUT_URL, type TransitionMode } from '@/spike/compositor/timeline'

interface Preset {
  readonly label: string
  readonly width: number
  readonly height: number
}

const PRESETS: readonly Preset[] = [
  { label: '720p', width: 1280, height: 720 },
  { label: '1080p', width: 1920, height: 1080 },
  { label: '4K', width: 3840, height: 2160 },
  { label: '1080×1920 (9:16)', width: 1080, height: 1920 },
]

/** 1080p is the number the milestone is measured against. */
const DEFAULT_PRESET = PRESETS[1] as Preset

export function CompositorSpike() {
  const glRef = useRef<HTMLCanvasElement | null>(null)
  const textRef = useRef<HTMLCanvasElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const engineRef = useRef<CompositorEngine | null>(null)

  const [stats, setStats] = useState<CompositorStats | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [preset, setPreset] = useState<Preset>(DEFAULT_PRESET)
  const [mode, setMode] = useState<TransitionMode>('cut')
  const [strength, setStrength] = useState(1)
  const [loop, setLoop] = useState(true)
  const [muted, setMuted] = useState(true)
  const [forceRaf, setForceRaf] = useState(false)
  const [showVideos, setShowVideos] = useState(false)
  const [scrubMs, setScrubMs] = useState<number | null>(null)
  // A 1 MB LUT and two proxies have to arrive before the first frame can be
  // drawn. On a fast connection that is invisible; over a tunnel it is most of
  // a minute of black canvas, which looks exactly like the failure this page
  // exists to detect. Say what is happening instead.
  const [loading, setLoading] = useState<string | null>('Loading the colour table…')

  useEffect(() => {
    const glCanvas = glRef.current
    const textCanvas = textRef.current
    const videoHost = hostRef.current
    if (glCanvas === null || textCanvas === null || videoHost === null) return

    // Strict Mode mounts this twice in development. Everything below is torn
    // down completely by the cleanup — GL context, video elements, timers —
    // so the second mount starts from nothing rather than fighting the first.
    let cancelled = false
    let engine: CompositorEngine | null = null

    void (async () => {
      try {
        const response = await fetch(LUT_URL, { cache: 'no-store' })
        if (!response.ok) {
          throw new Error(
            `${LUT_URL} returned ${response.status}. The spike's media is generated, not committed — run \`make spike-media\`.`,
          )
        }
        const lut = parseCubeLut(await response.text())
        if (cancelled) return
        setLoading('Loading the test clips…')

        engine = new CompositorEngine({
          glCanvas,
          textCanvas,
          videoHost,
          lut,
          onStats: (next) => {
            if (cancelled) return
            setStats(next)
            // The first frame on screen is the only honest "ready" signal:
            // it means the LUT parsed, a clip decoded and the shader ran.
            if (next.primed.some((clip) => clip.primed)) setLoading(null)
          },
        })
        engineRef.current = engine
      } catch (error) {
        if (!cancelled) {
          setLoading(null)
          setBootError(error instanceof Error ? error.message : String(error))
        }
      }
    })()

    return () => {
      cancelled = true
      engine?.dispose()
      engineRef.current = null
    }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const engine = engineRef.current
      if (engine === null) return
      const target = event.target
      if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement) return

      const step = event.shiftKey ? 1000 : 100
      if (event.code === 'Space') {
        event.preventDefault()
        engine.togglePlay()
      } else if (event.code === 'ArrowLeft') {
        event.preventDefault()
        engine.seek(engine.playheadMs() - step)
      } else if (event.code === 'ArrowRight') {
        event.preventDefault()
        engine.seek(engine.playheadMs() + step)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const applyPreset = useCallback((next: Preset) => {
    setPreset(next)
    engineRef.current?.setResolution(next.width, next.height)
  }, [])

  const applyMode = useCallback((next: TransitionMode) => {
    setMode(next)
    engineRef.current?.setMode(next)
  }, [])

  const durationMs = stats?.durationMs ?? 0
  const positionMs = scrubMs ?? stats?.positionMs ?? 0
  const playing = stats?.playing ?? false
  const mediaError = stats?.mediaError ?? null

  return (
    <main className="no-select mx-auto flex min-h-screen max-w-[1180px] flex-col gap-5 p-6">
      <header>
        <p className="font-mono text-xs uppercase tracking-widest text-[var(--color-accent)]">
          M1 · compositor spike
        </p>
        <h1 className="mt-1 text-2xl font-semibold">Two clips, a cut, a grade and a caption</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-2)]">
          Throwaway code answering one question: can the browser do this at all. Video frames go
          into a WebGL2 texture, a 3D LUT grades them in the fragment shader, captions land on a
          2D canvas above, and the clock comes from the playing element&rsquo;s own{' '}
          <code className="font-mono">currentTime</code> — never{' '}
          <code className="font-mono">performance.now()</code>.
        </p>
      </header>

      {bootError !== null && <Banner tone="error">{bootError}</Banner>}
      {mediaError !== null && <Banner tone="error">{mediaError}</Banner>}
      {stats?.contextLost === true && (
        <Banner tone="warn">WebGL context lost — waiting for the browser to restore it.</Banner>
      )}

      <section className="flex justify-center rounded-lg bg-black p-3">
        <div
          className="relative"
          style={{
            aspectRatio: `${preset.width} / ${preset.height}`,
            width: `min(100%, calc(58vh * ${preset.width} / ${preset.height}))`,
          }}
        >
          <canvas ref={glRef} className="absolute inset-0 block h-full w-full" />
          <canvas
            ref={textRef}
            className="pointer-events-none absolute inset-0 block h-full w-full"
          />
          {loading !== null && bootError === null && (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm text-[var(--color-ink)]">{loading}</p>
              <p className="max-w-xs text-xs text-[var(--color-ink-2)]">
                About 1 MB of test media. Over a slow link this takes a few seconds — a black
                canvas here is the download, not a bug.
              </p>
            </div>
          )}
        </div>
      </section>

      <section className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => engineRef.current?.togglePlay()}
          className="h-9 w-24 rounded bg-[var(--color-accent)] font-semibold text-[#08191d] hover:brightness-110"
        >
          {playing ? 'Pause' : 'Play'}
        </button>

        <input
          type="range"
          min={0}
          max={Math.max(durationMs, 1)}
          step={10}
          value={Math.min(positionMs, Math.max(durationMs, 1))}
          aria-label="Playhead"
          onChange={(event) => {
            const next = Number(event.target.value)
            setScrubMs(next)
            engineRef.current?.seek(next)
          }}
          onPointerUp={() => setScrubMs(null)}
          onBlur={() => setScrubMs(null)}
          className="h-9 min-w-[280px] flex-1 accent-[var(--color-accent)]"
        />

        <span className="w-40 shrink-0 text-right font-mono text-sm tabular-nums text-[var(--color-ink-2)]">
          {formatMs(positionMs)} / {formatMs(durationMs)}
        </span>
      </section>

      <section className="grid gap-4 rounded-lg border border-[var(--color-rule)] bg-[var(--color-surface-2)] p-4 md:grid-cols-2">
        <Field label="Transition">
          <div className="flex gap-2">
            {(['cut', 'crossfade'] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => applyMode(option)}
                className={`h-8 rounded px-3 text-sm capitalize ${
                  mode === option
                    ? 'bg-[var(--color-accent)] font-semibold text-[#08191d]'
                    : 'border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)]'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </Field>

        <Field label="Preview resolution">
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((option) => (
              <button
                key={option.label}
                type="button"
                onClick={() => applyPreset(option)}
                className={`h-8 rounded px-3 text-sm ${
                  preset.label === option.label
                    ? 'bg-[var(--color-accent)] font-semibold text-[#08191d]'
                    : 'border border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)]'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

        <Field label={`Colour grade — strength ${strength.toFixed(2)}`}>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={strength}
            aria-label="LUT strength"
            onChange={(event) => {
              const next = Number(event.target.value)
              setStrength(next)
              engineRef.current?.setLutStrength(next)
            }}
            className="w-full accent-[var(--color-accent)]"
          />
        </Field>

        <Field label="Switches">
          <div className="flex flex-wrap gap-4">
            <Toggle
              label="Loop"
              checked={loop}
              onChange={(next) => {
                setLoop(next)
                engineRef.current?.setLoop(next)
              }}
            />
            <Toggle
              label="Muted"
              checked={muted}
              onChange={(next) => {
                setMuted(next)
                engineRef.current?.setMuted(next)
              }}
            />
            <Toggle
              label="Force rAF"
              checked={forceRaf}
              onChange={(next) => {
                setForceRaf(next)
                engineRef.current?.setForceRaf(next)
              }}
            />
            <Toggle label="Show elements" checked={showVideos} onChange={setShowVideos} />
          </div>
        </Field>

        <Field label="Robustness">
          <button
            type="button"
            onClick={() => {
              if (engineRef.current?.simulateContextLoss() === false) {
                setBootError('WEBGL_lose_context is not available in this browser.')
              }
            }}
            className="h-8 rounded border border-[var(--color-rule)] px-3 text-sm text-[var(--color-ink-2)] hover:text-[var(--color-ink)]"
          >
            Drop the WebGL context
          </button>
        </Field>
      </section>

      <section className="rounded-lg border border-[var(--color-rule)] bg-[var(--color-surface-2)] p-4">
        <h2 className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
          Measurements
        </h2>
        {stats === null ? (
          <p className="mt-3 text-sm text-[var(--color-ink-2)]">Waiting for the first frame…</p>
        ) : (
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 font-mono text-sm sm:grid-cols-3 lg:grid-cols-4">
            <Stat label="loop" value={`${stats.loopFps.toFixed(1)} fps`} />
            <Stat label="frame cost" value={`${stats.frameCostMs.toFixed(2)} ms`} />
            <Stat label="driver" value={stats.driver} />
            <Stat label="clock" value={stats.clock} />
            <Stat label="showing" value={stats.layers} />
            <Stat label="canvas" value={`${stats.canvasWidth}×${stats.canvasHeight}`} />
            <Stat
              label="frames dropped"
              value={`${stats.droppedFrames} / ${stats.decodedFrames}`}
            />
            <Stat label="overlay redraws" value={String(stats.textRedraws)} />
            <Stat label="draws skipped" value={String(stats.skippedDraws)} />
            <Stat
              label="primed"
              value={stats.primed.map((p) => `${p.label}${p.primed ? '✓' : '·'}`).join(' ')}
            />
            <Stat label="play errors" value={stats.playError ?? 'none'} />
            <Stat label="gpu" value={stats.gpu} wide />
          </dl>
        )}
        <p className="mt-4 text-xs leading-relaxed text-[var(--color-ink-2)]">
          The test clips are 30 fps, so the <code className="font-mono">rvfc</code> driver reports
          about 30 — one callback per decoded frame is correct, not a dropped frame. Tick{' '}
          <strong>Force rAF</strong> to drive the loop at display rate and read the compositor&rsquo;s
          own ceiling. <strong>Frame cost</strong> is CPU time for the texture upload, the draw call
          and the overlay; GPU work finishes after it returns.
        </p>
      </section>

      <section>
        <h2 className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
          Video elements
        </h2>
        <p className="mt-1 text-xs text-[var(--color-ink-2)]">
          Hidden but laid out, never <code className="font-mono">display:none</code> — a browser
          that thinks an element is not rendered may throttle its decode.
        </p>
        <div
          ref={hostRef}
          className={
            showVideos
              ? 'mt-2 flex h-40 gap-2 rounded border border-[var(--color-rule)] bg-black p-2'
              : 'mt-2 flex h-px w-px overflow-hidden opacity-[0.01]'
          }
        />
      </section>
    </main>
  )
}

function formatMs(ms: number): string {
  const safe = Number.isFinite(ms) ? Math.max(0, ms) : 0
  const total = Math.floor(safe / 1000)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  const millis = Math.floor(safe % 1000)
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  // A <div> rather than a <label>: most of these wrap a group of buttons, and
  // a label around a group activates whichever control it feels like.
  return (
    <div className="flex flex-col gap-2">
      <span className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
        {label}
      </span>
      {children}
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <span className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-[var(--color-accent)]"
      />
      {label}
    </span>
  )
}

function Stat({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={wide === true ? 'col-span-2 sm:col-span-3 lg:col-span-4' : undefined}>
      <dt className="text-xs uppercase tracking-wide text-[var(--color-ink-2)]">{label}</dt>
      <dd className="truncate tabular-nums">{value}</dd>
    </div>
  )
}

function Banner({ tone, children }: { tone: 'error' | 'warn'; children: ReactNode }) {
  const styles =
    tone === 'error'
      ? 'border-red-500/50 bg-red-500/10 text-red-200'
      : 'border-amber-500/50 bg-amber-500/10 text-amber-200'
  return <p className={`rounded border px-4 py-3 text-sm ${styles}`}>{children}</p>
}
