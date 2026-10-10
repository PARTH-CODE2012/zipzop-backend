import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RECONNECT_DELAYS_MS, TOKEN_EXPIRED_CLOSE, openJobStream } from './job-watch'

/**
 * The socket's life cycle since M7: opened with a one-time ticket (M7-19),
 * closed by the server when the token behind it expires (M7-18), and brought
 * back by the client — at once after a 4001, with backoff after anything else,
 * never for an account that can no longer get a ticket.
 */

class FakeSocket {
  static made: FakeSocket[] = []
  onopen: ((event: unknown) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  closedByClient = false

  constructor(readonly url: string) {
    FakeSocket.made.push(this)
  }

  open() {
    this.onopen?.({})
  }

  serverClose(code: number) {
    this.onclose?.({ code })
  }

  send(data: unknown) {
    this.onmessage?.({ data })
  }

  close() {
    this.closedByClient = true
    this.onclose?.({ code: 1000 })
  }
}

const createSocket = (url: string) => new FakeSocket(url) as unknown as WebSocket
const last = (): FakeSocket => {
  const socket = FakeSocket.made.at(-1)
  if (!socket) throw new Error('no socket was opened')
  return socket
}

let tickets: number
const getTicket = vi.fn(async () => `ticket-${++tickets}`)

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-05T12:00:00Z'))
  FakeSocket.made = []
  tickets = 0
  getTicket.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('openJobStream', () => {
  it('opens with a fresh ticket in the URL, never an access token', async () => {
    const stream = openJobStream(getTicket, () => {}, { baseUrl: 'https://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(0)

    expect(getTicket).toHaveBeenCalledTimes(1)
    expect(last().url).toBe('wss://api.test/v1/ws?ticket=ticket-1')
    expect(last().url).not.toContain('token=')
    stream.close()
  })

  it('reconnects at once when the server closes at token expiry', async () => {
    const stream = openJobStream(getTicket, () => {}, { baseUrl: 'http://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(0)
    last().open()

    // Fifteen good minutes, then the server's 4001.
    await vi.advanceTimersByTimeAsync(15 * 60_000)
    last().serverClose(TOKEN_EXPIRED_CLOSE)
    await vi.advanceTimersByTimeAsync(0)

    expect(FakeSocket.made).toHaveLength(2)
    expect(last().url).toBe('ws://api.test/v1/ws?ticket=ticket-2')
    stream.close()
  })

  it('backs off instead of spinning when a 4001 arrives the instant it opens', async () => {
    const stream = openJobStream(getTicket, () => {}, { baseUrl: 'http://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(0)

    last().open()
    last().serverClose(TOKEN_EXPIRED_CLOSE)
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeSocket.made).toHaveLength(2) // the first one is free

    last().open()
    last().serverClose(TOKEN_EXPIRED_CLOSE)
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeSocket.made).toHaveLength(2) // the second one waits
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[1])
    expect(FakeSocket.made).toHaveLength(3)
    stream.close()
  })

  it('comes back after a dropped connection, with growing waits', async () => {
    const stream = openJobStream(getTicket, () => {}, { baseUrl: 'http://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(0)

    last().serverClose(1006)
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[0] - 1)
    expect(FakeSocket.made).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.made).toHaveLength(2)

    last().serverClose(1006)
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[1] - 1)
    expect(FakeSocket.made).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.made).toHaveLength(3)
    stream.close()
  })

  it.each([401, 403])('stops for good when the ticket is refused with %i', async (status) => {
    const refused = vi.fn(async () => {
      throw Object.assign(new Error('no'), { status })
    })
    const stream = openJobStream(refused, () => {}, { baseUrl: 'http://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(60_000)

    expect(refused).toHaveBeenCalledTimes(1)
    expect(FakeSocket.made).toHaveLength(0)
    stream.close()
  })

  it('keeps trying while the ticket call fails for any other reason', async () => {
    let calls = 0
    const flaky = vi.fn(async () => {
      calls += 1
      if (calls === 1) throw Object.assign(new Error('offline'), { status: 0 })
      return 'ticket-after-outage'
    })
    const stream = openJobStream(flaky, () => {}, { baseUrl: 'http://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[0])

    expect(last().url).toContain('ticket=ticket-after-outage')
    stream.close()
  })

  it('stays closed once the caller closes it', async () => {
    const stream = openJobStream(getTicket, () => {}, { baseUrl: 'http://api.test/v1', createSocket })
    await vi.advanceTimersByTimeAsync(0)
    last().open()

    stream.close()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(FakeSocket.made).toHaveLength(1)
    expect(FakeSocket.made[0]?.closedByClient).toBe(true)
  })

  it('turns events into hints and ignores the heartbeat', async () => {
    const hints: string[] = []
    const stream = openJobStream(getTicket, (jobId) => hints.push(jobId), {
      baseUrl: 'http://api.test/v1',
      createSocket,
    })
    await vi.advanceTimersByTimeAsync(0)
    last().open()

    last().send('{"type":"ping"}')
    last().send('{"type":"job.progress","jobId":"job_1","progress":40}')
    last().send('not json')

    expect(hints).toEqual(['job_1'])
    stream.close()
  })
})
