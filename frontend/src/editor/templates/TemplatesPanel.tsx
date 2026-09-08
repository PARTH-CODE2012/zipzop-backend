'use client'

/**
 * The templates panel.
 *
 * Save this project's look under a name, and put it on another one. That is the
 * whole feature — the narrow reading decided 25 August
 * (docs/13-mvp-direction.md §4), and deliberately not a supplied library.
 *
 * **Applying is one `commit`**, so ⌘Z takes all of it back at once. Fifteen
 * separate edits that each land in the history would make "actually, no"
 * fifteen presses, which is how a bulk action becomes something people stop
 * using.
 */

import { useCallback, useEffect, useState } from 'react'

import { useEditor } from '@/editor/state/store'
import {
  applyTemplate,
  extractTemplate,
  isEmpty,
  type TemplateSettings,
} from '@/editor/templates/template'
import { ApiError } from '@/lib/api/client'
import {
  deleteTemplate,
  listTemplates,
  saveTemplate,
  type TemplateResponse,
} from '@/lib/api/endpoints'

export function TemplatesPanel() {
  const commit = useEditor((state) => state.commit)
  const timeline = useEditor((state) => state.timeline)
  const [items, setItems] = useState<TemplateResponse[] | null>(null)
  const [name, setName] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setItems((await listTemplates()).items)
    } catch {
      // A list that will not load is not worth an error banner in a panel the
      // user opened to do something else. The save box still works.
      setItems([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const current = extractTemplate(timeline)
  const nothingToSave = isEmpty(current)

  async function save() {
    const trimmed = name.trim()
    if (!trimmed || nothingToSave) return
    setBusy(true)
    setNote(null)
    try {
      await saveTemplate({ name: trimmed, settings: current as unknown as Record<string, never> })
      setName('')
      setNote(`Saved as ${trimmed}.`)
      await load()
    } catch (cause) {
      setNote(cause instanceof ApiError ? cause.message : 'Could not save that.')
    } finally {
      setBusy(false)
    }
  }

  function apply(template: TemplateResponse) {
    const settings = template.settings as unknown as TemplateSettings
    // One commit. Undo takes the whole template back off in a single step,
    // like every other bulk operation in the editor.
    const changed = commit(`Apply ${template.name}`, (draft) => applyTemplate(draft, settings))
    setNote(changed ? `Applied ${template.name}.` : 'That template changed nothing here.')
  }

  async function remove(template: TemplateResponse) {
    try {
      await deleteTemplate(template.id)
      await load()
    } catch {
      setNote('Could not delete that.')
    }
  }

  return (
    <div className="flex flex-col gap-4" data-testid="panel-templates">
      <section className="flex flex-col gap-2">
        <p style={{ color: 'var(--color-ink-2)' }}>
          Save the caption style, colour look and transition length you are using here, then
          put them on another project in one step.
        </p>

        <div className="flex gap-2">
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name this look"
            className="min-w-0 flex-1 rounded border px-2 py-1.5"
            style={{ borderColor: 'var(--color-rule)', background: 'var(--color-surface-3)' }}
            data-testid="template-name"
            maxLength={80}
          />
          <button
            type="button"
            onClick={() => void save()}
            disabled={busy || !name.trim() || nothingToSave}
            className="px-3 py-1.5 disabled:opacity-40"
            style={{ border: '1px solid var(--color-rule)' }}
            data-testid="template-save"
          >
            Save
          </button>
        </div>

        {nothingToSave && (
          // A template of nothing appears in the list, can be applied, and does
          // nothing — which reads as broken. Better to say why the button is off.
          <p style={{ color: 'var(--color-ink-3)' }}>
            Style a title, grade a clip or set a transition first — there is nothing to save
            from this project yet.
          </p>
        )}
      </section>

      {items && items.length > 0 && (
        <section className="flex flex-col gap-1">
          <h3 style={{ color: 'var(--color-ink-2)' }}>Saved</h3>
          <ul className="flex flex-col" data-testid="template-list">
            {items.map((template) => (
              <li
                key={template.id}
                className="flex items-center gap-2 py-1.5"
                style={{ borderBottom: '1px solid var(--color-rule)' }}
              >
                <button
                  type="button"
                  onClick={() => apply(template)}
                  className="min-w-0 flex-1 truncate text-left"
                  data-testid={`template-apply-${template.name}`}
                >
                  {template.name}
                </button>
                <button
                  type="button"
                  onClick={() => void remove(template)}
                  aria-label={`Delete ${template.name}`}
                  className="px-2"
                  style={{ color: 'var(--color-ink-3)' }}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {note && (
        <p role="status" style={{ color: 'var(--color-ink-3)' }} data-testid="template-note">
          {note}
        </p>
      )}
    </div>
  )
}
