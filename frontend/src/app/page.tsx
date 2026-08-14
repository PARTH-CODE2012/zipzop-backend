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
      <div className="flex gap-4">
        <Link href="/projects" className="underline underline-offset-4">
          Open the editor
        </Link>
        <Link href="/pricing" className="underline underline-offset-4">
          Pricing
        </Link>
      </div>
    </main>
  )
}
