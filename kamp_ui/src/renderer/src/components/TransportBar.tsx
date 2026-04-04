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
  const queueVisible = useStore((s) => s.queueVisible)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const setFavorite = useStore((s) => s.setFavorite)

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

      <button
        className={`transport-btn favorite-btn${current_track?.favorite ? ' active' : ''}`}
        onClick={() =>
          current_track && void setFavorite(current_track.file_path, !current_track.favorite)
        }
        disabled={!current_track}
        title={current_track?.favorite ? 'Remove from favorites' : 'Add to favorites'}
        aria-pressed={current_track?.favorite ?? false}
      >
        <svg width="16" height="16" viewBox="0 0 24 24"
          fill={current_track?.favorite ? 'currentColor' : 'none'}
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
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
        <span className="time">{formatTime(position)}</span>
        <input
          type="range"
          className="seek-bar"
          min={0}
          max={duration || 1}
          step={0.5}
          value={position}
          onChange={(e) => seek(parseFloat(e.target.value))}
          style={
            { '--range-progress': `${(position / (duration || 1)) * 100}%` } as React.CSSProperties
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
