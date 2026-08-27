/**
 * Uploading a file to S3, with progress the user can believe.
 *
 * `XMLHttpRequest`, not `fetch`. This is the one place in the application
 * where the older API is the right one: `fetch` has no upload progress event,
 * and the streaming-request workaround is Chromium-only and needs HTTP/2.
 * An upload bar that jumps from 0% to 100% is worse than none, because the
 * user cannot tell a slow upload from a stalled one.
 *
 * The PUT goes **straight to S3** — no `Authorization` header. Adding one
 * breaks the presigned signature, and the failure looks like bad credentials
 * rather than like the mistake it is (contract §3).
 */

import {
  completeUpload,
  reserveUpload,
  type AssetResponse,
  type CompletedPart,
  type UploadResponse,
} from '@/lib/api/endpoints'

export interface UploadProgress {
  /** 0 to 1. */
  fraction: number
  bytesSent: number
  bytesTotal: number
}

export type UploadPhase = 'reserving' | 'uploading' | 'completing' | 'done' | 'error'

export interface UploadHandle {
  /** Cancels the transfer. The reserved asset row is left for the sweep. */
  abort: () => void
  done: Promise<AssetResponse>
}

export class UploadError extends Error {
  readonly code: string
  /** The HTTP status storage answered with, or `undefined` if it never did. */
  readonly status: number | undefined
  constructor(code: string, message: string, status?: number) {
    super(message)
    this.name = 'UploadError'
    this.code = code
    this.status = status
  }
}

/** Attempts per part before giving up on the whole transfer. */
const PART_ATTEMPTS = 3

function putWithProgress(
  url: string,
  body: Blob,
  headers: Record<string, string>,
  onProgress: (progress: UploadProgress) => void,
): { promise: Promise<string | null>; abort: () => void } {
  const xhr = new XMLHttpRequest()

  const promise = new Promise<string | null>((resolve, reject) => {
    xhr.open('PUT', url, true)
    for (const [name, value] of Object.entries(headers)) {
      xhr.setRequestHeader(name, value)
    }

    xhr.upload.onprogress = (event) => {
      // `lengthComputable` is false on some proxies. Reporting a fraction we
      // do not have would make the bar lie; leaving it alone lets the caller
      // show an indeterminate state instead.
      if (!event.lengthComputable) return
      onProgress({
        fraction: event.total > 0 ? event.loaded / event.total : 0,
        bytesSent: event.loaded,
        bytesTotal: event.total,
      })
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        // S3 quotes its ETags. The value is passed back to /complete as-is.
        resolve(xhr.getResponseHeader('ETag'))
      } else {
        reject(
          new UploadError(
            'UPLOAD_REJECTED',
            `Storage refused the upload (${xhr.status}). The link may have expired.`,
            xhr.status,
          ),
        )
      }
    }
    xhr.onerror = () =>
      reject(new UploadError('NETWORK', 'The upload could not reach storage.'))
    xhr.onabort = () => reject(new UploadError('ABORTED', 'Upload cancelled.'))
    xhr.ontimeout = () => reject(new UploadError('TIMEOUT', 'The upload timed out.'))

    xhr.send(body)
  })

  return { promise, abort: () => xhr.abort() }
}

/**
 * Transfer a file the server has offered a multipart plan for.
 *
 * **One part at a time.** The point of multipart here is not throughput, it is
 * that a dropped connection costs one part rather than the whole file — the
 * failure a 2 GB upload over a phone connection actually hits. Uploading
 * several parts at once would be faster and is a deliberate later change; it
 * would also mean interleaving retries and URL refreshes across parts, which
 * is the part of this worth getting right first.
 *
 * **Part URLs expire in fifteen minutes and a large upload does not fit in
 * fifteen minutes.** When storage refuses a part with a 403, this asks for the
 * reservation again under the same idempotency key: the server returns fresh
 * URLs for the *same* upload id, so the parts already sent still count. That
 * is the whole reason the upload id lives on the asset row rather than being
 * minted fresh on every reservation.
 *
 * The ETag of each part has to be readable by JavaScript, which means the
 * bucket's CORS policy must list `ETag` under `ExposeHeaders`. MinIO does by
 * default; a real S3 bucket does not, and the symptom is a completion that
 * fails with every ETag `null` rather than anything about CORS.
 */
async function uploadInParts(
  file: File,
  plan: NonNullable<UploadResponse['multipart']>,
  refresh: () => Promise<UploadResponse>,
  onProgress: (progress: UploadProgress) => void,
  setAbort: (abort: () => void) => void,
): Promise<CompletedPart[]> {
  const partSize = plan.partSizeBytes
  const partNumbers = plan.parts.map((part) => part.partNumber)
  // Keyed by part number rather than held as an array, because a refresh
  // hands back a whole new plan and matching it up by position would quietly
  // depend on the server returning the parts in the same order twice.
  const urls = new Map(plan.parts.map((part) => [part.partNumber, part.url]))

  const completed: CompletedPart[] = []
  let bytesDone = 0

  for (const partNumber of partNumbers) {
    const start = (partNumber - 1) * partSize
    const chunk = file.slice(start, Math.min(start + partSize, file.size))

    let etag: string | null = null
    for (let attempt = 1; attempt <= PART_ATTEMPTS; attempt += 1) {
      const url = urls.get(partNumber)
      if (!url) {
        throw new UploadError('NO_PART_URL', `Storage offered no URL for part ${partNumber}.`)
      }

      // No Content-Type: the part URLs are signed without one, and sending it
      // would be a header outside the signature — the same 403 the single-PUT
      // path gets for sending the wrong one.
      const transfer = putWithProgress(url, chunk, {}, (progress) =>
        onProgress({
          fraction: file.size > 0 ? (bytesDone + progress.bytesSent) / file.size : 0,
          bytesSent: bytesDone + progress.bytesSent,
          bytesTotal: file.size,
        }),
      )
      setAbort(transfer.abort)

      try {
        etag = await transfer.promise
        break
      } catch (error) {
        const failure = error as UploadError
        // A cancelled upload is a decision, not a failure to retry.
        if (failure.code === 'ABORTED') throw failure
        if (attempt === PART_ATTEMPTS) throw failure
        if (failure.status === 403) {
          // Expired signature. New URLs, same upload id, parts already sent
          // still count.
          for (const part of (await refresh()).multipart?.parts ?? []) {
            urls.set(part.partNumber, part.url)
          }
        }
      }
    }

    if (!etag) {
      throw new UploadError(
        'NO_ETAG',
        `Storage did not return an identifier for part ${partNumber}. ` +
          'The bucket must expose the ETag header.',
      )
    }
    completed.push({ partNumber, etag })
    bytesDone += chunk.size
    onProgress({
      fraction: file.size > 0 ? bytesDone / file.size : 0,
      bytesSent: bytesDone,
      bytesTotal: file.size,
    })
  }

  return completed
}

/**
 * Reserve, transfer, complete.
 *
 * The idempotency key is generated once per call and reused if the caller
 * retries, so a reservation that succeeded before a network timeout does not
 * become a second asset (contract §1).
 */
export function uploadFile(
  file: File,
  callbacks: {
    onPhase?: (phase: UploadPhase) => void
    onProgress?: (progress: UploadProgress) => void
  } = {},
): UploadHandle {
  const { onPhase = () => {}, onProgress = () => {} } = callbacks
  let abortTransfer = () => {}

  const done = (async (): Promise<AssetResponse> => {
    onPhase('reserving')
    // One key for the whole call, so asking again for fresh part URLs returns
    // the reservation that already exists rather than making a second one.
    const idempotencyKey = crypto.randomUUID()
    const reserve = () =>
      reserveUpload(
        {
          filename: file.name,
          sizeBytes: file.size,
          contentType: file.type || 'application/octet-stream',
        },
        idempotencyKey,
      )
    const reservation = await reserve()

    onPhase('uploading')
    onProgress({ fraction: 0, bytesSent: 0, bytesTotal: file.size })

    if (reservation.multipart) {
      const parts = await uploadInParts(
        file,
        reservation.multipart,
        reserve,
        onProgress,
        (abort) => {
          abortTransfer = abort
        },
      )
      onPhase('completing')
      const asset = await completeUpload(reservation.assetId, null, parts)
      onPhase('done')
      return asset
    }

    const transfer = putWithProgress(
      reservation.uploadUrl,
      file,
      reservation.headers,
      onProgress,
    )
    abortTransfer = transfer.abort
    const etag = await transfer.promise

    onPhase('completing')
    const asset = await completeUpload(reservation.assetId, etag)
    onPhase('done')
    return asset
  })()

  // Without this, an abort before the reservation resolves produces an
  // unhandled rejection in the console and nothing else.
  done.catch(() => {})

  return { abort: () => abortTransfer(), done }
}

/** Human-readable size, for the media bin. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['kB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}
