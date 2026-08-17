'use client'

/**
 * The media bin: upload, ingest status, and the source of clips.
 *
 * Ingest is asynchronous — `POST /complete` returns `202` and the asset sits
 * in `probing` until the worker has built its four derivatives. The bin polls
 * while anything is unfinished and stops when nothing is, rather than polling
 * forever on a timer. M4 replaces the poll with the WebSocket; the shape of
 * this component does not change when it does.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { useEditor } from '@/editor/state/store'
import { ApiError } from '@/lib/api/client'
import { deleteMedia, listMedia, type AssetResponse } from '@/lib/api/endpoints'
import { formatBytes, uploadFile, type UploadPhase } from '@/media/upload'

const POLL_INTERVAL_MS = 2000

interface Transfer {
  name: string
  phase: UploadPhase
  fraction: number
  error?: string
}

export function MediaBin() {
  const [assets, setAssets] = useState<AssetResponse[]>([])
  const [transfer, setTransfer] = useState<Transfer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const addClip = useEditor((state) => state.addClip)

  const refresh = useCallback(async () => {
    try {
      const page = await listMedia({ limit: 50 })
      setAssets(page.items)
      return page.items
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not load your media.')
      return []
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Poll only while something is still being ingested. A timer that runs
  // regardless is a request every two seconds for as long as the tab is open.
  useEffect(() => {
    const unfinished = assets.some(
      (asset) => asset.status === 'probing' || asset.status === 'pending_upload',
    )
    if (!unfinished) return
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [assets, refresh])

  const onPick = useCallback(
    async (file: File) => {
      setError(null)
      setTransfer({ name: file.name, phase: 'reserving', fraction: 0 })

      const handle = uploadFile(file, {
        onPhase: (phase) => setTransfer((t) => (t ? { ...t, phase } : t)),
        onProgress: ({ fraction }) => setTransfer((t) => (t ? { ...t, fraction } : t)),
      })

      try {
        await handle.done
        setTransfer(null)
        await refresh()
      } catch (cause) {
        const message =
          cause instanceof ApiError || cause instanceof Error
            ? cause.message
            : 'The upload failed.'
        setTransfer((t) => (t ? { ...t, phase: 'error', error: message } : t))
      }
    },
    [refresh],
  )

  const onDelete = useCallback(
    async (asset: AssetResponse) => {
      try {
        await deleteMedia(asset.id)
        await refresh()
      } catch (cause) {
        // ASSET_IN_USE names the projects; the message the server wrote is
        // already the one to show (contract §3).
        setError(cause instanceof ApiError ? cause.message : 'Could not delete that file.')
      }
    },
    [refresh],
  )

  return (
    <div className="flex h-full flex-col gap-3 p-3 text-sm" data-testid="media-bin">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          className="rounded border px-3 py-1.5 text-xs"
          style={{ borderColor: 'var(--color-rule)', color: 'var(--color-ink)' }}
          data-testid="upload-button"
        >
          Add media
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="video/*,audio/*"
          className="hidden"
          data-testid="file-input"
          onChange={(event) => {
            const file = event.target.files?.[0]
            // Reset first, so picking the same file twice in a row still fires.
            event.target.value = ''
            if (file) void onPick(file)
          }}
        />
      </div>

      {transfer && (
        <div
          className="rounded border p-2 text-xs"
          style={{ borderColor: 'var(--color-rule)' }}
          data-testid="transfer"
          data-phase={transfer.phase}
          data-fraction={transfer.fraction.toFixed(3)}
        >
          <div className="truncate" style={{ color: 'var(--color-ink-2)' }}>
            {transfer.name}
          </div>
          {transfer.phase === 'error' ? (
            <div className="mt-1" role="alert">
              {transfer.error}
            </div>
          ) : (
            <>
              <div
                className="mt-1 h-1 w-full overflow-hidden rounded"
                style={{ background: 'var(--color-rule)' }}
              >
                <div
                  className="h-full"
                  style={{
                    width: `${Math.round(transfer.fraction * 100)}%`,
                    background: 'var(--color-accent)',
                  }}
                  data-testid="transfer-bar"
                />
              </div>
              <div className="mt-1" style={{ color: 'var(--color-ink-2)' }}>
                {transfer.phase === 'uploading'
                  ? `${Math.round(transfer.fraction * 100)}%`
                  : transfer.phase}
              </div>
            </>
          )}
        </div>
      )}

      {error && (
        <div className="text-xs" role="alert" data-testid="bin-error">
          {error}
        </div>
      )}

      <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto" data-testid="asset-list">
        {assets.map((asset) => (
          <li
            key={asset.id}
            className="rounded border p-2"
            style={{ borderColor: 'var(--color-rule)' }}
            data-testid="asset"
            data-asset-id={asset.id}
            data-status={asset.status}
            data-duration-ms={asset.durationMs ?? ''}
          >
            <div className="flex items-start gap-2">
              {asset.thumbnailUrl ? (
                /* A plain <img>, not next/image. The src is a presigned URL
                   that expires in an hour; the optimiser would cache and
                   rewrite it, and it cannot re-sign the link when it dies. */
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={asset.thumbnailUrl}
                  alt=""
                  className="h-10 w-16 shrink-0 rounded object-cover"
                  data-testid="asset-thumbnail"
                />
              ) : (
                <div
                  className="h-10 w-16 shrink-0 rounded"
                  style={{ background: 'var(--color-surface-2)' }}
                />
              )}

              <div className="min-w-0 flex-1">
                <div className="truncate text-xs">{asset.originalFilename}</div>
                <div className="text-[11px]" style={{ color: 'var(--color-ink-2)' }}>
                  <StatusLabel asset={asset} />
                </div>
              </div>
            </div>

            <div className="mt-2 flex gap-2">
              <button
                type="button"
                disabled={asset.status !== 'ready'}
                onClick={() =>
                  addClip({ assetId: asset.id, durationMs: asset.durationMs ?? 0 })
                }
                className="rounded border px-2 py-1 text-[11px] disabled:opacity-40"
                style={{ borderColor: 'var(--color-rule)' }}
                data-testid="add-to-timeline"
              >
                Add to timeline
              </button>
              <button
                type="button"
                onClick={() => void onDelete(asset)}
                className="rounded border px-2 py-1 text-[11px]"
                style={{ borderColor: 'var(--color-rule)' }}
                data-testid="delete-asset"
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StatusLabel({ asset }: { asset: AssetResponse }) {
  if (asset.status === 'failed') {
    // The server writes this sentence for the person who uploaded the file.
    return <span role="alert">{asset.failureReason ?? 'This file could not be read.'}</span>
  }
  if (asset.status !== 'ready') {
    return <span>{asset.status === 'probing' ? 'Preparing…' : 'Waiting for upload…'}</span>
  }
  const seconds = Math.round((asset.durationMs ?? 0) / 1000)
  const size = asset.sizeBytes ? ` · ${formatBytes(asset.sizeBytes)}` : ''
  const shape = asset.width && asset.height ? ` · ${asset.width}×${asset.height}` : ''
  return (
    <span>
      {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}
      {shape}
      {size}
    </span>
  )
}
