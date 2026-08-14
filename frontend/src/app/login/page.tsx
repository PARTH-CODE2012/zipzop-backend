/**
 * Sign in / register. Built in M2 alongside the auth endpoints.
 * See docs/05-api-contract.md §2.
 */
export default function LoginPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 px-6">
      <h1 className="text-2xl font-bold">Sign in</h1>
      <p className="text-sm text-[var(--color-ink-2)]">Built in M2.</p>
    </main>
  )
}
