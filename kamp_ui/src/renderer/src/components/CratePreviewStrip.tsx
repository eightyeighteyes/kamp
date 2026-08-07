// The in-card preview mini-player (KAMP-651).
//
// Deliberately its own strip rather than reusing the TransportBar: the whole
// promise of preview is that the main transport keeps showing the user's own
// queue, untouched. Two players on screen is the point, not a redundancy.
//
// Position is interpolated locally. The daemon publishes on transitions only —
// a sample plus the moment it was taken — so the bar can move smoothly without
// the daemon broadcasting several times a second.
import React, { useEffect, useState } from 'react'
import type { PreviewState } from '../api/client'
import { formatClock } from '../utils/formatClock'

export function CratePreviewStrip({
  preview,
  onToggle,
  onStep,
  onSeek
}: {
  preview: PreviewState
  onToggle: () => void
  onStep: (delta: number) => void
  onSeek: (position: number) => void
}): React.JSX.Element {
  const [now, setNow] = useState(() => Date.now() / 1000)

  // Tick only while actually playing — a paused or buffering preview has a
  // fixed position, and a timer running against nothing is just wakeups.
  useEffect(() => {
    if (preview.state !== 'playing' || preview.buffering) return
    const id = window.setInterval(() => setNow(Date.now() / 1000), 250)
    return () => window.clearInterval(id)
  }, [preview.state, preview.buffering])

  // Extrapolate from the last sample. Clamped to the track length so a stalled
  // stream cannot march the bar off the end — the daemon's next transition
  // corrects it.
  const elapsed =
    preview.state === 'playing' && !preview.buffering
      ? Math.max(0, now - preview.position_updated_at)
      : 0
  const position = Math.min(preview.position + elapsed, preview.duration || Infinity)
  const pct = preview.duration > 0 ? Math.min(100, (position / preview.duration) * 100) : 0

  const playing = preview.state === 'playing'
  const preparing = preview.state === 'preparing' || preview.buffering

  return (
    <div className="crate-preview" role="group" aria-label="Preview">
      <div className="crate-preview-head">
        <span className="crate-preview-chip">PREVIEW</span>
        <span className="crate-preview-title">
          {preparing ? 'Cueing it up…' : preview.title || `Track ${preview.track_num ?? 1}`}
        </span>
      </div>

      <div className="crate-preview-controls">
        <button
          className="crate-preview-btn"
          onClick={() => onStep(-1)}
          aria-label="Previous track"
        >
          ‹‹
        </button>
        <button
          className="crate-preview-btn crate-preview-btn--play"
          onClick={onToggle}
          aria-label={playing ? 'Pause preview' : 'Play preview'}
        >
          {playing ? '❙❙' : '▶'}
        </button>
        <button className="crate-preview-btn" onClick={() => onStep(1)} aria-label="Next track">
          ››
        </button>

        <input
          className="crate-preview-seek"
          type="range"
          min={0}
          max={Math.max(1, Math.floor(preview.duration))}
          value={Math.floor(position)}
          onChange={(e) => onSeek(Number(e.target.value))}
          aria-label="Seek preview"
        />
        <span className="crate-preview-clock">
          {formatClock(position)} / {formatClock(preview.duration)}
        </span>
      </div>

      <div className="crate-preview-bar" aria-hidden="true">
        <div className="crate-preview-bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
