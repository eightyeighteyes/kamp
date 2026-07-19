import React, { useContext, useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { TooltipContext, useTooltip } from '../hooks/useTooltip'
import { TOOLTIPS } from '../tooltipStrings'
import {
  FavoriteIcon,
  NextIcon,
  PauseIcon,
  PlayIcon,
  PrevIcon,
  RepeatIcon,
  ShuffleIcon,
  StopIcon,
  VolumeIcon
} from './TransportIcons'
import { formatTime } from '../utils/formatTime'

export function TransportBar(): React.JSX.Element {
  const player = useStore((s) => s.player)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const stop = useStore((s) => s.stop)
  const next = useStore((s) => s.next)
  const prev = useStore((s) => s.prev)
  const seek = useStore((s) => s.seek)
  const setVolume = useStore((s) => s.setVolume)
  const setFavorite = useStore((s) => s.setFavorite)
  const queue = useStore((s) => s.queue)
  const setShuffle = useStore((s) => s.setShuffle)
  const setRepeat = useStore((s) => s.setRepeat)
  const albumGroupingActive = useStore((s) => s.albumGroupingActive)
  const shuffle = queue?.shuffle ?? false
  const repeat = queue?.repeat ?? 'off'
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)

  const { playing, position, duration, volume, current_track, buffering } = player

  const currentAlbum = current_track
    ? albums.find(
        (a) =>
          (a.album === current_track.album || a.display_album === current_track.album) &&
          (a.album_artist === current_track.album_artist ||
            a.display_album_artist === current_track.album_artist)
      )
    : undefined

  function goToNowPlaying(): void {
    void setActiveView('now-playing')
  }

  function goToArtist(): void {
    if (!current_track) return
    void setActiveView('library')
    selectArtist(current_track.album_artist)
  }

  function goToAlbum(): void {
    if (!currentAlbum) return
    void setActiveView('library')
    void selectAlbum(currentAlbum)
  }
  // Local scrub position: holds the seek-bar value while the pointer is down so
  // that React's controlled-input re-render (which would reset value to the
  // server position) can't fire a spurious second onChange and double-seek.
  const [scrubPos, setScrubPos] = useState<number | null>(null)
  const pointerDown = useRef(false)
  const tooltip = useTooltip()
  const { arm, disarm } = useContext(TooltipContext)
  const repeatBtnRef = useRef<HTMLButtonElement>(null)
  const repeatHovered = useRef(false)
  const repeatTooltip = `Repeat: ${repeat.charAt(0).toUpperCase() + repeat.slice(1)}`

  // Re-arm the tooltip if the mode changes while the pointer is still hovering
  // over the button — onMouseEnter won't fire again after a click.
  useEffect(() => {
    if (repeatHovered.current && repeatBtnRef.current) {
      arm(repeatTooltip, repeatBtnRef.current)
    }
  }, [repeatTooltip, arm])

  const displayPosition = scrubPos !== null ? scrubPos : position

  // Debounce the buffering indicator: only show it if buffering persists for
  // >250ms. This prevents a 1-2 frame shimmer flash when the cached URL
  // resolves immediately (CSS animation-delay doesn't suppress a class that
  // is added then removed within the delay window — only React state can).
  const [isBuffering, setIsBuffering] = useState(false)
  useEffect(() => {
    const timerId = setTimeout(() => setIsBuffering(!!buffering), buffering ? 250 : 0)
    return () => clearTimeout(timerId)
  }, [buffering])

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
  const currentTrackId = current_track?.id ?? null
  const [prevTrackId, setPrevTrackId] = useState<number | null>(currentTrackId)
  if (prevTrackId !== currentTrackId) {
    setPrevTrackId(currentTrackId)
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
              <button className="track-title" onClick={goToNowPlaying}>
                {current_track.title}
              </button>
            </div>
            <div className="track-field">
              <button className="track-artist" onClick={goToArtist}>
                {current_track.artist}
              </button>
            </div>
            <div className="track-field">
              <button className="track-album" onClick={goToAlbum}>
                {current_track.album}
              </button>
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

      <div className={`transport-progress${isBuffering ? ' is-buffering' : ''}`}>
        <span className="time">{isBuffering ? '…' : formatTime(displayPosition)}</span>
        <div className="seek-bar-wrapper">
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
              // track-change reset above (currentTrackId comparison) is the
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
                '--range-progress':
                  isBuffering && !duration
                    ? '100%'
                    : `${(displayPosition / (duration || 1)) * 100}%`
              } as React.CSSProperties
            }
          />
        </div>
        <span className="time">{isBuffering && !duration ? '' : formatTime(duration)}</span>
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
          ref={repeatBtnRef}
          className={`transport-btn${repeat !== 'off' ? ' active' : ''}`}
          onClick={() => {
            if (repeatHovered.current && repeatBtnRef.current) {
              const modes = albumGroupingActive
                ? (['off', 'queue', 'album', 'single'] as const)
                : (['off', 'queue', 'single'] as const)
              const idx = (modes as readonly string[]).indexOf(repeat)
              const next = modes[(idx === -1 ? 0 : idx + 1) % modes.length]
              arm(`Repeat: ${next.charAt(0).toUpperCase() + next.slice(1)}`, repeatBtnRef.current)
            }
            void setRepeat()
          }}
          onMouseEnter={(e) => {
            repeatHovered.current = true
            arm(repeatTooltip, e.currentTarget)
          }}
          onMouseLeave={() => {
            repeatHovered.current = false
            disarm()
          }}
          aria-label="Repeat"
          aria-pressed={repeat !== 'off'}
        >
          <RepeatIcon mode={repeat} />
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
    </div>
  )
}
