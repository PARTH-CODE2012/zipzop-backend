import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

import { buildCsp, newNonce } from '@/lib/csp'

/**
 * A fresh CSP nonce for every page response (M7-20) — see `src/lib/csp.ts`.
 *
 * The policy goes on the **request** too: that is where Next reads the nonce
 * from, to stamp it on the scripts it renders. On the response, it is the
 * policy the browser enforces. One nonce per response, never reused — a nonce
 * an attacker can predict or replay is `'unsafe-inline'` with extra steps.
 */
export function middleware(request: NextRequest): NextResponse {
  const csp = buildCsp({ nonce: newNonce(), development: process.env.NODE_ENV !== 'production' })

  const requestHeaders = new Headers(request.headers)
  requestHeaders.set('Content-Security-Policy', csp)

  const response = NextResponse.next({ request: { headers: requestHeaders } })
  response.headers.set('Content-Security-Policy', csp)
  return response
}

export const config = {
  matcher: [
    {
      // Documents only: static chunks carry no policy of their own, and a
      // prefetch is not a page the browser will execute as one.
      source: '/((?!_next/static|_next/image|favicon.ico).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
}
