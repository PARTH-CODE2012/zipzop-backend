'use client'

/**
 * Billing settings.
 *
 * **The one screen where the three balances are shown separately** — the monthly
 * allowance with its expiry date, purchased credits, and the face-mapping meter.
 * Everywhere else in the product shows a single total, because three numbers in
 * a toolbar is three numbers to reconcile while trying to edit a video
 * (docs/04-frontend-architecture.md §8.1).
 *
 * It is also where the two moments that decide whether people trust us happen:
 *
 * * **coming back from a checkout.** The redirect proves nothing — the webhook
 *   is what activates a plan — so arriving here with `?confirming=1` polls
 *   `GET /me` behind a calm "confirming your payment" state and never announces
 *   a success it has not seen. See `billing/confirming.ts`.
 * * **cancelling.** One click, no funnel, no retention offer, and the cost
 *   stated *before* the confirm — the server returns exactly what will be lost
 *   and kept for that purpose.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'

import { AuthPanel } from '@/account/AuthPanel'
import { useSession } from '@/account/session'
import {
  confirmMessage,
  confirmState,
  POLL_INTERVAL_MS,
  shouldKeepPolling,
  type ConfirmState,
} from '@/billing/confirming'
import { credits as formatCredits, expiryDate, price } from '@/billing/money'
import { ApiError } from '@/lib/api/client'
import {
  cancelSubscription,
  creditLedger,
  listTopupPacks,
  openPortal,
  startTopup,
  type CancelResponse,
  type LedgerEntryOut,
  type TopupPacksResponse,
} from '@/lib/api/endpoints'

export function BillingClient() {
  const { status } = useSession()

  if (status === 'restoring') {
    return <Centered>Checking your session…</Centered>
  }
  if (status === 'signed-out') {
    return (
      <Centered>
        <AuthPanel />
      </Centered>
    )
  }
  return <SignedIn />
}

function SignedIn() {
  const { refreshAccount } = useSession()
  const params = useSearchParams()
  const [error, setError] = useState<string | null>(null)

  const confirming = params.get('confirming') === '1'
  const scheduled = params.get('scheduled') === '1'

  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <h1 className="text-2xl font-bold">Billing</h1>

      {confirming && <ConfirmingBanner onSettled={refreshAccount} />}

      {scheduled && (
        <p
          className="mt-6 p-4 text-sm"
          style={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-rule)' }}
        >
          Your plan change is scheduled for the end of this billing period. Nothing to pay,
          and nothing changes until then — so the credits you have now are still yours to use.
        </p>
      )}

      {error && (
        <p className="mt-6 text-sm" role="alert" style={{ color: 'var(--color-danger)' }}>
          {error}
        </p>
      )}

      <Balances />
      <PlanSection onError={setError} />
      <Topups onError={setError} />
      <Ledger />
    </main>
  )
}

/* -------------------------------------------------------------- confirming */

function ConfirmingBanner({ onSettled }: { onSettled: () => Promise<void> }) {
  const { account } = useSession()
  const startedAt = useRef(Date.now())
  const planBefore = useRef(account?.subscription?.plan ?? 'free')
  const [elapsed, setElapsed] = useState(0)

  const state: ConfirmState = confirmState({
    planBefore: planBefore.current,
    planNow: account?.subscription?.plan ?? 'free',
    elapsedMs: elapsed,
  })

  useEffect(() => {
    if (!shouldKeepPolling(state)) return
    const timer = setInterval(() => {
      setElapsed(Date.now() - startedAt.current)
      // The webhook is what changes the answer, so this asks the server rather
      // than guessing from the URL it was sent back to.
      void onSettled()
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [state, onSettled])

  const done = state.kind === 'active'

  return (
    <p
      className="mt-6 flex items-center gap-3 p-4 text-sm"
      role="status"
      aria-live="polite"
      data-testid="confirming"
      style={{
        background: 'var(--color-surface-2)',
        border: `1px solid ${done ? 'var(--color-accent-line)' : 'var(--color-rule)'}`,
      }}
    >
      {!done && state.kind === 'confirming' && <Spinner />}
      <span>{confirmMessage(state)}</span>
    </p>
  )
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block h-3 w-3 shrink-0 rounded-full"
      style={{
        border: '2px solid var(--color-ink-faint)',
        borderTopColor: 'var(--color-accent)',
        animation: 'spin 0.8s linear infinite',
      }}
    />
  )
}

/* ---------------------------------------------------------------- balances */

function Balances() {
  const { account } = useSession()
  if (!account) return null
  const { credits } = account
  const expires = expiryDate(credits.planCreditsExpireAt)

  return (
    <section className="mt-8">
      <h2 className="text-sm font-semibold">Credits</h2>
      <dl className="mt-3 flex flex-col gap-2 text-sm">
        <Row
          label="This month's allowance"
          value={formatCredits(credits.plan)}
          // The only place this date is shown, and the reason the three balances
          // are split here: a single total cannot say what expires and when.
          note={expires ? `expires ${expires}` : null}
        />
        <Row
          label="Bought credits"
          value={formatCredits(credits.topup)}
          note="never expire, and survive cancelling"
        />
        {credits.facemapSeconds > 0 && (
          <Row label="Face-mapping seconds" value={formatCredits(credits.facemapSeconds)} />
        )}
        <Row label="Total you can spend" value={formatCredits(credits.total)} emphasis />
      </dl>
    </section>
  )
}

function Row({
  label,
  value,
  note,
  emphasis,
}: {
  label: string
  value: string
  note?: string | null
  emphasis?: boolean
}) {
  return (
    <div
      className="flex items-baseline justify-between gap-4"
      style={emphasis ? { borderTop: '1px solid var(--color-rule)', paddingTop: '0.5rem' } : undefined}
    >
      <dt style={{ color: 'var(--color-ink-2)' }}>
        {label}
        {note && (
          <span className="ml-2 text-xs" style={{ color: 'var(--color-ink-3)' }}>
            {note}
          </span>
        )}
      </dt>
      <dd className="tnum" style={{ fontWeight: emphasis ? 700 : 500 }}>
        {value}
      </dd>
    </div>
  )
}

/* -------------------------------------------------------------------- plan */

function PlanSection({ onError }: { onError: (message: string | null) => void }) {
  const { account, refreshAccount } = useSession()
  const [preview, setPreview] = useState<CancelResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [portalNote, setPortalNote] = useState<string | null>(null)

  const subscription = account?.subscription ?? null
  const paid = subscription != null && subscription.plan !== 'free'

  async function confirmCancel() {
    setBusy(true)
    onError(null)
    try {
      const result = await cancelSubscription(true)
      setPreview(result)
      await refreshAccount()
    } catch (cause) {
      onError(cause instanceof ApiError ? cause.message : 'Could not cancel.')
    } finally {
      setBusy(false)
    }
  }

  async function manage() {
    onError(null)
    try {
      const result = await openPortal(`${window.location.origin}/settings/billing`)
      if (result.portalUrl) {
        window.location.href = result.portalUrl
        return
      }
      // A real answer, not a failure: Razorpay hosts no customer portal. Saying
      // so beats opening a page that does not exist.
      setPortalNote(result.reason ?? 'Manage your subscription here.')
    } catch (cause) {
      onError(cause instanceof ApiError ? cause.message : 'Could not open the portal.')
    }
  }

  return (
    <section className="mt-10">
      <h2 className="text-sm font-semibold">Plan</h2>

      <div
        className="mt-3 flex flex-wrap items-center justify-between gap-4 p-4"
        style={{ background: 'var(--color-surface-2)', border: '1px solid var(--color-rule)' }}
      >
        <div>
          <p className="text-sm font-semibold">{subscription?.displayName ?? 'Free'}</p>
          <p className="mt-1 text-xs" style={{ color: 'var(--color-ink-3)' }}>
            {subscription?.cancelAtPeriodEnd
              ? `Ends ${expiryDate(subscription.currentPeriodEnd) ?? 'at the end of this period'} — you keep everything until then.`
              : paid
                ? `Renews ${expiryDate(subscription?.currentPeriodEnd) ?? 'monthly'}`
                : 'Upgrade whenever you want. Nothing you have made is affected.'}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Link href="/pricing" className="px-4 py-2 text-sm" style={{ color: 'var(--color-accent)' }}>
            {paid ? 'Change plan' : 'See the plans'}
          </Link>
          {paid && (
            <button type="button" onClick={() => void manage()} className="px-3 py-2 text-xs">
              Manage payment
            </button>
          )}
        </div>
      </div>

      {portalNote && (
        <p className="mt-2 text-xs" style={{ color: 'var(--color-ink-3)' }}>
          {portalNote}
        </p>
      )}

      {paid && !subscription?.cancelAtPeriodEnd && !preview && (
        <CancelBlock busy={busy} onConfirm={() => void confirmCancel()} />
      )}

      {preview && <CancelResult result={preview} />}
    </section>
  )
}

/**
 * One click, and the price of it stated in the same breath.
 *
 * No funnel, no "are you sure you want to lose all these features", no offer of
 * a discount to stay. CapCut is rated 1.2/5 on precisely this, and a cancel flow
 * that fights the user is the single loudest thing a review says about a product.
 */
function CancelBlock({ busy, onConfirm }: { busy: boolean; onConfirm: () => void }) {
  const [asked, setAsked] = useState(false)
  const { account } = useSession()

  if (!asked) {
    return (
      <button
        type="button"
        onClick={() => setAsked(true)}
        className="mt-3 text-xs"
        style={{ color: 'var(--color-ink-3)', textDecoration: 'underline' }}
        data-testid="cancel-start"
      >
        Cancel subscription
      </button>
    )
  }

  const plan = account?.credits.plan ?? 0
  const topup = account?.credits.topup ?? 0

  return (
    <div
      className="mt-3 flex flex-col gap-3 p-4 text-sm"
      style={{ border: '1px solid var(--color-rule)' }}
      data-testid="cancel-confirm"
    >
      <p>
        You keep your plan until the end of the period you have already paid for. After that
        the account returns to Free.
      </p>
      <p style={{ color: 'var(--color-ink-2)' }}>
        {plan > 0 && <>You will lose <strong>{formatCredits(plan)}</strong> unused plan credits. </>}
        {topup > 0 && <>Your <strong>{formatCredits(topup)}</strong> bought credits stay — they never expire.</>}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="px-4 py-2 text-sm disabled:opacity-50"
          style={{ border: '1px solid var(--color-danger)', color: 'var(--color-danger)' }}
        >
          {busy ? 'Cancelling…' : 'Cancel my subscription'}
        </button>
        <button type="button" onClick={() => setAsked(false)} className="px-4 py-2 text-sm">
          Keep it
        </button>
      </div>
    </div>
  )
}

function CancelResult({ result }: { result: CancelResponse }) {
  const lost = result.creditsLostAtPeriodEnd ?? {}
  const kept = result.creditsKept ?? {}
  return (
    <div
      className="mt-3 p-4 text-sm"
      style={{ border: '1px solid var(--color-rule)' }}
      data-testid="cancel-done"
    >
      <p>
        Cancelled. You keep the {result.plan} plan until{' '}
        {expiryDate(result.accessUntil) ?? 'the end of this period'}.
      </p>
      <p className="mt-2 text-xs" style={{ color: 'var(--color-ink-3)' }}>
        {Object.entries(lost)
          .filter(([, amount]) => amount > 0)
          .map(([bucket, amount]) => `${formatCredits(amount)} ${label(bucket)} expire then`)
          .join(' · ') || 'Nothing expires.'}
        {Object.entries(kept).some(([, amount]) => amount > 0) &&
          ` · ${Object.entries(kept)
            .filter(([, amount]) => amount > 0)
            .map(([bucket, amount]) => `${formatCredits(amount)} ${label(bucket)} stay`)
            .join(' · ')}`}
      </p>
    </div>
  )
}

function label(bucket: string): string {
  switch (bucket) {
    case 'plan':
      return 'plan credits'
    case 'topup':
      return 'bought credits'
    case 'facemapSeconds':
      return 'face-mapping seconds'
    default:
      return bucket
  }
}

/* ----------------------------------------------------------------- top-ups */

function Topups({ onError }: { onError: (message: string | null) => void }) {
  const [packs, setPacks] = useState<TopupPacksResponse | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    listTopupPacks()
      .then(setPacks)
      .catch(() => setPacks(null))
  }, [])

  async function buy(packCode: string) {
    setBusy(packCode)
    onError(null)
    try {
      const result = await startTopup({
        packCode,
        currency: packs?.currency ?? null,
        returnUrl: `${window.location.origin}/settings/billing?confirming=1`,
      })
      if (result.checkoutUrl) window.location.href = result.checkoutUrl
    } catch (cause) {
      onError(cause instanceof ApiError ? cause.message : 'Could not start the purchase.')
      setBusy(null)
    }
  }

  if (!packs || packs.packs.length === 0) return null

  return (
    <section className="mt-10">
      <h2 className="text-sm font-semibold">Add credits</h2>
      <p className="mt-1 text-xs" style={{ color: 'var(--color-ink-3)' }}>
        A one-off purchase. These never expire and are not affected by cancelling.
      </p>
      <ul className="mt-3 flex flex-wrap gap-2">
        {packs.packs.map((pack) => (
          <li key={pack.code}>
            <button
              type="button"
              onClick={() => void buy(pack.code)}
              disabled={busy !== null}
              className="px-4 py-2 text-sm disabled:opacity-50"
              style={{ border: '1px solid var(--color-rule)' }}
            >
              {formatCredits(pack.credits)} credits ·{' '}
              <span className="tnum">{price(pack.priceMinor, pack.currency)}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ------------------------------------------------------------------ ledger */

function Ledger() {
  const [items, setItems] = useState<LedgerEntryOut[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (from: string | null) => {
    setLoading(true)
    try {
      const page = await creditLedger({ limit: 20, ...(from ? { cursor: from } : {}) })
      // The **first** page replaces; later pages append. Appending
      // unconditionally showed every movement twice in development, because
      // React 18 runs an effect twice on mount — and it would do the same in
      // production the first time anything re-ran the initial load.
      setItems((current) => (from ? [...(current ?? []), ...page.items] : page.items))
      setCursor(page.nextCursor ?? null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(null)
  }, [load])

  const rows = useMemo(() => items ?? [], [items])

  return (
    <section className="mt-10">
      <h2 className="text-sm font-semibold">Every movement</h2>
      <p className="mt-1 text-xs" style={{ color: 'var(--color-ink-3)' }}>
        Where your credits went, with nothing left out.
      </p>

      <ul className="mt-3 flex flex-col" data-testid="ledger">
        {rows.map((row) => (
          <li
            key={row.id}
            className="flex items-baseline justify-between gap-4 py-2 text-xs"
            style={{ borderBottom: '1px solid var(--color-rule)' }}
          >
            <span style={{ color: 'var(--color-ink-2)' }}>
              {reason(row.reason)}
              <span className="ml-2" style={{ color: 'var(--color-ink-faint)' }}>
                {label(row.bucket)}
              </span>
            </span>
            <span
              className="tnum"
              style={{ color: row.delta > 0 ? 'var(--color-success)' : 'var(--color-ink)' }}
            >
              {row.delta > 0 ? '+' : ''}
              {formatCredits(row.delta)}
            </span>
          </li>
        ))}
      </ul>

      {cursor && (
        <button
          type="button"
          onClick={() => void load(cursor)}
          disabled={loading}
          className="mt-3 text-xs"
          style={{ color: 'var(--color-ink-3)', textDecoration: 'underline' }}
        >
          {loading ? 'Loading…' : 'Show more'}
        </button>
      )}
    </section>
  )
}

function reason(code: string): string {
  switch (code) {
    case 'signup_grant':
      return 'Welcome credits'
    case 'promo_grant':
      return 'Promo code bonus'
    case 'plan_grant':
      return 'Monthly allowance'
    case 'plan_expiry':
      return 'Allowance expired'
    case 'topup_purchase':
      return 'Credits bought'
    case 'reserve':
      return 'Job started'
    case 'refund':
      return 'Refunded'
    case 'admin_grant':
      return 'Granted by support'
    case 'admin_adjust':
      return 'Adjusted by support'
    default:
      return code
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center px-6">{children}</main>
}
