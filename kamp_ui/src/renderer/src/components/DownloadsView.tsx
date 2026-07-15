import React from 'react'
import { useStore } from '../store'
import type { DownloadItem } from '../api/client'
import { DownloadCard } from './DownloadCard'

/**
 * Downloads view (KAMP-569): the download queue split into Now Downloading /
 * Queued / Failed sections. Read-only display — reorder/retry/cancel are KAMP-570.
 *
 * An empty section is not rendered at all (conditional render — no CSS height
 * animation, per CLAUDE.md). Two whole-view empty states gate ahead of the list:
 * no Downloads-capable service connected, then an empty queue.
 */
export function DownloadsView(): React.JSX.Element {
  const queue = useStore((s) => s.downloadQueue)
  const configValues = useStore((s) => s.configValues)
  const downloadProgress = useStore((s) => s.downloadProgress)

  // "Is any Downloads-capable service connected?" — today that is just Bandcamp.
  // configValues is null until loadConfig() resolves, so `?? false` treats the
  // not-yet-loaded state as disconnected.
  const bandcampConnected = configValues?.['bandcamp.connected'] ?? false

  if (!bandcampConnected) {
    return (
      <div className="downloads-empty">
        <div className="downloads-empty-icon">⬇</div>
        <div className="downloads-empty-hint">
          No services connected: sign in to a Service to start managing downloads
        </div>
      </div>
    )
  }

  if (queue.length === 0) {
    return (
      <div className="downloads-empty">
        <div className="downloads-empty-icon">⬇</div>
        <div className="downloads-empty-hint">Download queue is empty</div>
      </div>
    )
  }

  // The snapshot is already ordered downloading → queued (by position) → failed.
  const downloading = queue.filter((i) => i.status === 'downloading')
  const queued = queue.filter((i) => i.status === 'queued')
  const failed = queue.filter((i) => i.status === 'failed')

  const section = (
    title: string,
    items: DownloadItem[],
    withProgress = false
  ): React.JSX.Element | null =>
    items.length === 0 ? null : (
      <section className="downloads-section">
        <h2 className="downloads-section-title">{title}</h2>
        {items.map((item) => (
          <DownloadCard
            key={`${item.provider}:${item.provider_item_id}`}
            item={item}
            // downloadProgress (KAMP-436) is keyed by the bandcamp sale id, which
            // equals provider_item_id — so the lookup hits for the active download.
            progress={withProgress ? downloadProgress.get(item.provider_item_id) : undefined}
          />
        ))}
      </section>
    )

  return (
    <div className="downloads-view">
      {section('Now Downloading', downloading, true)}
      {section('Queued', queued)}
      {section('Failed', failed)}
    </div>
  )
}
