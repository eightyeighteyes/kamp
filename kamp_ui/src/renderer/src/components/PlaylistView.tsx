import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { playlistArtUrl } from '../api/client'
import type { PlaylistTrack } from '../api/client'
import { TrackContextMenu } from './TrackContextMenu'
import { computeNewOrder } from '../utils/computeNewOrder'
import { truncateTitle } from '../utils/truncateTitle'
import {
  FavoriteIcon,
  PauseIcon,
  PlayIcon,
  PlayNextIcon,
  QueueAddIcon,
  WarnIcon
} from './TransportIcons'
import { formatTime } from '../utils/formatTime'

const HERO_DEFAULT = 45
const HERO_MIN = 15
const HERO_KEY = 'kamp:playlist-hero-height-pct'

type TrackMenu = { x: number; y: number; track: PlaylistTrack }

function HeroImage({ src }: { src: string }): React.JSX.Element {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      className={`track-list-hero-img${loaded ? ' loaded' : ''}`}
      src={src}
      alt=""
      draggable={false}
      onLoad={() => setLoaded(true)}
    />
  )
}

export function PlaylistView(): React.JSX.Element | null {
  const playlist = useStore((s) => s.library.selectedPlaylist)
  const playlistTracks = useStore((s) => s.library.playlistTracks)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const reorderPlaylistTracks = useStore((s) => s.reorderPlaylistTracks)
  const removeTrackFromPlaylist = useStore((s) => s.removeTrackFromPlaylist)
  const setPlaylistFavorite = useStore((s) => s.setPlaylistFavorite)
  const renamePlaylist = useStore((s) => s.renamePlaylist)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const playPlaylist = useStore((s) => s.playPlaylist)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const configValues = useStore((s) => s.configValues)
  const connected = configValues?.['bandcamp.connected'] ?? false

  const [menu, setMenu] = useState<TrackMenu | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [heroHeightPct, setHeroHeightPct] = useState<number>(() => {
    const saved = parseFloat(localStorage.getItem(HERO_KEY) ?? '')
    return isNaN(saved) ? HERO_DEFAULT : Math.min(HERO_DEFAULT, Math.max(HERO_MIN, saved))
  })
  const [isResizing, setIsResizing] = useState(false)
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set())
  const [anchorIdx, setAnchorIdx] = useState<number | null>(null)

  const titleInputRef = useRef<HTMLInputElement>(null)
  const dragFromIdx = useRef<number | null>(null)
  const didDragRef = useRef(false)
  const dragStartYRef = useRef(0)
  const heroAtDragStartRef = useRef(HERO_DEFAULT)
  const pendingSingleSelect = useRef<number | null>(null)

  // Clear selection when tracks are added or removed so indices don't go stale.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedIndices(new Set())
    setAnchorIdx(null)
  }, [playlistTracks.length])

  if (!playlist) return null

  const totalDuration = playlistTracks.reduce((sum, t) => sum + (t.duration || 0), 0)

  const handleResizeMouseDown = (e: React.MouseEvent): void => {
    e.preventDefault()
    didDragRef.current = false
    dragStartYRef.current = e.clientY
    heroAtDragStartRef.current = heroHeightPct
    setIsResizing(true)

    const onMove = (ev: MouseEvent): void => {
      const deltaVh = ((ev.clientY - dragStartYRef.current) / window.innerHeight) * 100
      if (Math.abs(ev.clientY - dragStartYRef.current) > 4) didDragRef.current = true
      if (!didDragRef.current) return
      setHeroHeightPct(
        Math.min(HERO_DEFAULT, Math.max(HERO_MIN, heroAtDragStartRef.current + deltaVh))
      )
    }

    const onUp = (): void => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      setIsResizing(false)
      if (didDragRef.current) {
        setHeroHeightPct((h) => {
          localStorage.setItem(HERO_KEY, String(Math.round(h)))
          return h
        })
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  const handleResizeReset = (): void => {
    setHeroHeightPct(HERO_DEFAULT)
    localStorage.setItem(HERO_KEY, String(HERO_DEFAULT))
  }

  const handleTitleDoubleClick = (): void => {
    setTitleDraft(playlist.title)
    setEditingTitle(true)
    setTimeout(() => titleInputRef.current?.select(), 0)
  }

  const commitTitle = (): void => {
    const trimmed = titleDraft.trim()
    if (trimmed && trimmed !== playlist.title) {
      void renamePlaylist(playlist.id, trimmed)
    }
    setEditingTitle(false)
  }

  // Insert all playlist tracks at "play next" position. playNext always inserts at
  // currentPosition+1, so iterating in reverse lands them in the correct order.
  const handlePlayNext = (): void => {
    if (playlistTracks.length === 0) return
    void (async () => {
      for (let i = playlistTracks.length - 1; i >= 0; i--) {
        await playNext(playlistTracks[i].file_path)
      }
    })()
  }

  const isCurrentPlaylist =
    currentTrack !== null && playlistTracks.some((t) => t.file_path === currentTrack.file_path)

  // If a playlist track is already in the queue: couple to transport (pause/resume).
  // Otherwise: replace the queue with this playlist's tracks and start playing.
  const handlePlay = (): void => {
    if (playlistTracks.length === 0) return
    if (isCurrentPlaylist) {
      void togglePlayPause()
    } else {
      void playPlaylist(playlist.id)
    }
  }

  const handleAddToQueue = (): void => {
    void (async () => {
      for (const t of playlistTracks) {
        await addToQueue(t.file_path)
      }
    })()
  }

  const handleRowMouseDown = (e: React.MouseEvent, idx: number): void => {
    if (e.button !== 0) return
    if (e.shiftKey && anchorIdx !== null) {
      const lo = Math.min(anchorIdx, idx)
      const hi = Math.max(anchorIdx, idx)
      setSelectedIndices(new Set(Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)))
    } else if (e.metaKey || e.ctrlKey) {
      setSelectedIndices((prev) => {
        const next = new Set(prev)
        next.has(idx) ? next.delete(idx) : next.add(idx)
        return next
      })
      setAnchorIdx(idx)
    } else if (selectedIndices.has(idx) && selectedIndices.size > 1) {
      // Defer collapse to mouseup so a drag can start with the full selection.
      pendingSingleSelect.current = idx
    } else {
      setSelectedIndices(new Set([idx]))
      setAnchorIdx(idx)
    }
  }

  const handleRowMouseUp = (idx: number): void => {
    if (pendingSingleSelect.current === idx) {
      pendingSingleSelect.current = null
      setSelectedIndices(new Set([idx]))
      setAnchorIdx(idx)
    }
  }

  // Drag-to-reorder handlers
  const handleDragStart = (e: React.DragEvent, idx: number): void => {
    pendingSingleSelect.current = null
    dragFromIdx.current = idx
    const isMulti = selectedIndices.has(idx) && selectedIndices.size > 1
    if (isMulti) {
      const sorted = [...selectedIndices].sort((a, b) => a - b)
      e.dataTransfer.setData('text/kamp-playlist-track-idx', String(idx))
      e.dataTransfer.setData('text/kamp-playlist-multi', JSON.stringify(sorted))
      const ghost = document.createElement('div')
      ghost.textContent = `${sorted.length} tracks`
      ghost.style.cssText =
        'position:fixed;top:-100px;background:var(--accent);color:#fff;padding:4px 10px;border-radius:3px;font-size:12px;font-weight:600'
      document.body.appendChild(ghost)
      e.dataTransfer.setDragImage(ghost, 0, 0)
      requestAnimationFrame(() => document.body.removeChild(ghost))
    } else {
      setSelectedIndices(new Set())
      setAnchorIdx(null)
      e.dataTransfer.setData('text/kamp-playlist-track-idx', String(idx))
    }
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleDragEnd = (): void => {
    setSelectedIndices(new Set())
    setAnchorIdx(null)
  }

  const isPlaylistDrop = (types: DOMStringList | readonly string[]): boolean =>
    Array.from(types).some(
      (t) => t === 'text/kamp-playlist-track-idx' || t === 'text/kamp-playlist-multi'
    )

  const handleDragOver = (e: React.DragEvent): void => {
    if (!isPlaylistDrop(e.dataTransfer.types)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    e.currentTarget.classList.add('drag-over')
  }

  const handleDragLeave = (e: React.DragEvent): void => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      e.currentTarget.classList.remove('drag-over')
    }
  }

  const handleDrop = (e: React.DragEvent, dropIdx: number): void => {
    e.preventDefault()
    e.currentTarget.classList.remove('drag-over')
    const multiJson = e.dataTransfer.getData('text/kamp-playlist-multi')
    const fromStr = e.dataTransfer.getData('text/kamp-playlist-track-idx')
    if (multiJson) {
      const sorted: number[] = JSON.parse(multiJson)
      const newOrder = computeNewOrder(playlistTracks.length, sorted, dropIdx)
      void reorderPlaylistTracks(
        playlist.id,
        newOrder.map((i) => playlistTracks[i].playlist_track_id)
      )
    } else if (fromStr) {
      const from = Number(fromStr)
      if (from === dropIdx) return
      const newOrder = computeNewOrder(playlistTracks.length, [from], dropIdx)
      void reorderPlaylistTracks(
        playlist.id,
        newOrder.map((i) => playlistTracks[i].playlist_track_id)
      )
    }
  }

  return (
    <div
      className={`track-list-view${isResizing ? ' track-list-view--resizing' : ''}`}
      style={{ '--hero-height-pct': heroHeightPct } as React.CSSProperties}
    >
      <div className="track-list-hero has-art">
        <HeroImage src={playlistArtUrl(playlist.id, playlist.updated_at)} />
      </div>
      <div className="track-list-hero-overlay" />

      <nav className="breadcrumb" aria-label="Navigation">
        <button onClick={() => void selectPlaylist(null)}>Playlists</button>
        <span className="breadcrumb-sep" aria-hidden="true">
          ›
        </span>
        <span title={playlist.title}>{truncateTitle(playlist.title)}</span>
      </nav>

      <div className="track-list-identity">
        <div className="track-list-identity-text">
          <button
            className={`track-list-album-fav-btn favorite-btn${playlist.favorite ? ' active' : ''}`}
            aria-label={playlist.favorite ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={playlist.favorite}
            onClick={() => void setPlaylistFavorite(playlist.id, !playlist.favorite)}
          >
            <FavoriteIcon active={playlist.favorite} size={36} />
          </button>
          {editingTitle ? (
            <input
              ref={titleInputRef}
              className="track-list-album-title"
              value={titleDraft}
              autoFocus
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitTitle}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitTitle()
                if (e.key === 'Escape') setEditingTitle(false)
              }}
              style={{
                background: 'transparent',
                border: 'none',
                outline: '1px solid var(--accent)',
                width: '100%'
              }}
            />
          ) : (
            <h1 className="track-list-album-title" onDoubleClick={handleTitleDoubleClick}>
              {playlist.title}
            </h1>
          )}
          <div className="track-list-album-year">
            {[
              playlistTracks.length === 1 ? '1 track' : `${playlistTracks.length} tracks`,
              totalDuration > 0 ? formatTime(totalDuration) : ''
            ]
              .filter(Boolean)
              .join(' · ')}
          </div>
        </div>
        <div className="album-controls-group">
          <div className="album-controls">
            <button
              className="album-secondary-btn"
              aria-label="Add all to queue"
              onClick={handleAddToQueue}
            >
              <QueueAddIcon size={16} />
            </button>
            <button
              className="album-secondary-btn"
              aria-label="Play all next"
              onClick={handlePlayNext}
            >
              <PlayNextIcon size={16} />
            </button>
            <button
              className="play-all-btn"
              aria-label={isCurrentPlaylist && playing ? 'Pause' : 'Play'}
              onClick={handlePlay}
            >
              {isCurrentPlaylist && playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
            </button>
          </div>
        </div>
      </div>

      <button
        className="album-meta-toggle"
        aria-label="Resize hero"
        onMouseDown={handleResizeMouseDown}
        onDoubleClick={handleResizeReset}
      />

      <div className="track-list-body">
        <ol className="track-rows">
          {playlistTracks.map((track, i) => {
            const isCurrent = currentTrack?.file_path === track.file_path
            const isRemote = track.source !== 'local'
            const isOffline = isRemote && !connected
            const isSelected = selectedIndices.has(i)
            return (
              <li
                key={track.playlist_track_id}
                className={[
                  'track-row',
                  isCurrent ? 'current' : '',
                  isOffline ? 'track-row--offline' : '',
                  isSelected ? 'selected' : ''
                ]
                  .filter(Boolean)
                  .join(' ')}
                tabIndex={0}
                draggable
                onMouseDown={(e) => handleRowMouseDown(e, i)}
                onMouseUp={() => handleRowMouseUp(i)}
                onDragStart={(e) => handleDragStart(e, i)}
                onDragEnd={handleDragEnd}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, i)}
                onContextMenu={(e) => {
                  e.preventDefault()
                  // Right-click on an unselected row: select only that row.
                  const nextIndices = isSelected ? selectedIndices : new Set([i])
                  if (!isSelected) {
                    setSelectedIndices(nextIndices)
                    setAnchorIdx(i)
                  }
                  setMenu({ x: e.clientX, y: e.clientY, track })
                }}
              >
                <span className="track-row-fav">
                  {track.favorite && <FavoriteIcon active size={10} />}
                </span>
                <span className="track-row-num">{i + 1}</span>
                <span className="track-row-title-cell">
                  {isOffline && (
                    <span
                      className="track-row-offline-icon"
                      title="Track unavailable offline"
                      aria-hidden="true"
                    >
                      <WarnIcon size={11} />
                    </span>
                  )}
                  <span
                    className={
                      isOffline ? 'track-row-title track-row-title--offline' : 'track-row-title'
                    }
                  >
                    {track.title}
                  </span>
                </span>
                <span className="track-row-artist">{track.artist}</span>
                <span className="track-row-duration">
                  {track.duration > 0 ? formatTime(track.duration) : '—'}
                </span>
              </li>
            )
          })}
        </ol>
        {playlistTracks.length === 0 && (
          <div className="album-grid-empty">
            No tracks yet. Right-click any track or album and choose Add to Playlist.
          </div>
        )}
      </div>

      {menu && (
        <TrackContextMenu
          x={menu.x}
          y={menu.y}
          track={menu.track}
          selectedTracks={
            selectedIndices.size > 1
              ? [...selectedIndices].sort((a, b) => a - b).map((i) => playlistTracks[i])
              : undefined
          }
          onClose={() => setMenu(null)}
          onRemoveFromPlaylist={() => {
            const targets =
              selectedIndices.size > 0
                ? [...selectedIndices]
                    .sort((a, b) => b - a)
                    .map((i) => playlistTracks[i].playlist_track_id)
                : [menu.track.playlist_track_id]
            targets.forEach((ptId) => void removeTrackFromPlaylist(playlist.id, ptId))
          }}
        />
      )}
    </div>
  )
}
