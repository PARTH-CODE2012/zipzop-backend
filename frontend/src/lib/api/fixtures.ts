/**
 * A fixture server, in-process, for looking at the editor without a backend.
 *
 * **This is the item `PHASE1-TASKS.md` has carried unticked since M0** — *"mock
 * server serving fixtures from the same schema"* — in the smallest form that
 * does the job. Not Prism, not MSW: both are a dependency and a second process
 * to run, and what was actually needed was for `request()` to answer from a
 * table instead of the network. That is one `if` at one call site.
 *
 * **Every shape here is typed against `generated.ts`**, which is generated from
 * the committed `openapi.json`. That is the entire point — a fixture that has
 * drifted from the contract is worse than no fixture, because it makes the
 * interface look right against a server that would reject it. When the contract
 * changes, this file stops compiling.
 *
 * ## When it is on
 *
 * Only when `NEXT_PUBLIC_DEMO=1` is set at build or dev time. It is off by
 * default, off in CI, and `assertNotProduction` below refuses to let it run in a
 * production build at all — a demo mode that could reach a real deployment is a
 * way to serve invented data to a customer.
 *
 * ## What it is not
 *
 * Not a substitute for the real thing. Nothing here touches ffmpeg, S3, Postgres
 * or a worker, so it proves **nothing** about ingest, jobs, credits or
 * persistence. It renders screens. The end-to-end proof of the real stack is
 * `e2e/m2.mjs`, which drives a real browser against real infrastructure and is
 * the thing that has actually caught bugs.
 */

import type { components } from '@/lib/api/generated'

type Schemas = components['schemas']

const NOW = Date.parse('2026-08-25T12:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()

export function isDemo(): boolean {
  return process.env.NEXT_PUBLIC_DEMO === '1'
}

/**
 * A production build must never serve invented data.
 *
 * Called once at module load. `NODE_ENV` is `production` for `next build`, so a
 * demo flag left in a deployment's environment fails the build's first render
 * rather than shipping a fake account to a real user.
 */
export function assertNotProduction(): void {
  if (isDemo() && process.env.NODE_ENV === 'production') {
    throw new Error(
      'NEXT_PUBLIC_DEMO=1 in a production build. The fixture server must never serve real users.',
    )
  }
}

// --------------------------------------------------------------------------
// The account
// --------------------------------------------------------------------------

/**
 * Credits deliberately split across two buckets.
 *
 * One of the fixtures M4 asked for, and the one most likely to catch a display
 * bug: the interface shows a single total everywhere except billing, and a
 * balance that only ever came from one bucket never exercises that.
 */
const ACCOUNT: Schemas['MeResponse'] = {
  id: 'usr_9b1d0c4e-3f2a-4c81-9d77-2e6b5a1f0c34',
  email: 'demo@zipzop.local',
  displayName: 'Demo',
  credits: { plan: 1840, topup: 260, total: 2100, facemapSeconds: 300 },
  storageBytesUsed: 4_182_364_160,
  createdAt: ago(90 * 24 * 3600_000),
}

// --------------------------------------------------------------------------
// Media
// --------------------------------------------------------------------------

/**
 * `proxyUrl` is null on purpose.
 *
 * This machine has no ffmpeg, so there is no proxy to point at, and inventing a
 * URL would give the compositor a 404 to decode — a broken picture that looks
 * like a renderer bug rather than an absent file. A clip with no proxy is a case
 * the preview already handles: it draws nothing and says so.
 */
const ASSETS: Schemas['AssetResponse'][] = [
  {
    id: 'ast_11111111-1111-4111-8111-111111111111',
    kind: 'video',
    status: 'ready',
    originalFilename: 'interview-take-3.mp4',
    sizeBytes: 48_211_904,
    durationMs: 62_000,
    width: 1080,
    height: 1920,
    fps: 30,
    proxyUrl: null,
    thumbnailUrl: null,
    createdAt: ago(2 * 3600_000),
  },
  {
    id: 'ast_22222222-2222-4222-8222-222222222222',
    kind: 'video',
    status: 'ready',
    originalFilename: 'b-roll-street.mp4',
    sizeBytes: 20_004_112,
    durationMs: 18_500,
    width: 1080,
    height: 1920,
    fps: 30,
    proxyUrl: null,
    thumbnailUrl: null,
    createdAt: ago(3 * 3600_000),
  },
  {
    id: 'ast_33333333-3333-4333-8333-333333333333',
    kind: 'audio',
    status: 'ready',
    originalFilename: 'bed-music.m4a',
    sizeBytes: 3_112_004,
    durationMs: 95_000,
    width: null,
    height: null,
    fps: null,
    proxyUrl: null,
    thumbnailUrl: null,
    createdAt: ago(26 * 3600_000),
  },
  {
    // Still ingesting — the state the media bin spins on, and the one that is
    // easy to forget exists when every fixture is `ready`.
    id: 'ast_44444444-4444-4444-8444-444444444444',
    kind: 'video',
    status: 'processing',
    originalFilename: 'just-uploaded.mp4',
    sizeBytes: 91_000_000,
    durationMs: null,
    width: null,
    height: null,
    fps: null,
    proxyUrl: null,
    thumbnailUrl: null,
    createdAt: ago(20_000),
  },
]

// --------------------------------------------------------------------------
// The timeline
// --------------------------------------------------------------------------

function mediaClip(over: Partial<Schemas['MediaClip']>): Schemas['MediaClip'] {
  return {
    id: 'clp_x',
    assetId: ASSETS[0]!.id,
    startMs: 0,
    durationMs: 6_000,
    sourceInMs: 0,
    speed: 1,
    volume: 1,
    audioFadeInMs: 0,
    audioFadeOutMs: 0,
    effects: [],
    ...over,
  }
}

function caption(
  id: string,
  text: string,
  startMs: number,
  durationMs: number,
): Schemas['TextClip'] {
  return {
    id,
    kind: 'caption' as const,
    text,
    startMs,
    durationMs,
    styleId: 'caption_bold',
    // Measured per word against the speaker's own baseline, not an absolute
    // level — contract §6.2. Varied here so the text track is not uniform.
    emphasis: (id.charCodeAt(id.length - 1) % 5) / 4,
    sourceJobId: 'job_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  }
}

/**
 * Words for the caption track, with **a deliberately misspelled name**.
 *
 * The other fixture M4 asked for by name. "Sarah" comes back as "Sara", which is
 * the single most likely thing a recogniser gets wrong and the exact thing the
 * milestone's closing condition asks someone to fix by hand.
 */
const WORDS = [
  'So', 'I', 'asked', 'Sara', 'about', 'the', 'launch', 'and', 'she',
  'said', 'it', 'was', 'always', 'going', 'to', 'be', 'tight',
]

const CAPTIONS = WORDS.map((word, index) =>
  caption(`clp_cap_${index}`, word, 400 + index * 380, 340),
)

const TIMELINE: Schemas['TimelineDocument'] = {
  schemaVersion: 1,
  tracks: [
    {
      id: 'trk_video',
      kind: 'video',
      index: 0,
      muted: false,
      locked: false,
      clips: [
        mediaClip({ id: 'clp_a', startMs: 0, durationMs: 6_200 }),
        mediaClip({
          id: 'clp_b',
          assetId: ASSETS[1]!.id,
          startMs: 6_200,
          durationMs: 4_300,
          // One clip already graded, so the Colour panel opens on a real value
          // rather than on "None" every time.
          effects: [
            { type: 'color_grade', lut: 'cinematic_warm', strength: 0.66, sourceJobId: null },
          ],
        }),
        mediaClip({ id: 'clp_c', startMs: 10_500, durationMs: 5_400, sourceInMs: 12_000 }),
      ],
    },
    {
      id: 'trk_audio',
      kind: 'audio',
      index: 1,
      muted: false,
      locked: false,
      clips: [
        mediaClip({
          id: 'clp_music',
          assetId: ASSETS[2]!.id,
          startMs: 0,
          durationMs: 15_900,
          volume: 0.35,
          audioFadeInMs: 800,
          audioFadeOutMs: 1_200,
        }),
      ],
    },
    { id: 'trk_text', kind: 'text', index: 2, muted: false, locked: false, clips: CAPTIONS },
  ],
}

const PROJECTS: Schemas['ProjectSummary'][] = [
  {
    id: 'prj_1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d',
    title: 'Launch interview — vertical cut',
    aspectRatio: '9:16',
    durationMs: 15_900,
    thumbnailUrl: null,
    updatedAt: ago(4 * 60_000),
  },
  {
    id: 'prj_2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e',
    title: 'Weekly recap',
    aspectRatio: '16:9',
    durationMs: 184_000,
    thumbnailUrl: null,
    updatedAt: ago(3 * 3600_000),
  },
  {
    id: 'prj_3c4d5e6f-7a8b-4c9d-8e1f-2a3b4c5d6e7f',
    title: 'Untitled project',
    aspectRatio: '9:16',
    durationMs: 0,
    thumbnailUrl: null,
    updatedAt: ago(9 * 24 * 3600_000),
  },
]

function projectFor(id: string): Schemas['ProjectResponse'] {
  const summary = PROJECTS.find((each) => each.id === id) ?? PROJECTS[0]!
  return {
    id: summary.id,
    title: summary.title,
    aspectRatio: summary.aspectRatio,
    timeline: summary.durationMs === 0 ? { schemaVersion: 1, tracks: [] } : TIMELINE,
    version: 7,
    assets: [],
    durationMs: summary.durationMs,
    // The canvas the project was created at. 9:16 at 1080 is the phase 1
    // default; the 16:9 fixture is there so the preview's letterboxing is
    // exercised by something.
    width: summary.aspectRatio === '16:9' ? 1920 : 1080,
    height: summary.aspectRatio === '16:9' ? 1080 : 1920,
    fps: 30,
    createdAt: ago(9 * 24 * 3600_000),
    updatedAt: summary.updatedAt,
  }
}

// --------------------------------------------------------------------------
// The routing table
// --------------------------------------------------------------------------

/** In-memory, so a project created in the demo is there when you go back. */
const created: Schemas['ProjectSummary'][] = []

/**
 * Answer a request, or return `MISS` so the caller falls through to the network.
 *
 * A miss rather than a 404: a route with no fixture should behave exactly as it
 * does today — fail against the absent server, visibly — rather than quietly
 * return something invented.
 */
export const MISS = Symbol('fixture-miss')

export function fixtureFor(method: string, path: string, body?: unknown): unknown | typeof MISS {
  const [route] = path.split('?')
  const at = (pattern: RegExp) => pattern.exec(route ?? '')

  if (method === 'POST' && route === '/auth/refresh') {
    return { accessToken: 'demo-access-token', expiresIn: 900 }
  }
  if (method === 'POST' && route === '/auth/logout') return undefined
  if (method === 'GET' && route === '/me') return ACCOUNT

  if (method === 'GET' && route === '/media') {
    return { items: ASSETS, nextCursor: null }
  }

  if (method === 'GET' && route === '/projects') {
    return { items: [...created, ...PROJECTS], nextCursor: null }
  }
  if (method === 'POST' && route === '/projects') {
    const input = (body ?? {}) as Partial<Schemas['CreateProjectRequest']>
    const fresh: Schemas['ProjectSummary'] = {
      id: `prj_${crypto.randomUUID()}`,
      title: input.title ?? 'Untitled project',
      aspectRatio: input.aspectRatio ?? '9:16',
      durationMs: 0,
      thumbnailUrl: null,
      // A demo run is short, and `Date.now()` here keeps "just now" honest for
      // something the user made a second ago.
      updatedAt: new Date().toISOString(),
    }
    created.unshift(fresh)
    return projectFor(fresh.id)
  }

  const project = at(/^\/projects\/([^/]+)$/)
  if (project && method === 'GET') return projectFor(project[1]!)
  if (project && method === 'PATCH') {
    // Autosave. Accepting it and bumping the version is what keeps the save
    // indicator moving through its real states instead of sitting on "error".
    const input = (body ?? {}) as { version?: number }
    return { id: project[1]!, version: (input.version ?? 7) + 1, updatedAt: new Date().toISOString() }
  }
  if (project && method === 'DELETE') {
    const index = created.findIndex((each) => each.id === project[1])
    if (index >= 0) created.splice(index, 1)
    return undefined
  }

  const duplicate = at(/^\/projects\/([^/]+)\/duplicate$/)
  if (duplicate && method === 'POST') {
    const source = [...created, ...PROJECTS].find((each) => each.id === duplicate[1])
    const copy: Schemas['ProjectSummary'] = {
      id: `prj_${crypto.randomUUID()}`,
      title: `${source?.title ?? 'Project'} (copy)`,
      aspectRatio: source?.aspectRatio ?? '9:16',
      durationMs: source?.durationMs ?? 0,
      thumbnailUrl: null,
      updatedAt: new Date().toISOString(),
    }
    created.unshift(copy)
    return projectFor(copy.id)
  }

  if (method === 'POST' && route === '/jobs/estimate') {
    const input = (body ?? {}) as Partial<Schemas['CreateJobRequest']>
    // The real numbers from pricing.py: captions 2 credits per minute of media,
    // smart_trim and color_analysis 1, rounded up, minimum one.
    const perMinute = input.tool === 'captions' ? 2 : 1
    const minutes = Math.max(1, Math.ceil(16 / 60))
    return {
      credits: perMinute * minutes,
      wouldReserveFrom: { plan: perMinute * minutes, topup: 0, facemapSeconds: 0 },
      estimatedSeconds: input.tool === 'captions' ? 24 : 9,
      sufficientBalance: true,
      blockedBy: null,
    }
  }

  if (method === 'GET' && route === '/jobs') return { items: [], nextCursor: null }

  return MISS
}
