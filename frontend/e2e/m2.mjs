/**
 * M2, end to end, in a real browser.
 *
 * *"Ends when: you can register, upload a real video, and see it as a clip
 * with a waveform, and scrub it smoothly."* — PHASE1-TASKS.md
 *
 * This drives Chromium over the DevTools protocol and checks the **result**,
 * not the page's opinion of itself: the file really goes to S3 through a
 * presigned URL, ffmpeg really runs, the waveform canvas is read back pixel by
 * pixel, and the proxy really decodes in a `<video>` element. That method is
 * what caught both of M1's bugs, neither of which was visible on screen.
 *
 * Software rendering (`--use-angle=swiftshader`) because headless Chromium
 * with hardware GL crashes the GPU process on this machine — an environment
 * quirk recorded in `src/editor/playback/README.md`, not a finding about the
 * compositor.
 *
 * Prerequisites, all of which `make e2e` starts for you:
 *   Postgres · Redis · MinIO · the API · a Celery worker · the Next dev server
 *
 * Usage:  node e2e/m2.mjs [--headful]
 */

import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as sleep } from 'node:timers/promises'

import { launch, openPage, waitFor } from './cdp.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const FIXTURE = join(HERE, 'fixture.mp4')
const APP = process.env.E2E_APP_URL ?? 'http://localhost:3000'
const API = process.env.E2E_API_URL ?? 'http://localhost:8000'

const headful = process.argv.includes('--headful')
const checks = []

function check(name, passed, detail = '') {
  checks.push({ name, passed, detail })
  const mark = passed ? '  ok  ' : ' FAIL '
  console.log(`[${mark}] ${name}${detail ? ` — ${detail}` : ''}`)
  return passed
}

async function main() {
  // Fail early and clearly rather than after a browser has launched.
  for (const [what, url] of [
    ['the API', `${API}/health/live`],
    ['the frontend', APP],
  ]) {
    const response = await fetch(url).catch(() => null)
    if (!response?.ok) throw new Error(`${what} is not answering at ${url}. Run \`make e2e-up\`.`)
  }

  const browser = await launch({
    headless: !headful,
    extraArgs: [
      '--use-angle=swiftshader',
      '--autoplay-policy=no-user-gesture-required',
      '--enable-features=NetworkService',
    ],
  })
  console.log(`chromium ${browser.version['Browser']}\n`)

  const page = await openPage(browser.port, 'about:blank')
  await page.send('Page.enable')
  await page.send('Runtime.enable')
  await page.send('DOM.enable')
  await page.send('Network.enable')

  // Every request the page makes, so the presigned PUT can be proven to have
  // gone straight to storage rather than through the API.
  const requests = []
  page.on('Network.requestWillBeSent', (p) =>
    requests.push({ url: p.request.url, method: p.request.method }),
  )
  const failures = []
  page.on('Runtime.exceptionThrown', (p) =>
    failures.push(p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text),
  )
  page.on('Runtime.consoleAPICalled', (p) => {
    if (p.type === 'error') failures.push(p.args?.map((a) => a.value ?? a.description).join(' '))
  })

  try {
    await run(page, requests)
  } finally {
    if (failures.length) {
      console.log('\npage errors:')
      for (const f of failures.slice(0, 10)) console.log('  ', String(f).slice(0, 300))
    }
    await browser.close()
  }

  const failed = checks.filter((c) => !c.passed)
  console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`)
  if (failed.length) {
    console.log('failed:')
    for (const f of failed) console.log('  -', f.name, f.detail)
    process.exit(1)
  }
}

async function run(page, requests) {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`

  // ---------------------------------------------------------------- 1. register
  await page.send('Page.navigate', { url: `${APP}/editor/e2e` })
  await waitFor(
    () => page.evaluate(`!!document.querySelector('[data-testid="auth-panel"]')`),
    { what: 'the sign-in panel', timeoutMs: 60_000 },
  )

  await page.evaluate(`
    (() => {
      document.querySelector('[data-testid="switch-mode"]').click()
      return true
    })()
  `)
  await waitFor(() =>
    page.evaluate(`document.querySelector('[data-testid="auth-panel"]').dataset.mode === 'register'`),
  )

  // Set values the way React notices — assigning .value directly does not fire
  // the synthetic change event and the state stays empty.
  await page.evaluate(`
    (() => {
      const set = (testid, value) => {
        const el = document.querySelector('[data-testid="' + testid + '"]')
        const proto = Object.getPrototypeOf(el)
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
        el.dispatchEvent(new Event('input', { bubbles: true }))
      }
      set('email', ${JSON.stringify(email)})
      set('display-name', 'E2E')
      set('password', 'hunter2hunter2')
      document.querySelector('[data-testid="submit"]').click()
      return true
    })()
  `)

  const workspace = await waitFor(
    () => page.evaluate(`!!document.querySelector('[data-testid="workspace"]')`),
    { what: 'the workspace after registering', timeoutMs: 30_000 },
  )
  check('registers a new account and lands in the editor', workspace === true)

  const shownEmail = await page.evaluate(
    `document.querySelector('[data-testid="account-email"]').textContent`,
  )
  check('shows the signed-in account', shownEmail === email, shownEmail)

  const credits = await page.evaluate(
    `document.querySelector('[data-testid="credits"]').textContent`,
  )
  check(
    'grants the free plan allowance at signup',
    credits.trim() === '300 credits',
    credits.trim(),
  )

  // The refresh token must not be reachable from script — that is the whole
  // reason it moved out of the response body and into an httpOnly cookie.
  const cookieVisible = await page.evaluate(`document.cookie.includes('zipzop_refresh')`)
  check('the refresh token is not readable by JavaScript', cookieVisible === false)

  // ------------------------------------------------------------------ 2. upload
  const { root } = await page.send('DOM.getDocument')
  const { nodeId } = await page.send('DOM.querySelector', {
    nodeId: root.nodeId,
    selector: '[data-testid="file-input"]',
  })
  if (!nodeId) throw new Error('no file input on the page')
  await page.send('DOM.setFileInputFiles', { nodeId, files: [FIXTURE] })

  // Progress has to be observed, not assumed: an upload bar that jumps 0 -> 100
  // is indistinguishable from one that never reported anything.
  const fractions = new Set()
  const sawUploading = await waitFor(
    async () => {
      const state = await page.evaluate(`
        (() => {
          const el = document.querySelector('[data-testid="transfer"]')
          return el ? { phase: el.dataset.phase, fraction: Number(el.dataset.fraction) } : null
        })()
      `)
      if (state) fractions.add(state.fraction)
      return state === null && fractions.size > 0 ? 'done' : state?.phase === 'uploading' ? true : false
    },
    { what: 'the upload to start', timeoutMs: 30_000, intervalMs: 40 },
  ).catch(() => false)

  check('reports upload progress from the browser', sawUploading !== false)

  const putToStorage = requests.filter(
    (r) => r.method === 'PUT' && r.url.includes('9000') && r.url.includes('X-Amz-Signature'),
  )
  check(
    'uploads straight to storage with a presigned URL',
    putToStorage.length === 1,
    `${putToStorage.length} signed PUT(s)`,
  )
  check(
    'the file never passes through the API',
    !requests.some((r) => r.method === 'PUT' && r.url.startsWith(`${API}/v1/media`)),
  )

  // ------------------------------------------------------------------ 3. ingest
  const asset = await waitFor(
    () =>
      page.evaluate(`
        (() => {
          const el = document.querySelector('[data-testid="asset"]')
          if (!el) return null
          return {
            id: el.dataset.assetId,
            status: el.dataset.status,
            durationMs: Number(el.dataset.durationMs || 0),
            hasThumb: !!el.querySelector('[data-testid="asset-thumbnail"]'),
          }
        })()
      `).then((a) => (a && a.status === 'ready' ? a : false)),
    { what: 'ingest to finish', timeoutMs: 120_000, intervalMs: 500 },
  )

  check('the worker ingests the upload to ready', asset.status === 'ready')
  check(
    'probes the duration in integer milliseconds',
    asset.durationMs === 6000,
    `${asset.durationMs} ms`,
  )
  check('generates a thumbnail', asset.hasThumb === true)

  // The three URLs are signed and expire in an hour (contract §3). Read from
  // the DOM rather than by calling the API: the access token lives in a module
  // closure and is deliberately not reachable from the page.
  const thumbSrc = await page.evaluate(
    `document.querySelector('[data-testid="asset-thumbnail"]').src`,
  )
  check(
    'serves media through signed URLs, not public ones',
    thumbSrc.includes('X-Amz-Signature'),
    thumbSrc.slice(0, 60) + '…',
  )

  // ---------------------------------------------------------------- 4. timeline
  await page.evaluate(`
    (() => { document.querySelector('[data-testid="add-to-timeline"]').click(); return true })()
  `)

  const clip = await waitFor(
    () =>
      page.evaluate(`
        (() => {
          const el = document.querySelector('[data-testid="clip"]')
          if (!el) return null
          const box = el.getBoundingClientRect()
          return {
            assetId: el.dataset.assetId,
            startMs: Number(el.dataset.startMs),
            durationMs: Number(el.dataset.durationMs),
            widthPx: Math.round(box.width),
          }
        })()
      `),
    { what: 'a clip on the timeline', timeoutMs: 20_000 },
  )

  check('puts the clip on the timeline', clip.assetId === asset.id)
  check(
    'the clip carries the probed duration',
    clip.durationMs === 6000 && clip.startMs === 0,
    `${clip.startMs}–${clip.durationMs} ms`,
  )
  // 6 s at the default 40 px/s is 240 px. A clip whose width does not follow
  // from the zoom means the time-to-pixel conversion is not being used.
  check('lays the clip out from milliseconds and zoom', Math.abs(clip.widthPx - 240) <= 2, `${clip.widthPx} px`)

  // ---------------------------------------------------------------- 5. waveform
  const waveform = await waitFor(
    async () => {
      const state = await page.evaluate(`
        (() => {
          const c = document.querySelector('[data-testid="waveform"]')
          if (!c || c.dataset.loaded !== 'true' || c.width === 0) return null
          const ctx = c.getContext('2d')
          const { data, width, height } = ctx.getImageData(0, 0, c.width, c.height)
          let painted = 0
          const columnsWithInk = new Set()
          for (let i = 3; i < data.length; i += 4) {
            if (data[i] > 8) {
              painted++
              columnsWithInk.add(Math.floor(((i - 3) / 4) % width))
            }
          }
          return { painted, width, height, columns: columnsWithInk.size }
        })()
      `)
      return state && state.painted > 0 ? state : false
    },
    { what: 'the waveform to draw', timeoutMs: 30_000, intervalMs: 250 },
  )

  check(
    'draws the waveform into a canvas',
    waveform.painted > 0,
    `${waveform.painted} lit pixels across ${waveform.width}×${waveform.height}`,
  )
  // A 440 Hz tone runs the whole clip, so ink must reach both ends. A waveform
  // that only paints the left edge is one that mapped its window wrongly.
  check(
    'the waveform spans the clip',
    waveform.columns >= waveform.width * 0.9,
    `${waveform.columns}/${waveform.width} columns`,
  )

  const domNodesPerPeak = await page.evaluate(
    `document.querySelectorAll('[data-testid="video-track"] *').length`,
  )
  // 6 s at 100 buckets a second is 600 peaks. Anything near that many elements
  // means the "never one DOM node per peak" rule was broken.
  check('never one DOM node per peak', domNodesPerPeak < 20, `${domNodesPerPeak} nodes`)

  // ------------------------------------------------------------- 6. the proxy
  const video = await waitFor(
    () =>
      page.evaluate(`
        (() => {
          const v = document.querySelector('video')
          if (!v) return null
          return {
            readyState: v.readyState,
            width: v.videoWidth,
            height: v.videoHeight,
            src: v.currentSrc,
            error: v.error ? v.error.code : null,
          }
        })()
      `).then((v) => (v && v.readyState >= 2 ? v : false)),
    { what: 'the proxy to decode in the browser', timeoutMs: 60_000, intervalMs: 250 },
  )

  check('decodes the real ingest proxy in the browser', video.readyState >= 2 && video.error === null)
  check(
    'the proxy is the 480p one, not the original',
    video.height === 480 && video.width === 854,
    `${video.width}×${video.height}`,
  )
  check('plays from a signed URL', video.src.includes('X-Amz-Signature'))

  // ------------------------------------------------------------- 7. scrubbing
  const lane = await page.evaluate(`
    (() => {
      const el = document.querySelector('[data-testid="timeline-lane"]')
      const b = el.getBoundingClientRect()
      return { x: b.x, y: b.y, w: b.width, h: b.height }
    })()
  `)

  const positions = []
  for (const fraction of [0.1, 0.3, 0.5, 0.7]) {
    const x = Math.round(lane.x + lane.w * fraction)
    const y = Math.round(lane.y + lane.h * 0.75)
    for (const type of ['mousePressed', 'mouseReleased']) {
      await page.send('Input.dispatchMouseEvent', {
        type,
        x,
        y,
        button: 'left',
        buttons: 1,
        clickCount: 1,
      })
    }
    await sleep(120)
    positions.push(
      await page.evaluate(
        `Number(document.querySelector('[data-testid="timeline"]').dataset.playheadMs)`,
      ),
    )
  }

  const increasing = positions.every((p, i) => i === 0 || p > positions[i - 1])
  check('scrubbing moves the playhead', increasing, positions.join(' → ') + ' ms')

  const readout = await page.evaluate(
    `document.querySelector('[data-testid="playhead-readout"]').textContent`,
  )
  check('the timecode reads in milliseconds', /^\d+:\d{2}\.\d{3}$/.test(readout.trim()), readout)

  // ------------------------------------------------------------- 8. playback
  // Rewind first. The scrub above deliberately dragged past the end of a
  // six-second clip, so the engine is sitting at the duration and the stats it
  // last published still say so — sampling straight after clicking play would
  // read that stale value and conclude playback finished instantly.
  await page.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Home',
    code: 'Home',
    windowsVirtualKeyCode: 36,
  })
  await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Home', code: 'Home' })
  await waitFor(
    () =>
      page.evaluate(
        `Number(document.querySelector('[data-testid="preview-stats"]').dataset.positionMs || 0) < 200`,
      ),
    { what: 'the playhead to rewind', timeoutMs: 15_000, intervalMs: 100 },
  )

  await page.evaluate(
    `(() => { document.querySelector('[data-testid="play"]').click(); return true })()`,
  )
  // Sampled throughout, not at one instant. The clip is six seconds and the
  // engine reports `hold` once it reaches the end, so a single reading taken
  // after playback finished says `hold` and proves nothing — the question is
  // what drove the clock *while it was running*.
  const samples = []
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    const stats = await page.evaluate(`
      (() => {
        const el = document.querySelector('[data-testid="preview-stats"]')
        return {
          fps: Number(el.dataset.fps || 0),
          driver: el.dataset.driver,
          clock: el.dataset.clock,
          skipped: Number(el.dataset.skippedDraws || 0),
          position: Number(el.dataset.positionMs || 0),
        }
      })()
    `)
    samples.push(stats)
    if (stats.position >= 5900) break
    await sleep(100)
  }

  const advanced = samples.some((s) => s.position > 200)
  const reachedEnd = samples.some((s) => s.position >= 5900)
  check(
    'plays the timeline through to the end',
    advanced && reachedEnd,
    `${samples.length} samples, last ${samples.at(-1)?.position} ms`,
  )

  const clocks = new Set(samples.map((s) => s.clock))
  check(
    'drives the clock from the media, not the wall',
    clocks.has('video'),
    [...clocks].join(', '),
  )
  check(
    'never falls back to the wall clock on a gapless timeline',
    !clocks.has('wall'),
    [...clocks].join(', '),
  )

  const worstSkipped = Math.max(...samples.map((s) => s.skipped))
  // A skipped draw is the M1 fix working — the renderer leaves the previous
  // frame up rather than painting black while a clip has no picture yet. A few
  // at startup are correct; a rising count during steady playback is not.
  check('does not keep skipping draws once playing', worstSkipped < 30, `${worstSkipped} skipped`)

  // ------------------------------------------------------- 9. session survives
  await page.send('Page.navigate', { url: `${APP}/editor/e2e` })
  const stillIn = await waitFor(
    () => page.evaluate(`!!document.querySelector('[data-testid="workspace"]')`),
    { what: 'the session to survive a reload', timeoutMs: 30_000 },
  ).catch(() => false)
  check('the httpOnly refresh cookie restores the session after a reload', stillIn === true)

  const assetSurvives = await waitFor(
    () => page.evaluate(`!!document.querySelector('[data-testid="asset"]')`),
    { what: 'the media bin to repopulate', timeoutMs: 20_000 },
  ).catch(() => false)
  check('the media bin reloads from the server', assetSurvives === true)

  // M2 deliberately does not persist the timeline — that is M3's whole title.
  const clipAfterReload = await page.evaluate(
    `document.querySelectorAll('[data-testid="clip"]').length`,
  )
  check(
    'the timeline is not persisted (M2 boundary, M3 adds it)',
    clipAfterReload === 0,
    `${clipAfterReload} clips`,
  )
}

main().catch((error) => {
  console.error('\nE2E failed:', error.message)
  process.exit(1)
})
