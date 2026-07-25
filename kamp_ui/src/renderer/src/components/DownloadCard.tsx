import React, { useState } from 'react'
import type { DownloadItem } from '../api/client'
import { artUrl } from '../api/client'
import { formatBytes } from '../utils/formatBytes'
import { useTooltip } from '../hooks/useTooltip'
import { RemoveFromQueueIcon, RetryIcon } from './TransportIcons'

/**
 * One row of the Downloads view (KAMP-569/570): artwork thumbnail + album name +
 * artist + file size, plus interactions (KAMP-570).
 *
 * - A `downloading` card shows a byte-progress bar along its bottom edge; the
 *   percent comes from the store's `downloadProgress` map (KAMP-436), passed in
 *   as `progress`. `undefined` → indeterminate pulse. No drag/buttons.
 * - A `queued` card is a pointer-events drag origin (via `onPointerDown`, whose
 *   handler lives in DownloadsView) and shows a Cancel (X) button.
 * - A `failed` card shows its `error_text` plus Retry + Cancel buttons.
 */
export function DownloadCard({
  item,
  progress,
  dropIdx,
  onPointerDown,
  onRetry,
  onCancel
}: {
  item: DownloadItem
  progress?: number
  dropIdx?: number
  onPointerDown?: (id: string, clientX: number, clientY: number) => void
  onRetry?: (id: string) => void
  onCancel?: (id: string) => void
}): React.JSX.Element {
  const tooltip = useTooltip()
  const [artFailed, setArtFailed] = useState(false)
  // Resolve art through the daemon's /api/v1/album-art endpoint (same-origin, so
  // the renderer CSP allows it, unlike the raw bcbits.com `artwork_ref` URL). The
  // endpoint serves cached Bandcamp CDN art for collection items via its
  // band_name/item_title fallback, even before the album is downloaded.
  const artSrc =
    item.album_artist && item.album_name ? artUrl(item.album_artist, item.album_name) : null
  const showArt = artSrc != null && !artFailed

  const size = formatBytes(item.size_bytes)
  const sizeLabel = size ? (item.size_is_estimate ? `~${size}` : size) : ''

  const isDownloading = item.status === 'downloading'
  const isQueued = item.status === 'queued'
  const isFailed = item.status === 'failed'
  const id = item.provider_item_id

  return (
    <div
      className={`download-card${isFailed ? ' download-card--failed' : ''}${
        isQueued ? ' download-card--draggable' : ''
      }`}
      // Pointer-events drag (KAMP-456/458): queued cards only. data-drop-idx lets
      // DownloadsView resolve the drop target from the cursor position.
      data-drop-idx={isQueued ? dropIdx : undefined}
      onPointerDown={
        isQueued && onPointerDown
          ? (e) => {
              if (e.button !== 0) return
              e.preventDefault()
              onPointerDown(id, e.clientX, e.clientY)
            }
          : undefined
      }
    >
      <div className="download-card-thumb">
        {showArt && <img src={artSrc ?? ''} alt="" onError={() => setArtFailed(true)} />}
      </div>
      <div className="download-card-info">
        <div className="download-card-title">{item.album_name || 'Unknown album'}</div>
        <div className="download-card-artist">{item.album_artist || 'Unknown artist'}</div>
        {isFailed && item.error_text && (
          <div className="download-card-error">{item.error_text}</div>
        )}
      </div>
      {sizeLabel && <div className="download-card-size">{sizeLabel}</div>}
      {/* Always rendered (empty on the downloading card) so its reserved width
          keeps the size column right-aligned across the three sections (KAMP-579). */}
      <div className="download-card-actions">
        {isFailed && onRetry && (
          <button
            className="download-card-btn"
            {...tooltip('Retry download')}
            aria-label="Retry download"
            // stopPropagation on pointerdown so the button doesn't start a card drag
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation()
              onRetry(id)
            }}
          >
            <RetryIcon size={15} />
          </button>
        )}
        {onCancel && (
          <button
            className="download-card-btn"
            {...tooltip('Remove from queue')}
            aria-label="Remove from queue"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation()
              onCancel(id)
            }}
          >
            <RemoveFromQueueIcon size={16} />
          </button>
        )}
      </div>
      {isDownloading && (
        <div
          className={`download-progress${
            typeof progress === 'number' ? '' : ' download-progress--indeterminate'
          }`}
        >
          <div
            className="download-progress-fill"
            style={typeof progress === 'number' ? { width: `${progress}%` } : undefined}
          />
        </div>
      )}
    </div>
  )
}
