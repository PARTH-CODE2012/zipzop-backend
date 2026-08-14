/**
 * Billing settings. Built in M6.
 *
 * The one screen where the three balances are shown separately — the monthly
 * allowance with its expiry date, purchased credits, and the face-mapping
 * meter. Everywhere else in the product shows a single total.
 * See docs/04-frontend-architecture.md §8.1.
 */
export default function BillingPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <h1 className="text-2xl font-bold">Billing</h1>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">Built in M6.</p>
    </main>
  )
}
