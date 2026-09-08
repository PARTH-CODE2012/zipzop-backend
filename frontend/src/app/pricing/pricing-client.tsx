'use client'

/**
 * The pricing table.
 *
 * Driven entirely by `GET /plans`, so a repricing is a database change rather
 * than a deploy — and so a plan retired with `is_public` disappears here without
 * anybody editing this file. Nothing about a tier is written down in the
 * frontend: not a price, not a credit count, not a name.
 *
 * Three things this page is deliberately honest about, because the competitor
 * research that prompted M6 found the opposite everywhere
 * (docs/20-m6-readiness.md §5):
 *
 * * **the price is shown before the click**, in the currency the buyer chose;
 * * **the currency is theirs to change** — the server's IP guess is a default,
 *   and VPNs, travellers and expatriates make it unreliable;
 * * **no countdowns, no "most popular" badge on the tier we want sold.** The
 *   only emphasis is on the plan the account is already on.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'

import { useSession } from '@/account/session'
import { credits as formatCredits, monthlyPrice } from '@/billing/money'
import { ApiError } from '@/lib/api/client'
import { listPlans, startCheckout, type PlanOfferOut, type PlansResponse } from '@/lib/api/endpoints'

const CURRENCIES = ['USD', 'INR'] as const
type Currency = (typeof CURRENCIES)[number]

export function PricingClient() {
  const router = useRouter()
  const { status, account } = useSession()
  const [currency, setCurrency] = useState<Currency | null>(null)
  const [data, setData] = useState<PlansResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async (want: Currency | null) => {
    try {
      const response = await listPlans(want ?? undefined)
      setData(response)
      // Adopt the server's suggestion the first time, then leave it alone: a
      // list that re-guessed on every load would undo the user's own choice.
      setCurrency((current) => current ?? (response.suggestedCurrency as Currency))
      setError(null)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not load the plans.')
    }
  }, [])

  useEffect(() => {
    void load(null)
  }, [load])

  function choose(next: Currency) {
    setCurrency(next)
    void load(next)
  }

  async function subscribe(plan: PlanOfferOut) {
    if (status !== 'signed-in') {
      // Not a dead end: they came here to buy something, so send them where
      // they can, and come back.
      router.push(`/login?next=${encodeURIComponent('/pricing')}`)
      return
    }
    setBusy(plan.code)
    setError(null)
    try {
      const result = await startCheckout({
        plan: plan.code,
        currency: currency ?? null,
        returnUrl: `${window.location.origin}/settings/billing?confirming=1`,
      })
      if (result.checkoutUrl) {
        window.location.href = result.checkoutUrl
        return
      }
      // A downgrade: scheduled for the period boundary, nothing to pay. The
      // server returns this instead of a checkout page rather than as an error,
      // because it is a success.
      router.push('/settings/billing?scheduled=1')
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not start the checkout.')
      setBusy(null)
    }
  }

  const currentPlan = account?.subscription?.plan ?? null

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="flex flex-wrap items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Plans</h1>
          <p className="mt-2 max-w-prose text-sm" style={{ color: 'var(--color-ink-2)' }}>
            Credits pay for the work — transcription, trimming, colour, export. Editing,
            saving and everything already in your projects are always free.
          </p>
        </div>

        <fieldset className="flex items-center gap-2" data-testid="currency-picker">
          <legend className="sr-only">Currency</legend>
          {CURRENCIES.map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => choose(code)}
              aria-pressed={currency === code}
              className="px-3 py-1.5 text-xs"
              style={{
                color: currency === code ? 'var(--color-ink)' : 'var(--color-ink-3)',
                borderBottom:
                  currency === code
                    ? '2px solid var(--color-accent)'
                    : '2px solid transparent',
              }}
            >
              {code}
            </button>
          ))}
        </fieldset>
      </header>

      {error && (
        <p className="mt-6 text-sm" role="alert" style={{ color: 'var(--color-danger)' }}>
          {error}
        </p>
      )}

      {!data && !error && (
        <p className="mt-10 text-sm" style={{ color: 'var(--color-ink-3)' }}>
          Loading the plans…
        </p>
      )}

      {data && (
        <ul
          className="mt-10 grid gap-4"
          style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}
          data-testid="plan-list"
        >
          {data.plans.map((plan) => (
            <PlanCard
              key={plan.code}
              plan={plan}
              current={plan.code === currentPlan}
              busy={busy === plan.code}
              onChoose={() => void subscribe(plan)}
            />
          ))}
        </ul>
      )}

      <p className="mt-10 max-w-prose text-xs" style={{ color: 'var(--color-ink-3)' }}>
        Cancel in one click from your billing settings, at any time. You keep the plan until
        the end of the period you have paid for, and any credits you bought outright never
        expire. We charge nothing you have not chosen.
      </p>
    </main>
  )
}

function PlanCard({
  plan,
  current,
  busy,
  onChoose,
}: {
  plan: PlanOfferOut
  current: boolean
  busy: boolean
  onChoose: () => void
}) {
  const free = plan.priceMinor === 0
  return (
    <li
      className="flex flex-col gap-3 p-5"
      data-testid={`plan-${plan.code}`}
      style={{
        background: 'var(--color-surface-2)',
        border: current
          ? '1px solid var(--color-accent-line)'
          : '1px solid var(--color-rule)',
      }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">{plan.displayName}</h2>
        {current && (
          <span className="text-xs" style={{ color: 'var(--color-accent)' }}>
            Your plan
          </span>
        )}
      </div>

      <p className="tnum text-xl font-bold">{monthlyPrice(plan.priceMinor, plan.currency)}</p>

      <dl className="flex flex-col gap-1.5 text-xs" style={{ color: 'var(--color-ink-2)' }}>
        <Line label="Credits" value={`${formatCredits(plan.monthlyCredits)} a month`} />
        {/* The marketing figure, and it is labelled as approximate because it
            is derived from a reference project rather than measured on yours. */}
        <Line label="Roughly" value={`${plan.approxVideosPerMonth} videos a month`} />
        <Line label="Export up to" value={`${plan.maxExportHeight}p`} />
        <Line
          label="Watermark"
          value={plan.watermark === 'none' ? 'None' : plan.watermark === 'custom' ? 'Your own' : 'Ours'}
        />
        <Line label="Queue" value={plan.queueLabel} />
      </dl>

      <button
        type="button"
        onClick={onChoose}
        disabled={current || free || busy}
        className="mt-auto px-4 py-2 text-sm disabled:opacity-40"
        style={{
          background: current ? 'transparent' : 'var(--color-accent)',
          color: current ? 'var(--color-ink-3)' : 'var(--color-accent-ink)',
        }}
      >
        {current ? 'Current plan' : free ? 'Included' : busy ? 'Opening…' : `Choose ${plan.displayName}`}
      </button>
    </li>
  )
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt>{label}</dt>
      <dd style={{ color: 'var(--color-ink)' }}>{value}</dd>
    </div>
  )
}
