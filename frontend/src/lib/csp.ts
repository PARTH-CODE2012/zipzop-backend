// --------------------------------------------------------------------------
// Content-Security-Policy — docs/07-security.md §5.2, docs/22-m7-readiness.md §2.4
//
// It matters more here than it usually would because of the refresh-token
// design: contract 1.2 moved the refresh token into an httpOnly cookie on the
// argument that an XSS which can call the API still cannot walk away with a
// 30-day credential. A CSP is the control that makes that argument true in
// practice — it is what keeps an injected `<script>` from running at all.
//
// **A nonce per response, not `'unsafe-inline'` (M7-20).** The App Router emits
// inline bootstrap and hydration scripts, and until M7 closed the only way to
// let them run was `'unsafe-inline'`, which lets *any* inline script run —
// including an injected one. `src/middleware.ts` now draws a fresh nonce for
// every request and puts this policy on the request as well as the response;
// Next reads the nonce from the request's header and stamps it on every script
// it renders. `'strict-dynamic'` then extends that trust to the chunks those
// scripts load, and to nothing an attacker writes into the page. The cost is
// that pages render per request rather than once at build (`app/layout.tsx`).
//
// The origins the browser legitimately reaches are deployment-specific and read
// from the same `NEXT_PUBLIC_*` variables the client already uses:
//
//   * the API (fetch + the presigned-PUT upload go to it and to storage);
//   * the WebSocket (`connect-src` covers ws/wss);
//   * media — proxies, thumbnails and peaks are presigned URLs on the storage
//     origin (MinIO in dev, CloudFront in prod), loaded into <img>, <video> and
//     fetch. `NEXT_PUBLIC_MEDIA_ORIGIN` names it; without it the app can only
//     reach same-origin media, which is wrong in every real deployment.
//
// One directive still carries a documented compromise: `style-src` includes
// `'unsafe-inline'` for the styles React and Tailwind inject. A style cannot
// run code; the residual is CSS-based exfiltration, which needs an injection
// point the code does not have (no `dangerouslySetInnerHTML` anywhere).
//
// Everything else is closed: `frame-ancestors 'none'` (clickjacking, §6.9),
// `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`.

function originOf(url: string | undefined): string | null {
  if (!url) return null
  try {
    return new URL(url).origin
  } catch {
    return null
  }
}

export interface CspOptions {
  nonce: string
  /** Development needs `'unsafe-eval'` for Fast Refresh; production never. */
  development: boolean
  env?: Record<string, string | undefined>
}

export function buildCsp({ nonce, development, env = process.env }: CspOptions): string {
  const api = originOf(env.NEXT_PUBLIC_API_BASE_URL) ?? 'http://localhost:8123'
  const ws = originOf(env.NEXT_PUBLIC_WS_URL) ?? 'ws://localhost:8123'
  // Storage origin for presigned media. Defaults to the dev MinIO; must be set
  // to the CDN origin in production or graded frames and waveforms will not load.
  const media = originOf(env.NEXT_PUBLIC_MEDIA_ORIGIN) ?? 'http://localhost:9000'

  const connect = Array.from(new Set(["'self'", api, ws, media])).join(' ')
  const mediaSources = Array.from(new Set(["'self'", 'blob:', 'data:', media])).join(' ')

  // Next's dev server (Fast Refresh / react-refresh) evaluates strings as
  // JavaScript, so development — and only development — needs `'unsafe-eval'`.
  // A production build ships no react-refresh runtime.
  const devEval = development ? " 'unsafe-eval'" : ''

  return [
    "default-src 'self'",
    // `'self'` is ignored by browsers that understand `'strict-dynamic'`, and
    // kept for the ones that do not.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${devEval}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data: " + media,
    `media-src ${mediaSources}`,
    `connect-src ${connect}`,
    "font-src 'self' data:",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ')
}

/** 128 random bits, base64 — what CSP3 asks a nonce to be. */
export function newNonce(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return btoa(String.fromCharCode(...bytes))
}
