'use client'

/**
 * Who is signed in.
 *
 * The access token lives in memory inside `lib/api/client.ts` and is never
 * written to storage, so a reload starts with nothing. The refresh token is an
 * httpOnly cookie the client cannot read — the only way to ask whether a
 * session survived a reload is to try to use it, which is what `restore` does
 * on mount.
 *
 * That one round trip on load is the price of a refresh token JavaScript
 * cannot steal, and it is worth paying.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@/lib/api/client'
import * as endpoints from '@/lib/api/endpoints'
import type { MeResponse } from '@/lib/api/endpoints'

type Status = 'restoring' | 'signed-in' | 'signed-out'

interface Session {
  status: Status
  account: MeResponse | null
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string, displayName?: string) => Promise<void>
  signOut: () => Promise<void>
  refreshAccount: () => Promise<void>
}

const SessionContext = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>('restoring')
  const [account, setAccount] = useState<MeResponse | null>(null)

  const load = useCallback(async () => {
    const me = await endpoints.me()
    setAccount(me)
    setStatus('signed-in')
  }, [])

  useEffect(() => {
    let live = true
    ;(async () => {
      try {
        await endpoints.refresh()
        if (live) await load()
      } catch {
        // No cookie, or a rotated one. Either way there is no session, and
        // that is an ordinary state on a first visit rather than an error.
        if (live) {
          setAccount(null)
          setStatus('signed-out')
        }
      }
    })()
    return () => {
      live = false
    }
  }, [load])

  const signIn = useCallback(
    async (email: string, password: string) => {
      await endpoints.login({ email, password })
      await load()
    },
    [load],
  )

  const signUp = useCallback(
    async (email: string, password: string, displayName?: string) => {
      await endpoints.register({ email, password, displayName: displayName ?? null })
      await load()
    },
    [load],
  )

  const signOut = useCallback(async () => {
    await endpoints.logout()
    setAccount(null)
    setStatus('signed-out')
  }, [])

  const value = useMemo<Session>(
    () => ({ status, account, signIn, signUp, signOut, refreshAccount: load }),
    [status, account, signIn, signUp, signOut, load],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession must be used inside a SessionProvider')
  return session
}

/** Turns an API failure into a sentence, branching on `code` and never on text. */
export function describe(cause: unknown): string {
  if (cause instanceof ApiError) {
    switch (cause.code) {
      case 'INVALID_CREDENTIALS':
        return cause.message
      case 'RATE_LIMITED':
        return 'Too many attempts. Wait a moment and try again.'
      case 'VALIDATION_ERROR':
        return 'Check the email address and password.'
      default:
        return cause.message
    }
  }
  return 'Something went wrong. Try again.'
}
