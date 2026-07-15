import React from 'react'
import { useStore } from '../store'

/**
 * Downloads view (KAMP-568 scaffolding).
 *
 * This is the stub that wires the plumbing — the `activeView`/nav slot and the
 * `downloadQueue` store slice fed by the REST snapshot + `download.queue` WS
 * event. The real Now Downloading / Queued / Failed cards land in a later epic
 * step; for now this renders a minimal empty state (or a bare count so the live
 * wiring is observable).
 */
export function DownloadsView(): React.JSX.Element {
  const queue = useStore((s) => s.downloadQueue)

  if (queue.length === 0) {
    return (
      <div className="downloads-empty">
        <div className="downloads-empty-icon">⬇</div>
        <div className="downloads-empty-hint">Download queue is empty</div>
      </div>
    )
  }

  return (
    <div className="downloads-empty">
      <div className="downloads-empty-hint">
        {queue.length} download{queue.length === 1 ? '' : 's'} in the queue
      </div>
    </div>
  )
}
