/**
 * Billing settings.
 *
 * The one screen where the three balances are shown separately — the monthly
 * allowance with its expiry date, purchased credits, and the face-mapping
 * meter. Everywhere else in the product shows a single total.
 * See docs/04-frontend-architecture.md §8.1.
 *
 * Wrapped in `Suspense` because the client reads `?confirming=1` with
 * `useSearchParams`, which Next requires a boundary for in a static route.
 */

import { Suspense } from 'react'

import { BillingClient } from './billing-client'

export const metadata = { title: 'Billing · ZipZop' }

export default function BillingPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-2xl px-6 py-12">
          <h1 className="text-2xl font-bold">Billing</h1>
        </main>
      }
    >
      <BillingClient />
    </Suspense>
  )
}
