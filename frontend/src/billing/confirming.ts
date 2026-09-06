/**
 * The state between paying and being on the plan.
 *
 * 🔴 **The redirect is never proof of payment.** Contract §7:
 *
 * > The subscription is **not** active when the user comes back — it activates
 * > when the provider's webhook arrives, usually within seconds. On return,
 * > poll `GET /me` until `subscription.plan` changes, with a short "confirming
 * > your payment" state. Never assume success from the redirect alone: the user
 * > can land on `returnUrl` by pressing back.
 *
 * That last sentence is the whole reason this file exists. A client that
 * celebrates on arrival tells somebody who pressed back that they have bought a
 * plan they have not, and tells somebody whose webhook is thirty seconds late
 * that their payment failed.
 *
 * Pure and clock-free: the decisions are made here and the timer belongs to the
 * component, so both are testable without one.
 */

/** What the interface should be showing. */
export type ConfirmState =
  | { kind: 'idle' }
  /** Paid, waiting for the webhook. Show a calm progress line, not a spinner of doom. */
  | { kind: 'confirming'; elapsedMs: number }
  /** The plan changed. */
  | { kind: 'active'; plan: string }
  /**
   * Long enough that something is probably wrong — but the money may well have
   * moved, so this is never phrased as a failure.
   */
  | { kind: 'slow'; elapsedMs: number }

/**
 * How long to keep polling before saying so.
 *
 * The checklist asks for a 30-second fallback. Webhooks normally arrive within
 * a second or two; thirty gives a slow provider room without leaving somebody
 * staring at a spinner wondering whether they have been charged.
 */
export const CONFIRM_TIMEOUT_MS = 30_000

/**
 * How often to ask.
 *
 * Two seconds, not two hundred milliseconds. The webhook is what changes the
 * answer, so polling faster does not make it arrive sooner — it only multiplies
 * requests at the exact moment the system is already doing work for this user.
 */
export const POLL_INTERVAL_MS = 2_000

export interface Snapshot {
  /** The plan the account was on when checkout started. */
  planBefore: string
  /** The plan `GET /me` reports now. */
  planNow: string
  /** Milliseconds since the user came back. */
  elapsedMs: number
}

export function confirmState({ planBefore, planNow, elapsedMs }: Snapshot): ConfirmState {
  if (planNow !== planBefore) return { kind: 'active', plan: planNow }
  if (elapsedMs >= CONFIRM_TIMEOUT_MS) return { kind: 'slow', elapsedMs }
  return { kind: 'confirming', elapsedMs }
}

/** Stop polling once the answer cannot change any more. */
export function shouldKeepPolling(state: ConfirmState): boolean {
  return state.kind === 'confirming'
}

/**
 * What to say, and it is deliberately not an error.
 *
 * A payment that has not confirmed yet is not a failed payment, and telling
 * somebody it failed is how a support ticket and a chargeback start. The slow
 * case says what we know — the money may have left, we have not seen it yet,
 * the page will update itself — and gives them somewhere to go.
 */
export function confirmMessage(state: ConfirmState): string {
  switch (state.kind) {
    case 'idle':
      return ''
    case 'confirming':
      return 'Confirming your payment. This usually takes a few seconds.'
    case 'active':
      return `You are on the ${state.plan} plan.`
    case 'slow':
      return (
        'Still confirming. Your payment may already have gone through — this page ' +
        'updates itself when it lands, and nothing is charged twice. If it has not ' +
        'updated in a few minutes, get in touch and we will look.'
      )
  }
}
