import type { NextConfig } from 'next'

const config: NextConfig = {
  reactStrictMode: true,

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
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
    ]
  },
}

export default config
