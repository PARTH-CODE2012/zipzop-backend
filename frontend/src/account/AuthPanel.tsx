'use client'

/**
 * Sign in and register, in one panel.
 *
 * Unstyled beyond what makes it usable, like the rest of M2 — no palette has
 * been delivered, so the form is a form.
 */

import { useState } from 'react'

import { describe, useSession } from '@/account/session'

export function AuthPanel() {
  const { signIn, signUp } = useSession()
  const [mode, setMode] = useState<'sign-in' | 'register'>('sign-in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'sign-in') await signIn(email, password)
      else await signUp(email, password, displayName || undefined)
    } catch (cause) {
      setError(describe(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto flex w-full max-w-sm flex-col gap-3 p-6 text-sm"
      data-testid="auth-panel"
      data-mode={mode}
    >
      <h1 className="text-base">{mode === 'sign-in' ? 'Sign in' : 'Create an account'}</h1>

      <label className="flex flex-col gap-1">
        <span style={{ color: 'var(--color-ink-2)' }}>Email</span>
        <input
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="rounded border px-2 py-1.5"
          style={{ borderColor: 'var(--color-rule)', background: 'var(--color-surface-2)' }}
          data-testid="email"
          autoComplete="email"
        />
      </label>

      {mode === 'register' && (
        <label className="flex flex-col gap-1">
          <span style={{ color: 'var(--color-ink-2)' }}>Name</span>
          <input
            type="text"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="rounded border px-2 py-1.5"
            style={{ borderColor: 'var(--color-rule)', background: 'var(--color-surface-2)' }}
            data-testid="display-name"
            autoComplete="name"
          />
        </label>
      )}

      <label className="flex flex-col gap-1">
        <span style={{ color: 'var(--color-ink-2)' }}>Password</span>
        <input
          type="password"
          required
          // Matches the server's minimum. There is no maximum worth enforcing:
          // the password is SHA-256'd before bcrypt sees it, so a passphrase is
          // neither truncated nor rejected.
          minLength={8}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="rounded border px-2 py-1.5"
          style={{ borderColor: 'var(--color-rule)', background: 'var(--color-surface-2)' }}
          data-testid="password"
          autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
        />
      </label>

      {error && (
        <p role="alert" data-testid="auth-error" className="text-xs">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="rounded border px-3 py-1.5 disabled:opacity-50"
        style={{ borderColor: 'var(--color-rule)' }}
        data-testid="submit"
      >
        {busy ? 'Working…' : mode === 'sign-in' ? 'Sign in' : 'Create account'}
      </button>

      <button
        type="button"
        onClick={() => {
          setMode(mode === 'sign-in' ? 'register' : 'sign-in')
          setError(null)
        }}
        className="text-xs underline"
        style={{ color: 'var(--color-ink-2)' }}
        data-testid="switch-mode"
      >
        {mode === 'sign-in' ? 'Create an account instead' : 'I already have an account'}
      </button>
    </form>
  )
}
