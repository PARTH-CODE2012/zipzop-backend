'use client'

/**
 * Keeps a compositor failure inside the preview.
 *
 * **The compositor having no fallback is a decision; the editor going with it
 * is not.** `docs/04-frontend-architecture.md` §4.4 commits to one WebGL2
 * canvas and no software path — that is deliberate and stays. What was not
 * deliberate is what happened when the context could not be created: the
 * renderer threw during render, no boundary caught it, and React unmounted the
 * whole tree. The user got a white page reading *"Application error: a
 * client-side exception has occurred"*, with their timeline, their media and
 * their unsaved work behind it and no way back.
 *
 * Found by opening the editor in a browser without WebGL2. Every other part of
 * the application works perfectly well without a picture — the timeline, the
 * inspector, the tools, autosave — so a failure here takes the picture and
 * nothing else.
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  message: string | null
}

export class PreviewBoundary extends Component<Props, State> {
  override state: State = { message: null }

  static getDerivedStateFromError(error: unknown): State {
    return {
      message:
        error instanceof Error ? error.message : 'The preview could not start on this browser.',
    }
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // Logged rather than reported: this is an environment fact about the
    // viewer's browser, not a fault in the project, and a bug report for it
    // would be noise.
    console.error('preview failed', error, info.componentStack)
  }

  override render(): ReactNode {
    if (this.state.message === null) return this.props.children

    return (
      <div
        className="flex h-full w-full flex-col items-center justify-center gap-2 px-6 text-center text-xs"
        style={{ background: 'rgba(0,0,0,0.4)', color: 'var(--color-ink-3)' }}
        data-testid="preview-unavailable"
      >
        <p style={{ color: 'var(--color-ink-2)' }}>The preview cannot run here.</p>
        <p>{this.state.message}</p>
        <p style={{ color: 'var(--color-ink-faint)' }}>
          Everything else still works — editing, saving and the tools are unaffected.
        </p>
      </div>
    )
  }
}
