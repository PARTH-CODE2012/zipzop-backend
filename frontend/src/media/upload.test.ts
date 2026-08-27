/**
 * The upload transfer, and the multipart path in particular.
 *
 * This file did not exist until 28 August, which is half of why the multipart
 * flow was broken on both sides at once: the backend passed an ETag where S3
 * wanted an upload id, and the frontend refused anything over 100 MB rather
 * than sending parts at all. Neither half had a test that went past the
 * reservation.
 *
 * `XMLHttpRequest` is stubbed rather than the transfer being injected: the
 * wiring between this module and XHR — which header goes on which request,
 * which response header the ETag is read from — is exactly what was wrong, so
 * a fake that stands in for XHR itself keeps that wiring under test.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { UploadError, uploadFile, type UploadProgress } from './upload'

const reserveUpload = vi.fn()
const completeUpload = vi.fn()

vi.mock('@/lib/api/endpoints', () => ({
  reserveUpload: (...args: unknown[]) => reserveUpload(...args),
  completeUpload: (...args: unknown[]) => completeUpload(...args),
}))

interface SentRequest {
  url: string
  size: number
  headers: Record<string, string>
}

/** What the fake answers with, decided per request by the test. */
type Responder = (request: SentRequest, index: number) => { status: number; etag?: string }

const sent: SentRequest[] = []
let respond: Responder = () => ({ status: 200, etag: '"part-etag"' })

class FakeXhr {
  upload: { onprogress: ((event: unknown) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  ontimeout: (() => void) | null = null
  status = 0

  #url = ''
  #headers: Record<string, string> = {}
  #etag: string | undefined

  open(_method: string, url: string): void {
    this.#url = url
  }

  setRequestHeader(name: string, value: string): void {
    this.#headers[name] = value
  }

  getResponseHeader(name: string): string | null {
    return name === 'ETag' ? (this.#etag ?? null) : null
  }

  send(body: Blob): void {
    const request = { url: this.#url, size: body.size, headers: { ...this.#headers } }
    const index = sent.length
    sent.push(request)
    queueMicrotask(() => {
      const outcome = respond(request, index)
      this.status = outcome.status
      this.#etag = outcome.etag
      if (outcome.status >= 200 && outcome.status < 300) {
        this.upload.onprogress?.({ lengthComputable: true, loaded: body.size, total: body.size })
      }
      this.onload?.()
    })
  }

  abort(): void {
    this.onabort?.()
  }
}

const PART_SIZE = 8 * 1024 * 1024

function multipartReservation(parts: number, urlSuffix = '') {
  return {
    assetId: 'ast_1',
    uploadUrl: 'https://storage.example/single',
    method: 'PUT',
    headers: { 'Content-Type': 'video/mp4' },
    expiresAt: '2026-08-28T12:00:00Z',
    multipart: {
      uploadId: 'upload-1',
      partSizeBytes: PART_SIZE,
      parts: Array.from({ length: parts }, (_, index) => ({
        partNumber: index + 1,
        url: `https://storage.example/part/${index + 1}${urlSuffix}`,
      })),
    },
  }
}

/** A file of `size` bytes whose content is irrelevant but whose length is not. */
function fileOf(size: number): File {
  return new File([new Uint8Array(size)], 'clip.mp4', { type: 'video/mp4' })
}

beforeEach(() => {
  sent.length = 0
  respond = () => ({ status: 200, etag: '"part-etag"' })
  reserveUpload.mockReset()
  completeUpload.mockReset()
  completeUpload.mockResolvedValue({ id: 'ast_1', status: 'probing' })
  vi.stubGlobal('XMLHttpRequest', FakeXhr)
})

describe('a multipart upload', () => {
  it('sends every part and completes with the ETags storage returned', async () => {
    const size = PART_SIZE + 4096
    reserveUpload.mockResolvedValue(multipartReservation(2))
    respond = (_request, index) => ({ status: 200, etag: `"etag-${index + 1}"` })

    await uploadFile(fileOf(size)).done

    expect(sent).toHaveLength(2)
    expect(sent[0]?.url).toBe('https://storage.example/part/1')
    expect(sent[1]?.url).toBe('https://storage.example/part/2')
    // Every byte announced, split on the boundary the server chose.
    expect(sent[0]?.size).toBe(PART_SIZE)
    expect(sent[1]?.size).toBe(4096)
    expect(sent[0]!.size + sent[1]!.size).toBe(size)

    expect(completeUpload).toHaveBeenCalledWith('ast_1', null, [
      { partNumber: 1, etag: '"etag-1"' },
      { partNumber: 2, etag: '"etag-2"' },
    ])
  })

  it('sends no Content-Type on a part', async () => {
    // The part URLs are signed without one, so sending it is a header outside
    // the signature — a 403 that reads like bad credentials.
    reserveUpload.mockResolvedValue(multipartReservation(1))

    await uploadFile(fileOf(1024)).done

    expect(sent[0]?.headers).toEqual({})
  })

  it('reports progress that never goes backwards and ends at the whole file', async () => {
    const size = PART_SIZE * 2 + 100
    reserveUpload.mockResolvedValue(multipartReservation(3))
    const seen: UploadProgress[] = []

    await uploadFile(fileOf(size), { onProgress: (p) => seen.push(p) }).done

    expect(seen.length).toBeGreaterThan(3)
    for (let i = 1; i < seen.length; i += 1) {
      expect(seen[i]!.bytesSent).toBeGreaterThanOrEqual(seen[i - 1]!.bytesSent)
    }
    expect(seen.at(-1)).toEqual({ fraction: 1, bytesSent: size, bytesTotal: size })
  })

  it('refreshes the part URLs when a signature has expired, and keeps the same upload', async () => {
    // A 2 GB upload does not finish inside the fifteen minutes its part URLs
    // are signed for. The reservation is asked for again under the *same*
    // idempotency key, so the server returns fresh URLs for the upload already
    // in progress rather than starting a second one.
    reserveUpload
      .mockResolvedValueOnce(multipartReservation(1))
      .mockResolvedValueOnce(multipartReservation(1, '?renewed'))

    let first = true
    respond = () => {
      if (first) {
        first = false
        return { status: 403 }
      }
      return { status: 200, etag: '"after-refresh"' }
    }

    await uploadFile(fileOf(1024)).done

    expect(reserveUpload).toHaveBeenCalledTimes(2)
    const firstKey = reserveUpload.mock.calls[0]?.[1]
    const secondKey = reserveUpload.mock.calls[1]?.[1]
    expect(secondKey).toBe(firstKey)

    expect(sent).toHaveLength(2)
    expect(sent[1]?.url).toBe('https://storage.example/part/1?renewed')
    expect(completeUpload).toHaveBeenCalledWith('ast_1', null, [
      { partNumber: 1, etag: '"after-refresh"' },
    ])
  })

  it('gives up on a part that keeps failing rather than completing a hole', async () => {
    reserveUpload.mockResolvedValue(multipartReservation(2))
    respond = (request) =>
      request.url.endsWith('/2') ? { status: 500 } : { status: 200, etag: '"ok"' }

    await expect(uploadFile(fileOf(PART_SIZE + 10)).done).rejects.toMatchObject({
      code: 'UPLOAD_REJECTED',
    })
    expect(completeUpload).not.toHaveBeenCalled()
  })

  it('refuses to complete when the bucket hides the ETag header', async () => {
    // The CORS failure mode: every part uploads fine and every ETag is null,
    // so completion would ask S3 to assemble parts it cannot identify.
    reserveUpload.mockResolvedValue(multipartReservation(1))
    respond = () => ({ status: 200 })

    await expect(uploadFile(fileOf(1024)).done).rejects.toBeInstanceOf(UploadError)
    expect(completeUpload).not.toHaveBeenCalled()
  })
})

describe('a single-PUT upload', () => {
  it('still goes up in one request with the signed Content-Type', async () => {
    reserveUpload.mockResolvedValue({
      assetId: 'ast_2',
      uploadUrl: 'https://storage.example/single',
      method: 'PUT',
      headers: { 'Content-Type': 'video/mp4' },
      expiresAt: '2026-08-28T12:00:00Z',
      multipart: null,
    })
    respond = () => ({ status: 200, etag: '"whole-file"' })

    await uploadFile(fileOf(2048)).done

    expect(sent).toHaveLength(1)
    expect(sent[0]?.url).toBe('https://storage.example/single')
    expect(sent[0]?.headers).toEqual({ 'Content-Type': 'video/mp4' })
    expect(completeUpload).toHaveBeenCalledWith('ast_2', '"whole-file"')
  })
})
