'use client'

/**
 * Editor shell.
 *
 * The layout below is the skeleton the real thing grows into:
 *
 *   ┌──────────────────────────────────────────┐
 *   │ toolbar — ordinary tools + AI tools      │
 *   ├───────────┬──────────────────────────────┤
 *   │ media bin │ preview (WebGL canvas)       │
 *   │           ├──────────────────────────────┤
 *   │           │ inspector                    │
 *   ├───────────┴──────────────────────────────┤
 *   │ timeline — video / audio / text tracks   │
 *   └──────────────────────────────────────────┘
 *
 * Nothing here is wired yet. The compositor spike (M1) comes first — it is the
 * one part with no library to fall back on, and if it resists, the whole
 * schedule moves. Do not build this layout out before the spike lands.
 */
export function EditorClient({ projectId }: { projectId: string }) {
  return (
    <div className="no-select flex h-screen flex-col">
      <header className="flex h-12 shrink-0 items-center border-b border-[var(--color-rule)] px-4">
        <span className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
          Toolbar
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="w-64 shrink-0 border-r border-[var(--color-rule)] p-4">
          <span className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
            Media
          </span>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-1 items-center justify-center bg-black/40">
            <p className="text-sm text-[var(--color-ink-2)]">
              Preview canvas — project <code className="font-mono">{projectId}</code>
            </p>
          </div>
          <div className="h-40 shrink-0 border-t border-[var(--color-rule)] p-4">
            <span className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
              Inspector
            </span>
          </div>
        </div>
      </div>

      <footer className="h-56 shrink-0 border-t border-[var(--color-rule)] p-4">
        <span className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
          Timeline
        </span>
      </footer>
    </div>
  )
}
