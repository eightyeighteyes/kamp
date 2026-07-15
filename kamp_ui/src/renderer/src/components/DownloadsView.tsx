import React, { useRef } from 'react'
import { useStore } from '../store'
import { DownloadCard } from './DownloadCard'
import { computeNewOrder } from '../utils/computeNewOrder'

/**
 * Downloads view (KAMP-569/570): the download queue split into Now Downloading /
 * Queued / Failed sections, with interactions.
 *
 * An empty section is not rendered at all (conditional render — no CSS height
 * animation, per CLAUDE.md). Two whole-view empty states gate ahead of the list:
 * no Downloads-capable service connected, then an empty queue.
 *
 * Queued cards drag to reorder via **pointer events** (not HTML5 drag), so Escape
 * cancels instantly (KAMP-456/458): document pointermove/pointerup/keydown are
 * wired here on pointerdown and torn down together; the reorder only runs on
 * pointerup. Retry/Cancel are optimistic store actions reconciled by the WS
 * `download.queue` snapshot.
 */
export function DownloadsView(): React.JSX.Element {
  const queue = useStore((s) => s.downloadQueue)
  const configValues = useStore((s) => s.configValues)
  const downloadProgress = useStore((s) => s.downloadProgress)
  const reorderDownloadQueue = useStore((s) => s.reorderDownloadQueue)
  const retryDownload = useStore((s) => s.retryDownload)
  const cancelDownload = useStore((s) => s.cancelDownload)

  // Currently highlighted drop-indicator element (avoids a DOM query on clear).
  const activeDropRef = useRef<HTMLElement | null>(null)
  const queuedSectionRef = useRef<HTMLElement | null>(null)

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

  // --- Pointer-events drag reorder (queued cards only) ----------------------
  const clearDropIndicator = (): void => {
    const el = activeDropRef.current
    if (el) {
      el.classList.remove('download-card--drop-above', 'download-card--drop-below')
      activeDropRef.current = null
    }
  }

  const cardUnder = (x: number, y: number): HTMLElement | null => {
    const el = document.elementFromPoint(x, y)
    return (el?.closest('.download-card[data-drop-idx]') as HTMLElement | null) ?? null
  }

  const updateDropIndicator = (x: number, y: number): void => {
    clearDropIndicator()
    const card = cardUnder(x, y)
    if (!card) return
    const rect = card.getBoundingClientRect()
    const cls =
      y < rect.top + rect.height / 2 ? 'download-card--drop-above' : 'download-card--drop-below'
    card.classList.add(cls)
    activeDropRef.current = card
  }

  const resolveDropIdx = (x: number, y: number): number | null => {
    const card = cardUnder(x, y)
    if (!card) {
      // Dropped in the Queued section's empty area → tail.
      const sec = queuedSectionRef.current
      if (sec) {
        const r = sec.getBoundingClientRect()
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return queued.length
      }
      return null
    }
    const idx = parseInt(card.dataset.dropIdx ?? '')
    if (isNaN(idx)) return null
    const rect = card.getBoundingClientRect()
    return y < rect.top + rect.height / 2 ? idx : idx + 1
  }

  const handleQueuedPointerDown = (id: string, startX: number, startY: number): void => {
    const draggedIdx = queued.findIndex((i) => i.provider_item_id === id)
    if (draggedIdx < 0) return
    let dragStarted = false
    let ghost: HTMLDivElement | null = null

    const onMove = (ev: PointerEvent): void => {
      if (!dragStarted) {
        if (Math.abs(ev.clientX - startX) < 4 && Math.abs(ev.clientY - startY) < 4) return
        dragStarted = true
        ghost = document.createElement('div')
        ghost.textContent = queued[draggedIdx].album_name || 'Download'
        ghost.style.cssText =
          'position:fixed;top:-100px;left:-100px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:var(--accent);color:#fff;padding:4px 10px;border-radius:3px;font-size:12px;font-weight:600;pointer-events:none;z-index:9999'
        document.body.appendChild(ghost)
      }
      if (ghost) {
        ghost.style.left = `${ev.clientX + 12}px`
        ghost.style.top = `${ev.clientY - 12}px`
      }
      updateDropIndicator(ev.clientX, ev.clientY)
    }

    const cleanup = (): void => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('keydown', onEscape)
      if (ghost) {
        document.body.removeChild(ghost)
        ghost = null
      }
      clearDropIndicator()
    }

    const onUp = (ev: PointerEvent): void => {
      const wasDrag = dragStarted
      cleanup()
      if (!wasDrag) return
      const dropIdx = resolveDropIdx(ev.clientX, ev.clientY)
      if (dropIdx === null) return
      const order = computeNewOrder(queued.length, [draggedIdx], dropIdx)
      const orderedIds = order.map((i) => queued[i].provider_item_id)
      void reorderDownloadQueue(orderedIds)
    }

    // Escape cancels the drag immediately (real DOM listener; no reorder runs).
    const onEscape = (ev: KeyboardEvent): void => {
      if (ev.key === 'Escape') cleanup()
    }

    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('keydown', onEscape)
  }

  return (
    <div className="downloads-view">
      {downloading.length > 0 && (
        <section className="downloads-section">
          <h2 className="downloads-section-title">Now Downloading</h2>
          {downloading.map((item) => (
            <DownloadCard
              key={`${item.provider}:${item.provider_item_id}`}
              item={item}
              // downloadProgress (KAMP-436) is keyed by the bandcamp sale id, which
              // equals provider_item_id — so the lookup hits for the active download.
              progress={downloadProgress.get(item.provider_item_id)}
            />
          ))}
        </section>
      )}
      {queued.length > 0 && (
        <section className="downloads-section" ref={queuedSectionRef}>
          <h2 className="downloads-section-title">Queued</h2>
          {queued.map((item, idx) => (
            <DownloadCard
              key={`${item.provider}:${item.provider_item_id}`}
              item={item}
              dropIdx={idx}
              onPointerDown={handleQueuedPointerDown}
              onCancel={cancelDownload}
            />
          ))}
        </section>
      )}
      {failed.length > 0 && (
        <section className="downloads-section">
          <h2 className="downloads-section-title">Failed</h2>
          {failed.map((item) => (
            <DownloadCard
              key={`${item.provider}:${item.provider_item_id}`}
              item={item}
              onRetry={retryDownload}
              onCancel={cancelDownload}
            />
          ))}
        </section>
      )}
    </div>
  )
}
