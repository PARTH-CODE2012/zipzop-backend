import type { Metadata } from 'next'

import { CompositorSpike } from './compositor-spike'

export const metadata: Metadata = {
  title: 'Compositor spike — M1',
  description: 'Two clips, a cut, a LUT and a text overlay, composited in WebGL2.',
}

/**
 * M1, the compositor spike (PHASE1-TASKS.md).
 *
 * A throwaway route on purpose. It has no state management, no data fetching
 * and no relationship to the real editor — its whole job is to answer whether
 * a browser can play a timeline back at 60 fps before anything is built on top
 * of that assumption. The engine underneath it is written to be lifted into
 * `editor/playback/` in M2; this page is not.
 */
export default function CompositorSpikePage() {
  return <CompositorSpike />
}
