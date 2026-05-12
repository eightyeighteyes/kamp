import React, { useRef, useState } from 'react'
import { useStore } from '../store'

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function TransportBar(): React.JSX.Element {
  const player = useStore((s) => s.player)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const stop = useStore((s) => s.stop)
  const next = useStore((s) => s.next)
  const prev = useStore((s) => s.prev)
  const seek = useStore((s) => s.seek)
  const setVolume = useStore((s) => s.setVolume)
  const queueVisible = useStore((s) => s.queueVisible)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const setFavorite = useStore((s) => s.setFavorite)

  const { playing, position, duration, volume, current_track } = player
  // Local scrub position: holds the seek-bar value while the pointer is down so
  // that React's controlled-input re-render (which would reset value to the
  // server position) can't fire a spurious second onChange and double-seek.
  const [scrubPos, setScrubPos] = useState<number | null>(null)
  const pointerDown = useRef(false)
  const displayPosition = scrubPos !== null ? scrubPos : position

  // Force-clear scrub state when the track changes. Without this, a pointerup
  // event that didn't reach the slider (release outside its bounds, OS-level
  // capture glitch, etc.) would leave scrubPos wedged at the user's last seek
  // target — the slider would then ignore every fresh server position forever
  // and "stick at the seek position until restart" (KAMP-284 follow-up bug,
  // surfaced after non-gapless EOF transitions: server reports position=0 for
  // the new track but the slider keeps showing the wedged scrub value).
  //
  // Pattern: "adjust state during rendering" per React docs — store the prior
  // value in state and compare during render. Avoids the useEffect cascade
  // warning and runs synchronously so the very first render after a track
  // change already shows the server position.
  const currentPath = current_track?.file_path ?? null
  const [prevPath, setPrevPath] = useState<string | null>(currentPath)
  if (prevPath !== currentPath) {
    setPrevPath(currentPath)
    setScrubPos(null)
    // pointerDown.current is intentionally not touched here — a fresh
    // onPointerDown will set it true again, and pointerUp/pointerCancel
    // remain the canonical clear sites. Mutating a ref during render is
    // disallowed by the React Compiler lint anyway.
  }

  return (
    <div className="transport-bar">
      <div className="transport-track-info">
        {current_track ? (
          <>
            <div className="track-field">
              <span className="track-title">{current_track.title}</span>
            </div>
            <div className="track-field">
              <span className="track-artist">{current_track.artist}</span>
            </div>
            <div className="track-field">
              <span className="track-album">{current_track.album}</span>
            </div>
          </>
        ) : (
          <span className="track-idle">No track loaded</span>
        )}
      </div>

      <button
        className={`transport-btn favorite-btn${current_track?.favorite ? ' active' : ''}`}
        onClick={() =>
          current_track && void setFavorite(current_track.file_path, !current_track.favorite)
        }
        disabled={!current_track}
        title={current_track?.favorite ? 'Remove from favorites' : 'Add to favorites'}
        aria-pressed={current_track?.favorite ?? false}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill={current_track?.favorite ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
        </svg>
      </button>

      <div className="transport-controls">
        <button className="transport-btn" onClick={prev} title="Previous (←)">
          ⏮
        </button>
        <button
          className="transport-btn primary"
          onClick={togglePlayPause}
          title={playing ? 'Pause (Space)' : 'Play (Space)'}
        >
          {playing ? '⏸' : '▶'}
        </button>
        <button className="transport-btn" onClick={stop} title="Stop">
          ⏹
        </button>
        <button className="transport-btn" onClick={next} title="Next (→)">
          ⏭
        </button>
      </div>

      <div className="transport-progress">
        <span className="time">{formatTime(displayPosition)}</span>
        <input
          type="range"
          className="seek-bar"
          min={0}
          max={duration || 1}
          step={0.5}
          value={displayPosition}
          onPointerDown={(e) => {
            pointerDown.current = true
            setScrubPos(position)
            // Pin pointer events to the slider so pointerup is guaranteed to
            // fire here even if the user releases the pointer outside the
            // slider bounds. Without this, scrubPos can wedge — the
            // track-change reset above (currentPath comparison) is the
            // escape hatch for any wedge that still slips through.
            try {
              e.currentTarget.setPointerCapture(e.pointerId)
            } catch {
              // Some browsers reject setPointerCapture on non-trusted events
              // (synthetic pointer events from automation, etc.) — ignore.
            }
          }}
          onChange={(e) => {
            const val = parseFloat(e.target.value)
            setScrubPos(val)
            if (pointerDown.current) seek(val)
          }}
          onPointerUp={() => {
            pointerDown.current = false
            setScrubPos(null)
          }}
          onPointerCancel={() => {
            // Touch-cancel, OS-level capture loss, or a programmatic
            // releasePointerCapture call — treat exactly like pointerup so
            // the slider can never stay wedged on scrubPos.
            pointerDown.current = false
            setScrubPos(null)
          }}
          style={
            {
              '--range-progress': `${(displayPosition / (duration || 1)) * 100}%`
            } as React.CSSProperties
          }
        />
        <span className="time">{formatTime(duration)}</span>
      </div>

      <div className="transport-volume">
        <span title="Volume">🔊</span>
        <input
          type="range"
          className="volume-slider"
          min={0}
          max={100}
          value={volume}
          onChange={(e) => setVolume(parseInt(e.target.value, 10))}
          style={{ '--range-progress': `${volume}%` } as React.CSSProperties}
        />
        <span className="volume-label">{volume}</span>
      </div>

      <button
        className={`transport-btn queue-toggle-btn${queueVisible ? ' active' : ''}`}
        onClick={toggleQueuePanel}
        title="Queue (Q)"
      >
        ☰
      </button>
    </div>
  )
}
