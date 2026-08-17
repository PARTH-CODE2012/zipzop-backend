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

import { completeUpload, reserveUpload, type AssetResponse } from '@/lib/api/endpoints'

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
  constructor(code: string, message: string) {
    super(message)
    this.name = 'UploadError'
    this.code = code
  }
}

function putWithProgress(
  url: string,
  file: File,
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
          ),
        )
      }
    }
    xhr.onerror = () =>
      reject(new UploadError('NETWORK', 'The upload could not reach storage.'))
    xhr.onabort = () => reject(new UploadError('ABORTED', 'Upload cancelled.'))
    xhr.ontimeout = () => reject(new UploadError('TIMEOUT', 'The upload timed out.'))

    xhr.send(file)
  })

  return { promise, abort: () => xhr.abort() }
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
    const reservation = await reserveUpload(
      {
        filename: file.name,
        sizeBytes: file.size,
        contentType: file.type || 'application/octet-stream',
      },
      crypto.randomUUID(),
    )

    if (reservation.multipart) {
      // Files over 100 MB come back with per-part URLs. M2's interface caps
      // uploads below that; wiring the multi-part transfer is a task of its
      // own and shipping a half-done one would fail silently at 101 MB.
      throw new UploadError(
        'MULTIPART_NOT_IMPLEMENTED',
        'Files over 100 MB are not supported by this build yet.',
      )
    }

    onPhase('uploading')
    onProgress({ fraction: 0, bytesSent: 0, bytesTotal: file.size })

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
