'use client'

/**
 * The icon rail down the left edge.
 *
 * M4.5 item 4. The structural claim is worth restating because it is the whole
 * reason for the change: **the rail grows by one icon per tool, and the
 * inspector's job never changes shape.** The panels it replaced grew by one
 * stacked block per tool, into a right-hand column that was already full with
 * three and gets four more in phase 2.
 *
 * Charter §10 for the marks, §14 for the accessible names — every control here
 * is icon-plus-label, and the label is real text rather than a `title`, because
 * a rail of unlabelled glyphs is a rail nobody reads twice.
 */

import {
  IconMessage,
  IconLibrary,
  IconPalette,
  IconSliders,
  IconTypography,
  IconWand,
} from '@/editor/icons'
import { MODES, type ModeId } from '@/editor/rail/modes'
import { useRail } from '@/editor/rail/rail-store'

const GLYPHS: Record<ModeId, React.ReactNode> = {
  media: <IconLibrary size={17} />,
  titles: <IconTypography size={17} />,
  audio: <IconSliders size={17} />,
  colour: <IconPalette size={17} />,
  captions: <IconMessage size={17} />,
  trim: <IconWand size={17} />,
}

export function ModeRail() {
  const mode = useRail((state) => state.mode)
  const setMode = useRail((state) => state.setMode)

  return (
    <nav
      className="flex w-16 shrink-0 flex-col items-center gap-1 border-r py-2"
      style={{ borderColor: 'var(--color-rule)', background: 'var(--color-surface-2)' }}
      aria-label="Editor modes"
      data-testid="mode-rail"
      data-mode={mode}
    >
      {MODES.map((each) => {
        const active = each.id === mode
        return (
          <button
            key={each.id}
            type="button"
            onClick={() => setMode(each.id)}
            title={each.hint}
            aria-label={`${each.label} — ${each.hint}`}
            aria-current={active ? 'true' : undefined}
            className="flex w-14 flex-col items-center gap-1 px-1 py-2"
            style={{
              borderRadius: 'var(--radius-sm)',
              // Charter §8 and rule 3: the active mode is a tint *and* a weight
              // change *and* `aria-current`, never a hue on its own.
              background: active ? 'var(--color-accent-soft)' : 'transparent',
              color: active
                ? 'var(--color-accent)'
                : each.costsCredits
                  ? 'var(--color-ink-3)'
                  : 'var(--color-ink-2)',
              fontWeight: active ? 600 : 400,
              transition: 'background var(--duration-micro) ease-out',
            }}
            data-testid={`mode-${each.id}`}
            data-active={active}
          >
            <span aria-hidden="true">{GLYPHS[each.id]}</span>
            <span className="text-[10px] leading-none">{each.label}</span>
            {/* Charter rule 5 — a user never learns what a button costs by
                pressing it. The dot is the same signal the tools panel carried
                before the move; it belongs to the tool, not to where it sat. */}
            {each.costsCredits && (
              <span
                aria-hidden="true"
                title="Spends credits"
                style={{
                  width: 4,
                  height: 4,
                  borderRadius: 999,
                  background: 'var(--color-accent)',
                }}
              />
            )}
          </button>
        )
      })}
    </nav>
  )
}
