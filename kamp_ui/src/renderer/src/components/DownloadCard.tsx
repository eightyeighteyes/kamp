import React, { useState } from 'react'
import type { DownloadItem } from '../api/client'
import { artUrl } from '../api/client'
import { formatBytes } from '../utils/formatBytes'

/**
 * One row of the Downloads view (KAMP-569), list layout mirroring the library
 * `.module-list-row`: artwork thumbnail + album name + artist + file size.
 *
 * Read-only display — reorder/retry/cancel controls are KAMP-570.
 * - A `downloading` card shows a byte-progress bar along its bottom edge; the
 *   percent comes from the store's `downloadProgress` map (KAMP-436), passed in
 *   as `progress`. `undefined` → indeterminate pulse, not a 0% bar.
 * - A `failed` card shows its `error_text`.
 */
export function DownloadCard({
  item,
  progress
}: {
  item: DownloadItem
  progress?: number
}): React.JSX.Element {
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
  const isFailed = item.status === 'failed'

  return (
    <div className={`download-card${isFailed ? ' download-card--failed' : ''}`}>
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
