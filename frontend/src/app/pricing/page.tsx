/**
 * Pricing table. Built in M6, driven by GET /plans so a price change is a
 * data change rather than a deploy.
 *
 * Tiers are shown as "≈ N videos/month" because that is what a creator
 * understands — credits are the internal unit, never the advertised one.
 * See docs/01-product-vision.md §8.2.
 */
export default function PricingPage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <h1 className="text-2xl font-bold">Pricing</h1>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">Built in M6.</p>
    </main>
  )
}
