// Minimal Chrome DevTools Protocol driver — no dependencies.
// Used to verify the M1 compositor spike in a real browser with a real GPU.

import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

export async function launch({ port = 9222, headless = true, extraArgs = [], wrapper = null } = {}) {
  const profile = mkdtempSync(join(tmpdir(), 'zipzop-cdp-'))
  const args = [
    headless ? '--headless=new' : '--new-window',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-dev-shm-usage',
    '--hide-scrollbars',
    '--force-device-scale-factor=1',
    '--window-size=1400,1000',
    '--mute-audio',
    ...extraArgs,
    'about:blank',
  ]

  const command = wrapper === null ? 'chromium' : wrapper[0]
  const commandArgs = wrapper === null ? args : [...wrapper.slice(1), 'chromium', ...args]
  const child = spawn(command, commandArgs, { stdio: ['ignore', 'pipe', 'pipe'] })
  const stderr = []
  child.stderr.on('data', (d) => stderr.push(String(d)))

  let version = null
  for (let i = 0; i < 150; i++) {
    if (child.exitCode !== null) {
      throw new Error(`chromium exited ${child.exitCode}\n${stderr.join('')}`)
    }
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`)
      if (res.ok) {
        version = await res.json()
        break
      }
    } catch {
      /* not up yet */
    }
    await sleep(200)
  }
  if (version === null) throw new Error(`devtools never came up\n${stderr.join('')}`)

  return {
    version,
    port,
    stderr,
    async close() {
      try {
        await fetch(`http://127.0.0.1:${port}/json/close`).catch(() => {})
      } catch {
        /* ignore */
      }
      child.kill('SIGTERM')
      await sleep(400)
      if (child.exitCode === null) child.kill('SIGKILL')
      rmSync(profile, { recursive: true, force: true })
    },
  }
}

export async function openPage(port, url) {
  const res = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, {
    method: 'PUT',
  })
  if (!res.ok) throw new Error(`could not open a page: ${res.status} ${await res.text()}`)
  const target = await res.json()
  return connect(target.webSocketDebuggerUrl)
}

export function connect(wsUrl) {
  const ws = new WebSocket(wsUrl)
  const pending = new Map()
  const listeners = new Map()
  let nextId = 1

  const ready = new Promise((resolve, reject) => {
    ws.addEventListener('open', () => resolve(), { once: true })
    ws.addEventListener('error', (e) => reject(new Error(`websocket error: ${e.message ?? e}`)), {
      once: true,
    })
  })

  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id !== undefined) {
      const slot = pending.get(message.id)
      if (slot === undefined) return
      pending.delete(message.id)
      if (message.error !== undefined) slot.reject(new Error(JSON.stringify(message.error)))
      else slot.resolve(message.result)
      return
    }
    const handlers = listeners.get(message.method)
    if (handlers !== undefined) for (const handler of handlers) handler(message.params)
  })

  const send = async (method, params = {}) => {
    await ready
    const id = nextId++
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      ws.send(JSON.stringify({ id, method, params }))
    })
  }

  const on = (method, handler) => {
    const handlers = listeners.get(method) ?? []
    handlers.push(handler)
    listeners.set(method, handlers)
  }

  const evaluate = async (expression, { userGesture = false } = {}) => {
    const result = await send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture,
    })
    if (result.exceptionDetails !== undefined) {
      throw new Error(
        `page threw: ${result.exceptionDetails.exception?.description ?? result.exceptionDetails.text}`,
      )
    }
    return result.result.value
  }

  return { ws, ready, send, on, evaluate, close: () => ws.close() }
}

export async function waitFor(fn, { timeoutMs = 20_000, intervalMs = 100, what = 'condition' } = {}) {
  const deadline = Date.now() + timeoutMs
  let last
  while (Date.now() < deadline) {
    last = await fn()
    if (last) return last
    await sleep(intervalMs)
  }
  throw new Error(`timed out waiting for ${what} (last value: ${JSON.stringify(last)})`)
}
