import type { Metadata } from 'next'

import { ParityHarness } from './parity-harness'

export const metadata: Metadata = {
  title: 'LUT parity — M5',
  description: 'The preview grade over known colours, for comparison against the export renderer.',
}

/**
 * M5's closing condition, browser half.
 *
 * A `/spike` route for the same reason M1's compositor was: verification code
 * that is not part of the product and is not linked from anywhere. It exists so
 * `e2e/lut-parity.mjs` can measure the preview against the export renderer
 * rather than assert that they agree.
 */
export default function LutParityPage() {
  return <ParityHarness />
}
