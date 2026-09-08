/**
 * The billing decisions, without a DOM.
 *
 * The suite runs in `node` (see `vitest.config.ts`), so what is tested here is
 * what would actually be wrong: money rendered from minor units, the state
 * between paying and being on the plan, and whether a refusal leaves the user
 * somewhere to go.
 */

import { describe, expect, it } from 'vitest'

import {
  CONFIRM_TIMEOUT_MS,
  confirmMessage,
  confirmState,
  shouldKeepPolling,
} from './confirming'
import { credits, expiryDate, monthlyPrice, price } from './money'
import { paywallFor } from './paywall'

describe('money comes over the wire in minor units', () => {
  it('renders cents as dollars', () => {
    expect(price(399, 'USD')).toBe('$3.99')
    expect(price(1999, 'USD')).toBe('$19.99')
  })

  it('renders paise as rupees, without decimals when there are none', () => {
    // ₹199, not ₹199.00 — a whole rupee price with two zeros reads as a
    // template that was not finished.
    expect(price(19_900, 'INR')).toBe('₹199')
    expect(price(99_900, 'INR')).toBe('₹999')
  })

  it('separates thousands in a price too', () => {
    // ₹1999 sitting beside ₹999 in a pricing table is a moment of arithmetic
    // the reader should not have to do.
    expect(price(199_900, 'INR')).toBe('₹1,999')
    expect(price(299_900, 'INR')).toBe('₹2,999')
  })

  it('says Free rather than a zero', () => {
    // "$0.00" in a pricing table reads like a bug, and free is the thing being
    // said.
    expect(price(0, 'USD')).toBe('Free')
    expect(monthlyPrice(0, 'INR')).toBe('Free')
  })

  it('puts the unit next to the number', () => {
    expect(monthlyPrice(399, 'USD')).toBe('$3.99/month')
  })

  it('falls back to the code for a currency it has no symbol for', () => {
    expect(price(500, 'EUR')).toBe('EUR 5')
  })

  it('separates thousands in a credit balance', () => {
    expect(credits(1840)).toBe('1,840')
    expect(credits(300)).toBe('300')
  })

  it('gives a date rather than "in 12 days"', () => {
    // Somebody deciding whether to spend a balance before it expires compares
    // it against their own calendar.
    expect(expiryDate('2026-09-30T00:00:00Z')).toContain('2026')
    expect(expiryDate(null)).toBeNull()
    expect(expiryDate('not a date')).toBeNull()
  })
})

describe('the state between paying and being on the plan', () => {
  it('is still confirming while the plan has not changed', () => {
    // 🔴 The redirect is not proof of payment. A user can land on returnUrl by
    // pressing back, and the webhook is what actually activates the plan.
    const state = confirmState({ planBefore: 'free', planNow: 'free', elapsedMs: 1_000 })
    expect(state.kind).toBe('confirming')
    expect(shouldKeepPolling(state)).toBe(true)
  })

  it('is active the moment the plan changes', () => {
    const state = confirmState({ planBefore: 'free', planNow: 'beta', elapsedMs: 2_400 })
    expect(state).toEqual({ kind: 'active', plan: 'beta' })
    expect(shouldKeepPolling(state)).toBe(false)
  })

  it('stops polling after the timeout rather than forever', () => {
    const state = confirmState({
      planBefore: 'free',
      planNow: 'free',
      elapsedMs: CONFIRM_TIMEOUT_MS,
    })
    expect(state.kind).toBe('slow')
    expect(shouldKeepPolling(state)).toBe(false)
  })

  it('still reports success if the plan changed after the timeout', () => {
    // A late webhook is not a failed payment, and the ordering here decides
    // which of the two the user is told.
    const state = confirmState({
      planBefore: 'free',
      planNow: 'pro',
      elapsedMs: CONFIRM_TIMEOUT_MS * 3,
    })
    expect(state).toEqual({ kind: 'active', plan: 'pro' })
  })

  it('never calls a slow confirmation a failure', () => {
    // The money may well have moved. Telling somebody their payment failed when
    // it did not is how a chargeback starts.
    const message = confirmMessage({ kind: 'slow', elapsedMs: 40_000 })
    expect(message).not.toMatch(/fail|error|wrong|problem/i)
    expect(message).toMatch(/may already have gone through/i)
  })
})

describe('a refusal always leaves somewhere to go', () => {
  it('names the shortfall and points at credits', () => {
    const wall = paywallFor('INSUFFICIENT_CREDITS', { required: 22, available: 8 })
    expect(wall).not.toBeNull()
    expect(wall?.detail).toContain('22')
    expect(wall?.detail).toContain('8')
    expect(wall?.href).toBe('/settings/billing')
  })

  it('names the plan the server chose, not one hardcoded here', () => {
    // `requiredPlan` is read from the plans table server-side, so it is the
    // cheapest public plan that covers it. Naming a tier in the frontend would
    // have gone stale the day the beta plan arrived — and would have sent
    // somebody to a $19.99 plan for something $3.99 covers.
    const wall = paywallFor('PLAN_LIMIT_EXCEEDED', { requiredPlan: 'beta' })
    expect(wall?.detail).toContain('beta')
    expect(wall?.action).toContain('beta')
  })

  it('still gives a way out when the server named no plan', () => {
    const wall = paywallFor('PLAN_LIMIT_EXCEEDED', {})
    expect(wall?.href).toBe('/pricing')
    expect(wall?.action).toBeTruthy()
  })

  it('promises that nothing already made is lost', () => {
    // Running out mid-project must never block plain editing and never lose
    // work. Saying so is half of keeping it true.
    for (const code of ['INSUFFICIENT_CREDITS', 'PLAN_LIMIT_EXCEEDED', 'SUBSCRIPTION_REQUIRED']) {
      expect(paywallFor(code)?.detail).toMatch(/stays/i)
    }
  })

  it('gives every refusal an action and a destination', () => {
    for (const code of [
      'INSUFFICIENT_CREDITS',
      'PLAN_LIMIT_EXCEEDED',
      'FAIR_USE_EXCEEDED',
      'STORAGE_QUOTA_EXCEEDED',
      'SUBSCRIPTION_REQUIRED',
    ]) {
      const wall = paywallFor(code)
      expect(wall, code).not.toBeNull()
      expect(wall?.action, code).toBeTruthy()
      expect(wall?.href, code).toMatch(/^\//)
    }
  })

  it('does not invent a wall for a code it has no sentence for', () => {
    expect(paywallFor('SOMETHING_NEW')).toBeNull()
    expect(paywallFor(null)).toBeNull()
  })

  it('reads storage figures when the server sends them', () => {
    const wall = paywallFor('STORAGE_QUOTA_EXCEEDED', {
      usedBytes: 5 * 1024 ** 3,
      limitBytes: 5 * 1024 ** 3,
    })
    expect(wall?.detail).toContain('5.0 GB')
  })
})
