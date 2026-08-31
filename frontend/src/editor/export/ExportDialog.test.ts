/**
 * The export dialog's decisions, without a DOM.
 *
 * The component is React and the suite runs in `node` — deliberately, per
 * `vitest.config.ts`. What is worth testing here is not that a button renders:
 * it is that a `blockedBy` code becomes a sentence a person can act on, and
 * that the resolution list matches the ceilings the server actually enforces.
 * Both are pure, and both are the parts that would be wrong.
 */

import { describe, expect, it } from 'vitest'

import { ASPECTS, RESOLUTIONS, blockedReason } from './ExportDialog'
import type { EstimateResponse } from '@/lib/api/endpoints'

function estimate(over: Partial<EstimateResponse> = {}): EstimateResponse {
  return {
    credits: 4,
    wouldReserveFrom: { plan: 4, topup: 0, facemapSeconds: 0 },
    estimatedSeconds: 45,
    sufficientBalance: true,
    blockedBy: null,
    ...over,
  } as EstimateResponse
}

describe('what blocks an export', () => {
  it('says nothing when nothing is blocking', () => {
    expect(blockedReason(estimate())).toBeNull()
  })

  it('explains a plan ceiling rather than only disabling the button', () => {
    // A greyed-out 4K option with no explanation is indistinguishable from a
    // broken one, and the server already knows which plan is needed.
    expect(blockedReason(estimate({ blockedBy: 'PLAN_LIMIT_EXCEEDED' }))).toMatch(/plan/i)
  })

  it('distinguishes running out of credits from not being allowed', () => {
    // Different problems with different fixes: one is a purchase, the other an
    // upgrade. Collapsing them into "cannot export" sends people to the wrong
    // page.
    const credits = blockedReason(estimate({ blockedBy: 'INSUFFICIENT_CREDITS' }))
    const plan = blockedReason(estimate({ blockedBy: 'PLAN_LIMIT_EXCEEDED' }))
    expect(credits).not.toEqual(plan)
    expect(credits).toMatch(/credits/i)
  })

  it('shows a code it has no sentence for rather than swallowing it', () => {
    // An unexplained disabled button is worse than a bare code — at least the
    // code can be searched for.
    expect(blockedReason(estimate({ blockedBy: 'SOMETHING_NEW' }))).toBe('SOMETHING_NEW')
  })

  it('treats a missing estimate as not-yet-known, not as blocked', () => {
    expect(blockedReason(null)).toBeNull()
  })
})

describe('the presets offered', () => {
  it('offers exactly the heights the plan ceilings are expressed in', () => {
    // `plans.max_export_height` is seeded 720, 1080 and 2160. Offering a
    // resolution no ceiling refers to would be a tier invented in the client.
    expect(RESOLUTIONS.map((r) => r.height)).toEqual([720, 1080, 2160])
  })

  it('leads with vertical, because the product is short-form', () => {
    expect(ASPECTS[0]?.value).toBe('9:16')
  })

  it('labels 2160p as 4K, which is what people call it', () => {
    expect(RESOLUTIONS.find((r) => r.value === '2160p')?.label).toBe('4K')
  })
})
