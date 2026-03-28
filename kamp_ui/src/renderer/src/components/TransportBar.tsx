import React from 'react'
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

  const { playing, position, duration, volume, current_track } = player
  return (
    <div className="transport-bar">
      <div className="transport-track-info">
        {current_track ? (
          <>
            <span className="track-title">{current_track.title}</span>
            <span className="track-artist">{current_track.artist}</span>
            <span className="track-album">{current_track.album}</span>
          </>
        ) : (
          <span className="track-idle">No track loaded</span>
        )}
      </div>

      <div className="transport-controls">
        <button className="transport-btn" onClick={prev} title="Previous">
          ⏮
        </button>
        <button
          className="transport-btn primary"
          onClick={togglePlayPause}
          title={playing ? 'Pause' : 'Play'}
        >
          {playing ? '⏸' : '▶'}
        </button>
        <button className="transport-btn" onClick={stop} title="Stop">
          ⏹
        </button>
        <button className="transport-btn" onClick={next} title="Next">
          ⏭
        </button>
      </div>

      <div className="transport-progress">
        <span className="time">{formatTime(position)}</span>
        <input
          type="range"
          className="seek-bar"
          min={0}
          max={duration || 1}
          step={0.5}
          value={position}
          onChange={(e) => seek(parseFloat(e.target.value))}
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
        />
        <span className="volume-label">{volume}</span>
      </div>
    </div>
  )
}
