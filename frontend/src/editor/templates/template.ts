/**
 * Templates: the user's own settings, saved and reapplied.
 *
 * The narrow reading of "reuse", decided 25 August
 * (docs/13-mvp-direction.md §4) — caption style, colour grade, transition
 * defaults, title styling. **Not** a supplied library: that reading carries a
 * licensed music catalogue and the naming-templates-after-real-people exposure,
 * neither of which has an owner.
 *
 * Two properties this file exists to keep:
 *
 * * **a template is a subset of the timeline document**, so extracting one is
 *   reading and applying one is an ordinary edit. No worker, no queue, no
 *   credits, no new job type.
 * * **applying is a single commit**, so it undoes in one step like every other
 *   bulk operation. Fifteen separate edits that each land in the history would
 *   make "actually, no" fifteen presses of ⌘Z.
 *
 * Pure, and therefore tested without a DOM or a store.
 */

import type { Draft } from 'immer'

import * as ops from '@/editor/state/operations'
import type { MediaClip, TextClip, TimelineDocument } from '@/editor/state/timeline-document'

/**
 * The version of the shape, stored with the settings.
 *
 * The server keeps `settings` as opaque JSON precisely so the editor can change
 * this without a migration — and an old template read by a newer editor has to
 * be recognisable as old rather than merely wrong.
 */
export const TEMPLATE_VERSION = 1

export interface TemplateSettings {
  version: number
  /** Caption and title styling — colour, size, stroke. */
  textStyle?: {
    color?: string
    fontSize?: number
    strokeColor?: string
    strokeWidth?: number
  }
  /** The look, and how much of it. */
  grade?: { lut: string; strength: number }
  /** What a join between two clips does by default. */
  transition?: { type: string; durationMs: number }
}

/**
 * Read the settings out of a project.
 *
 * The **most common** value wins rather than the first: a project where four
 * titles are white and one was turned red by accident should save white, and
 * "the first clip" is an arbitrary pick that would save whichever happens to
 * sit at zero.
 */
export function extractTemplate(document: TimelineDocument): TemplateSettings {
  const settings: TemplateSettings = { version: TEMPLATE_VERSION }

  const textStyle = commonest(
    textClips(document).map((clip) => clip.style),
    (style) => JSON.stringify(style),
  )
  if (textStyle) {
    settings.textStyle = {
      ...(textStyle.color != null ? { color: textStyle.color } : {}),
      ...(textStyle.fontSize != null ? { fontSize: textStyle.fontSize } : {}),
      ...(textStyle.strokeColor != null ? { strokeColor: textStyle.strokeColor } : {}),
      ...(textStyle.strokeWidth != null ? { strokeWidth: textStyle.strokeWidth } : {}),
    }
  }

  const grade = commonest(
    mediaClips(document)
      .map((clip) => clip.effects.find((effect) => effect.type === 'color_grade'))
      .filter((effect): effect is NonNullable<typeof effect> => effect != null),
    (effect) => `${effect.lut}:${effect.strength}`,
  )
  if (grade) settings.grade = { lut: grade.lut, strength: grade.strength }

  const transition = commonest(
    mediaClips(document)
      .map((clip) => clip.transitionOut)
      .filter((value): value is NonNullable<typeof value> => value != null),
    (value) => `${value.type}:${value.durationMs}`,
  )
  if (transition) {
    settings.transition = { type: transition.type, durationMs: transition.durationMs }
  }

  return settings
}

/**
 * Whether saving would record anything at all.
 *
 * A template of nothing is worse than no template: it appears in the list, it
 * can be applied, and applying it does nothing — which reads as broken.
 */
export function isEmpty(settings: TemplateSettings): boolean {
  return !settings.textStyle && !settings.grade && !settings.transition
}

/**
 * Apply the settings to a document, in place.
 *
 * Written as a recipe over an immer draft so the caller can pass it straight to
 * `commit()` — **one entry in the history, whatever it touched.**
 *
 * Everything goes through `operations.ts` rather than assigning fields
 * directly. `setTransition` clamps to half the shorter neighbouring clip
 * (timeline invariant 7), and a template carrying a 2-second dissolve applied
 * to a 1-second clip must be clamped rather than saved and rejected by the
 * server on the next autosave.
 */
export function applyTemplate(
  document: Draft<TimelineDocument>,
  settings: TemplateSettings,
): void {
  if (settings.textStyle) {
    for (const clip of textClips(document as unknown as TimelineDocument)) {
      const draft = clip as Draft<TextClip>
      draft.style = { ...draft.style, ...settings.textStyle }
    }
  }

  if (settings.grade) {
    const grade = settings.grade
    for (const clip of mediaClips(document as unknown as TimelineDocument)) {
      ops.applyColorGrade(document, clip.id, { lut: grade.lut, strength: grade.strength })
    }
  }

  if (settings.transition) {
    const transition = settings.transition
    const clips = mediaClips(document as unknown as TimelineDocument)
    clips.forEach((clip, index) => {
      // The last clip has nothing to dissolve into, and a transition out of the
      // end of the timeline is a fade to black nobody asked for.
      if (index === clips.length - 1) return
      ops.setTransition(document, clip.id, 'out', {
        type: transition.type,
        durationMs: transition.durationMs,
      } as never)
    })
  }
}

/* ---------------------------------------------------------------- helpers */

function mediaClips(document: TimelineDocument): MediaClip[] {
  return document.tracks.flatMap((track) =>
    track.kind === 'text' ? [] : ((track.clips ?? []) as MediaClip[]),
  )
}

function textClips(document: TimelineDocument): TextClip[] {
  return document.tracks.flatMap((track) =>
    track.kind === 'text' ? ((track.clips ?? []) as TextClip[]) : [],
  )
}

/** The value that appears most often, or `null` when there are none. */
function commonest<T>(values: readonly T[], key: (value: T) => string): T | null {
  if (values.length === 0) return null
  const counts = new Map<string, { value: T; count: number }>()
  for (const value of values) {
    const id = key(value)
    const seen = counts.get(id)
    if (seen) seen.count += 1
    else counts.set(id, { value, count: 1 })
  }
  let best: { value: T; count: number } | null = null
  for (const entry of counts.values()) {
    if (!best || entry.count > best.count) best = entry
  }
  return best?.value ?? null
}
