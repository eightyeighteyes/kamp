import React, { useRef, useState } from 'react'
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
  const queuePosition = useStore((s) => s.queue?.position ?? -1)
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const skipToQueueTrack = useStore((s) => s.skipToQueueTrack)
  const configValues = useStore((s) => s.configValues)
  const connected = configValues?.['bandcamp.connected'] ?? false

  const [menu, setMenu] = useState<TrackMenu | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const titleInputRef = useRef<HTMLInputElement>(null)

  const dragFromIdx = useRef<number | null>(null)

  if (!playlist) return null

  const totalDuration = playlistTracks.reduce((sum, t) => sum + (t.duration || 0), 0)

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

  // Insert all tracks next and immediately start playing the first one.
  const handlePlay = (): void => {
    if (playlistTracks.length === 0) return
    const insertAfter = queuePosition
    void (async () => {
      for (let i = playlistTracks.length - 1; i >= 0; i--) {
        await playNext(playlistTracks[i].file_path)
      }
      await skipToQueueTrack(insertAfter + 1)
    })()
  }

  const handleAddToQueue = (): void => {
    void (async () => {
      for (const t of playlistTracks) {
        await addToQueue(t.file_path)
      }
    })()
  }

  // Drag-to-reorder handlers
  const handleDragStart = (e: React.DragEvent, idx: number): void => {
    dragFromIdx.current = idx
    e.dataTransfer.setData('text/kamp-playlist-track-idx', String(idx))
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleDragOver = (e: React.DragEvent): void => {
    if (e.dataTransfer.types.includes('text/kamp-playlist-track-idx')) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      e.currentTarget.classList.add('drag-over')
    }
  }

  const handleDragLeave = (e: React.DragEvent): void => {
    // Only remove the indicator when the cursor leaves the row entirely,
    // not when it moves between child elements (spans, etc.).
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      e.currentTarget.classList.remove('drag-over')
    }
  }

  const handleDrop = (e: React.DragEvent, dropIdx: number): void => {
    e.preventDefault()
    e.currentTarget.classList.remove('drag-over')
    const fromStr = e.dataTransfer.getData('text/kamp-playlist-track-idx')
    if (!fromStr) return
    const from = Number(fromStr)
    if (from === dropIdx) return
    const newOrder = computeNewOrder(playlistTracks.length, [from], dropIdx)
    const newTrackIds = newOrder.map((i) => playlistTracks[i].playlist_track_id)
    void reorderPlaylistTracks(playlist.id, newTrackIds)
  }

  return (
    <div className="track-list-view">
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
            <button className="play-all-btn" aria-label="Play" onClick={handlePlay}>
              {playing &&
              currentTrack &&
              playlistTracks.some((t) => t.file_path === currentTrack.file_path) ? (
                <PauseIcon size={18} />
              ) : (
                <PlayIcon size={18} />
              )}
            </button>
          </div>
        </div>
      </div>

      <div className="track-list-divider" />

      <div className="track-list-body">
        <ol className="track-rows">
          {playlistTracks.map((track, i) => {
            const isCurrent = currentTrack?.file_path === track.file_path
            const isRemote = track.source !== 'local'
            const isOffline = isRemote && !connected
            return (
              <li
                key={track.playlist_track_id}
                className={`track-row${isCurrent ? ' current' : ''}${isOffline ? ' track-row--offline' : ''}`}
                tabIndex={0}
                draggable
                onDragStart={(e) => handleDragStart(e, i)}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, i)}
                onContextMenu={(e) => {
                  e.preventDefault()
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
          onClose={() => setMenu(null)}
          onRemoveFromPlaylist={() =>
            void removeTrackFromPlaylist(playlist.id, menu.track.playlist_track_id)
          }
        />
      )}
    </div>
  )
}
