import React from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'

/**
 * Global download progress bar (KAMP-571): a thin, full-width bar rendered just
 * above the transport in every view while the download queue has work in flight.
 * Clicking it opens the Downloads view. It hides when the queue is idle.
 *
 * All progress math lives in the store: `downloadBatch.floor` is the batch-anchored
 * aggregate percent — completed items over the batch total, refined by the in-flight
 * item's byte-percent, ratcheted monotonically (0–99) and reset when the batch
 * drains (see store `setDownloadQueue` / `setAlbumProgress`). This component just
 * renders it, so it never fights the zustand/React re-render rules.
 */
export function GlobalDownloadBar(): React.JSX.Element | null {
  const batch = useStore((s) => s.downloadBatch)
  const queue = useStore((s) => s.downloadQueue)
  const setActiveView = useStore((s) => s.setActiveView)
  const tooltip = useTooltip()

  if (batch == null || batch.total === 0) return null

  const pct = batch.floor
  const activeCount = queue.filter(
    (i) => i.status === 'downloading' || i.status === 'queued'
  ).length
  // Nothing has reported measurable progress yet → indeterminate pulse instead of
  // a stuck 0% sliver.
  const indeterminate = pct <= 0

  return (
    <button
      type="button"
      className="global-download-bar"
      onClick={() => void setActiveView('downloads')}
      {...tooltip(`${activeCount} downloading — click to view`)}
      aria-label={`Downloads in progress (${activeCount}) — open Downloads view`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div
        className={`global-download-bar-fill${
          indeterminate ? ' global-download-bar-fill--indeterminate' : ''
        }`}
        style={indeterminate ? undefined : { width: `${pct}%` }}
      />
    </button>
  )
}
