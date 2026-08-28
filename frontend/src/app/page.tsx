import Link from 'next/link'

/**
 * Landing page. Server-rendered — this is the part of the product Next.js
 * actually earns its place on.
 */
export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-6 px-6">
      <p className="font-mono text-xs uppercase tracking-widest text-[var(--color-ink-2)]">
        ZipZop
      </p>
      <h1 className="text-4xl font-bold tracking-tight">
        A video editor that does the boring work for you
      </h1>
      <p className="text-lg text-[var(--color-ink-2)]">
        Cut the silences, generate the captions, grade the picture — then adjust any of it, because
        it all lands on a real timeline.
      </p>
      {/* M4.5 item 1: `/projects` was the only way in and it was a stub, so
          this link led nowhere anybody could use. It is a real list now, and
          `/editor/scratch` — a route segment that is not a project id means
          *open a fresh project* — is named here too, because it was previously
          reachable only by knowing the URL. */}
      <div className="flex flex-wrap items-center gap-4">
        <Link
          href="/editor/scratch"
          className="px-4 py-2 text-sm"
          style={{
            borderRadius: 'var(--radius-pill)',
            background: 'var(--color-accent)',
            color: 'var(--color-accent-ink)',
            fontWeight: 600,
          }}
        >
          Start editing
        </Link>
        <Link href="/projects" className="underline underline-offset-4">
          Your projects
        </Link>
        <Link href="/pricing" className="underline underline-offset-4">
          Pricing
        </Link>
      </div>
    </main>
  )
}
