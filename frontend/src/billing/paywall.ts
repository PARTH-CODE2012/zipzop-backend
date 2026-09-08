/**
 * What to say when the account is the thing in the way.
 *
 * **No dead ends** — the M6 checklist's own words. Every refusal here names the
 * unblock *and* links to it: being told "not enough credits" with nothing to
 * press is a wall, and a wall in the middle of somebody's project is where they
 * decide the product is not worth it.
 *
 * Two rules the wording follows, both from the competitor research that
 * prompted this milestone (docs/20-m6-readiness.md §5):
 *
 * * **say the price before the click**, never after;
 * * **never block plain editing.** Running out of credits stops new *jobs*. It
 *   must not stop trimming, saving or exporting what is already rendered, and it
 *   must never lose work. Nothing in this module returns an action that leaves
 *   the editor.
 */

export interface Paywall {
  /** One line, in the user's terms. */
  title: string
  /** What can be done about it. Never blank — that is the dead end. */
  detail: string
  /** The label of the way out. */
  action: string
  /** Where the action goes. */
  href: string
}

const PRICING = '/pricing'
const BILLING = '/settings/billing'

/**
 * Turn an API error code into a way forward.
 *
 * `details.requiredPlan` comes from the server, which reads the plans table —
 * so the plan named here is the *cheapest public plan that covers it*, and it
 * follows a repricing without this file being edited. Naming a tier in the
 * frontend would have gone stale the day the `beta` plan arrived.
 */
export function paywallFor(
  code: string | null | undefined,
  details: Record<string, unknown> = {},
): Paywall | null {
  if (!code) return null

  switch (code) {
    case 'INSUFFICIENT_CREDITS': {
      const required = numberOr(details.required, null)
      const available = numberOr(details.available, null)
      const shortfall =
        required !== null && available !== null ? ` You need ${required} and have ${available}.` : ''
      return {
        title: 'Not enough credits for this',
        detail:
          `Credits pay for the work, not the app.${shortfall} ` +
          'Everything already in this project stays exactly as it is.',
        action: 'Add credits',
        href: BILLING,
      }
    }

    case 'PLAN_LIMIT_EXCEEDED': {
      const plan = stringOr(details.requiredPlan, null)
      return {
        title: 'Your plan does not include this',
        detail: plan
          ? `The ${plan} plan covers it. Everything else you have made stays available.`
          : 'A higher plan covers it. Everything else you have made stays available.',
        action: plan ? `See the ${plan} plan` : 'See the plans',
        href: PRICING,
      }
    }

    case 'FAIR_USE_EXCEEDED':
      return {
        title: 'This account has passed its monthly fair-use ceiling',
        detail:
          'That ceiling exists to catch runaway automation, not ordinary use. ' +
          'Get in touch and we will sort it out — usually the same day.',
        action: 'Contact us',
        href: BILLING,
      }

    case 'STORAGE_QUOTA_EXCEEDED': {
      const used = numberOr(details.usedBytes, null)
      const limit = numberOr(details.limitBytes, null)
      const figures =
        used !== null && limit !== null ? ` ${gigabytes(used)} of ${gigabytes(limit)} used.` : ''
      return {
        title: 'Your storage is full',
        detail: `Delete something you no longer need, or move to a larger plan.${figures}`,
        action: 'See the plans',
        href: PRICING,
      }
    }

    case 'SUBSCRIPTION_REQUIRED':
      return {
        title: 'This needs a paid plan',
        detail: 'Everything you have made stays available on the free plan.',
        action: 'See the plans',
        href: PRICING,
      }

    default:
      // A code with no sentence is still a dead end if it is swallowed. Showing
      // the bare code at least gives a support conversation something to go on.
      return null
  }
}

function numberOr(value: unknown, fallback: number | null): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function stringOr(value: unknown, fallback: string | null): string | null {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

function gigabytes(bytes: number): string {
  const gb = bytes / 1024 ** 3
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb)} GB`
}
