/**
 * Money, as a person reads it.
 *
 * Every amount that crosses the API is in **minor units** — cents and paise,
 * never a float (contract §7, and `plans.price_usd_cents` in the schema). This
 * module is the only place that divides by a hundred, so a rounding mistake has
 * one place to be and one place to be fixed.
 */

/** Minor units per major unit. Both currencies we take use 100. */
const MINOR_PER_MAJOR = 100

const SYMBOLS: Record<string, string> = { USD: '$', INR: '₹' }

/**
 * `399, 'USD'` → `$3.99`. `0` → `Free`.
 *
 * Free is a word rather than `$0.00`, because a price of nothing is the thing
 * being said, and "$0.00" reads like a bug in a pricing table.
 */
export function price(minor: number, currency: string): string {
  if (minor === 0) return 'Free'
  const symbol = SYMBOLS[currency] ?? `${currency} `
  const major = minor / MINOR_PER_MAJOR
  // Whole amounts lose the decimals: ₹199, not ₹199.00. Anything with a
  // fractional part keeps both, so $3.99 never renders as $3.9. Thousands are
  // separated either way — `₹1999` beside `₹999` in a pricing table is a
  // moment of arithmetic the reader should not have to do.
  const text = major.toLocaleString('en-US', {
    minimumFractionDigits: Number.isInteger(major) ? 0 : 2,
    maximumFractionDigits: 2,
  })
  return `${symbol}${text}`
}

/** `399, 'USD'` → `$3.99/month`. The unit belongs next to the number. */
export function monthlyPrice(minor: number, currency: string): string {
  return minor === 0 ? 'Free' : `${price(minor, currency)}/month`
}

/** `1840` → `1,840`. Credits are counted, so they get thousands separators. */
export function credits(amount: number): string {
  return amount.toLocaleString('en-US')
}

/**
 * The date a plan's credits run out, in words a person can act on.
 *
 * Not a relative "in 12 days": the whole point of showing it is so somebody can
 * decide whether to spend the balance before then, and a date is what they will
 * compare against their own calendar.
 */
export function expiryDate(iso: string | null | undefined): string | null {
  if (!iso) return null
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return null
  // Pinned to en-GB rather than the viewer's locale. The product is in English
  // and nothing else in it is translated, so `expires 6 octobre 2026` inside an
  // English sentence reads as a bug rather than as a courtesy. Day-month-year
  // also avoids the 03/04 ambiguity that a numeric format would carry.
  return when.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
}
