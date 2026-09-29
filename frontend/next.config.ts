import type { NextConfig } from 'next'

// --------------------------------------------------------------------------
// Content-Security-Policy — docs/07-security.md §5.2, docs/22-m7-readiness.md §2.4
//
// The header the review asked for and the config never set. It matters more
// here than it usually would because of the refresh-token design: contract 1.2
// moved the refresh token into an httpOnly cookie on the argument that an XSS
// which can call the API still cannot walk away with a 30-day credential. A CSP
// is the control that makes that argument true in practice — it is what keeps an
// injected `<script src=evil>` from running at all.
//
// The origins the browser legitimately reaches are deployment-specific and read
// from the same `NEXT_PUBLIC_*` variables the client already uses, so the policy
// is built here rather than hard-coded:
//
//   * the API (fetch + the presigned-PUT upload go to it and to storage);
//   * the WebSocket (`connect-src` covers ws/wss);
//   * media — proxies, thumbnails and peaks are presigned URLs on the storage
//     origin (MinIO in dev, CloudFront in prod), loaded into <img>, <video> and
//     fetch. `NEXT_PUBLIC_MEDIA_ORIGIN` names it; without it the app can only
//     reach same-origin media, which is wrong in every real deployment.
//
// Two directives carry a known, documented compromise:
//   * `script-src` includes `'unsafe-inline'` because the App Router emits inline
//     bootstrap/hydration scripts and this build has no nonce middleware yet.
//     The injection surface it would otherwise open is closed from the other
//     side: there is no `dangerouslySetInnerHTML` and no `innerHTML =` anywhere
//     in `frontend/src` (§2.4). Tightening this to a nonce is the follow-up.
//   * `style-src` includes `'unsafe-inline'` for Tailwind's injected styles.
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

function buildCsp(): string {
  const api = originOf(process.env.NEXT_PUBLIC_API_BASE_URL) ?? 'http://localhost:8123'
  const ws = originOf(process.env.NEXT_PUBLIC_WS_URL) ?? 'ws://localhost:8123'
  // Storage origin for presigned media. Defaults to the dev MinIO; must be set
  // to the CDN origin in production or graded frames and waveforms will not load.
  const media = originOf(process.env.NEXT_PUBLIC_MEDIA_ORIGIN) ?? 'http://localhost:9000'

  const connect = Array.from(new Set(["'self'", api, ws, media])).join(' ')
  const mediaSources = Array.from(new Set(["'self'", 'blob:', 'data:', media])).join(' ')

  // Next's dev server (Fast Refresh / react-refresh) evaluates strings as
  // JavaScript, so development — and only development — needs `'unsafe-eval'`.
  // A production build ships no react-refresh runtime, so the tighter policy
  // holds where it counts. Gating on NODE_ENV keeps the two apart.
  const devEval = process.env.NODE_ENV !== 'production' ? " 'unsafe-eval'" : ''

  return [
    "default-src 'self'",
    // App Router hydration is inline; no nonce middleware yet (see header note).
    `script-src 'self' 'unsafe-inline'${devEval}`,
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

const config: NextConfig = {
  reactStrictMode: true,

  // No image optimisation, on purpose. Every picture in the product is a
  // presigned URL on another origin, drawn with a plain <img> (see the notes in
  // MediaBin.tsx and projects-client.tsx), so `/_next/image` served nothing we
  // use — and it is exactly the surface of GHSA-2xp9-vwfh-vxw4, an
  // unauthenticated RCE in Next's image optimizer found in the M7 audit.
  // `next` is upgraded past it too; this removes the endpoint's reason to exist.
  images: { unoptimized: true },

  // The editor mounts video elements and a WebGL context. Strict Mode's
  // double-invoke in development is useful everywhere else, so it stays on —
  // but effects that create a GL context or a <video> must be written to
  // tolerate being run twice. If you see two compositors fighting, that is why.

  eslint: {
    // Linting runs as its own step — `pnpm lint`, and a dedicated CI job that
    // fails the pipeline. This does NOT mean lint errors are tolerated; it
    // means they are reported once, by the ESLint CLI, instead of twice with
    // Next's build-time detection warning about a flat config it cannot
    // recognise. See eslint.config.mjs for why eslint-config-next is not used.
    ignoreDuringBuilds: true,
  },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'Content-Security-Policy', value: buildCsp() },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          // Kept alongside `frame-ancestors 'none'` for browsers that predate
          // CSP framing — belt and braces, not a duplicate.
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
    ]
  },
}

export default config
