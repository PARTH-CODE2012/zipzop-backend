/**
 * Pricing table. Driven by GET /plans so a price change is a data change rather
 * than a deploy — and so a plan retired through `plans.is_public` disappears
 * from here without this file being edited.
 *
 * Tiers are shown as "≈ N videos/month" beside the credit count because that is
 * what a creator understands; credits stay the unit everything is actually
 * priced in. See docs/01-product-vision.md §8.2 and contract §7.
 */

import { PricingClient } from './pricing-client'

export const metadata = { title: 'Plans · ZipZop' }

export default function PricingPage() {
  return <PricingClient />
}
