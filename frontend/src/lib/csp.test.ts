import { describe, expect, it } from 'vitest'
import { NextRequest } from 'next/server'

import { middleware } from '@/middleware'

import { buildCsp, newNonce } from './csp'

/** One directive's value, e.g. `directive(csp, 'script-src')`. */
function directive(csp: string, name: string): string {
  const found = csp
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name} `))
  if (!found) throw new Error(`no ${name} in ${csp}`)
  return found
}

describe('buildCsp', () => {
  it('lets scripts run by nonce and never by being inline (M7-20)', () => {
    const scripts = directive(buildCsp({ nonce: 'abc123', development: false, env: {} }), 'script-src')

    expect(scripts).toContain("'nonce-abc123'")
    expect(scripts).toContain("'strict-dynamic'")
    expect(scripts).not.toContain("'unsafe-inline'")
    expect(scripts).not.toContain("'unsafe-eval'")
  })

  it("allows eval in development only, for Fast Refresh", () => {
    const scripts = directive(buildCsp({ nonce: 'n', development: true, env: {} }), 'script-src')
    expect(scripts).toContain("'unsafe-eval'")
  })

  it('reaches the deployment’s own origins and nothing else', () => {
    const csp = buildCsp({
      nonce: 'n',
      development: false,
      env: {
        NEXT_PUBLIC_API_BASE_URL: 'https://api.zipzop.app/v1',
        NEXT_PUBLIC_WS_URL: 'wss://api.zipzop.app/v1/ws',
        NEXT_PUBLIC_MEDIA_ORIGIN: 'https://media.zipzop.app',
      },
    })

    expect(directive(csp, 'connect-src')).toBe(
      "connect-src 'self' https://api.zipzop.app wss://api.zipzop.app https://media.zipzop.app",
    )
    expect(directive(csp, 'img-src')).toContain('https://media.zipzop.app')
    expect(csp).not.toContain('localhost')
  })

  it('keeps the closed directives closed', () => {
    const csp = buildCsp({ nonce: 'n', development: false, env: {} })
    for (const closed of [
      "frame-ancestors 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ]) {
      expect(csp).toContain(closed)
    }
  })
})

describe('newNonce', () => {
  it('is 128 random bits, never the same twice', () => {
    const seen = new Set(Array.from({ length: 200 }, () => newNonce()))
    expect(seen.size).toBe(200)
    for (const nonce of seen) expect(atob(nonce)).toHaveLength(16)
  })
})

describe('middleware', () => {
  const nonceIn = (csp: string | null) => /'nonce-([^']+)'/.exec(csp ?? '')?.[1]

  it('puts one policy on the response and on the request Next renders from', () => {
    const response = middleware(new NextRequest('http://localhost:3000/editor/prj_1'))

    const sent = response.headers.get('content-security-policy')
    // How `NextResponse.next({ request })` hands rewritten request headers on.
    const forwarded = response.headers.get('x-middleware-request-content-security-policy')

    expect(nonceIn(sent)).toBeTruthy()
    expect(forwarded).toBe(sent)
  })

  it('draws a new nonce for every response', () => {
    const first = middleware(new NextRequest('http://localhost:3000/'))
    const second = middleware(new NextRequest('http://localhost:3000/'))

    expect(nonceIn(first.headers.get('content-security-policy'))).not.toBe(
      nonceIn(second.headers.get('content-security-policy')),
    )
  })
})
