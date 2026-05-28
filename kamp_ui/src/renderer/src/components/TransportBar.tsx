import React, { useRef, useState } from 'react'
import { useStore } from '../store'
import { useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import {
  FavoriteIcon,
  NextIcon,
  PauseIcon,
  PlayIcon,
  PrevIcon,
  QueueIcon,
  RepeatIcon,
  ShuffleIcon,
  StopIcon,
  VolumeIcon
} from './TransportIcons'

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
  const queue = useStore((s) => s.queue)
  const setShuffle = useStore((s) => s.setShuffle)
  const setRepeat = useStore((s) => s.setRepeat)
  const shuffle = queue?.shuffle ?? false
  const repeat = queue?.repeat ?? false

  const { playing, position, duration, volume, current_track } = player
  // Local scrub position: holds the seek-bar value while the pointer is down so
  // that React's controlled-input re-render (which would reset value to the
  // server position) can't fire a spurious second onChange and double-seek.
  const [scrubPos, setScrubPos] = useState<number | null>(null)
  const pointerDown = useRef(false)
  const tooltip = useTooltip()
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
        onClick={() => current_track && void setFavorite(current_track, !current_track.favorite)}
        disabled={!current_track}
        {...tooltip(
          current_track?.favorite
            ? TOOLTIPS.TRANSPORT_FAVORITE_REMOVE
            : TOOLTIPS.TRANSPORT_FAVORITE_ADD
        )}
        aria-pressed={current_track?.favorite ?? false}
      >
        <FavoriteIcon active={current_track?.favorite ?? false} />
      </button>

      <div className="transport-controls">
        <button
          className="transport-btn"
          onClick={prev}
          {...tooltip(TOOLTIPS.TRANSPORT_PREV)}
          aria-label="Previous"
        >
          <PrevIcon />
        </button>
        <button
          className="transport-btn primary"
          onClick={togglePlayPause}
          {...tooltip(playing ? TOOLTIPS.TRANSPORT_PAUSE : TOOLTIPS.TRANSPORT_PLAY)}
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button
          className="transport-btn"
          onClick={stop}
          {...tooltip(TOOLTIPS.TRANSPORT_STOP)}
          aria-label="Stop"
        >
          <StopIcon />
        </button>
        <button
          className="transport-btn"
          onClick={next}
          {...tooltip(TOOLTIPS.TRANSPORT_NEXT)}
          aria-label="Next"
        >
          <NextIcon />
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

      <div className="transport-mode-btns">
        <button
          className={`transport-btn${shuffle ? ' active' : ''}`}
          onClick={() => void setShuffle(!shuffle)}
          {...tooltip(TOOLTIPS.TRANSPORT_SHUFFLE)}
          aria-label="Shuffle"
          aria-pressed={shuffle}
        >
          <ShuffleIcon />
        </button>
        <button
          className={`transport-btn${repeat ? ' active' : ''}`}
          onClick={() => void setRepeat(!repeat)}
          {...tooltip(TOOLTIPS.TRANSPORT_REPEAT)}
          aria-label="Repeat"
          aria-pressed={repeat}
        >
          <RepeatIcon />
        </button>
      </div>

      <div className="transport-volume">
        <span className="volume-icon" aria-hidden="true">
          <VolumeIcon />
        </span>
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
        {...tooltip(TOOLTIPS.TRANSPORT_QUEUE)}
        aria-label="Queue"
        aria-pressed={queueVisible}
      >
        <QueueIcon />
      </button>
    </div>
  )
}
