import type { NextConfig } from 'next'

const config: NextConfig = {
  reactStrictMode: true,

  // No `X-Powered-By: Next.js`. Naming the framework and nothing else is what
  // a scanner looking for the next Next.js advisory reads first (ZAP baseline
  // on the staging stack, docs/24-m7-closure.md §3.1).
  poweredByHeader: false,

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
        // The Content-Security-Policy is not here: it carries a per-response
        // nonce, so `src/middleware.ts` sets it (M7-20, `src/lib/csp.ts`).
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          // The editor uses none of these, so no page — and no script that
          // reached one — can ask for them. Not `payment`: the checkout
          // provider's frame may use the Payment Request API.
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=(), browsing-topics=()',
          },
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
