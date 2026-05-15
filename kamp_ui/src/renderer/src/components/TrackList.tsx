import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Track } from '../api/client'
import { TrackContextMenu } from './TrackContextMenu'
import {
  FavoriteIcon,
  PencilIcon,
  PlayIcon,
  PauseIcon,
  QueueAddIcon,
  PlayNextIcon
} from './TransportIcons'

type ContextMenu = { x: number; y: number; track: Track }

function HeroImage({ src }: { src: string }): React.JSX.Element {
  const [loaded, setLoaded] = useState(false)
  return (
    <img
      className={`track-list-hero-img${loaded ? ' loaded' : ''}`}
      src={src}
      alt=""
      onLoad={() => setLoaded(true)}
      onError={() => setLoaded(false)}
    />
  )
}

export function TrackList(): React.JSX.Element | null {
  const album = useStore((s) => s.library.selectedAlbum)
  const tracks = useStore((s) => s.library.tracks)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const playTrack = useStore((s) => s.playTrack)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const setAlbumFavorite = useStore((s) => s.setAlbumFavorite)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const playAlbumNext = useStore((s) => s.playAlbumNext)

  const albumEditMode = useStore((s) => s.albumEditMode)
  const setAlbumEditMode = useStore((s) => s.setAlbumEditMode)
  const albumTitleRef = useRef<HTMLHeadingElement>(null)

  // Focus the album title heading when entering edit mode so screen readers
  // receive the live-region announcement in context.
  useEffect(() => {
    if (albumEditMode) albumTitleRef.current?.focus()
  }, [albumEditMode])

  const [menu, setMenu] = useState<ContextMenu | null>(null)

  if (!album) return null

  const isCurrentAlbum =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div className={`track-list-view${albumEditMode ? ' track-list-view--edit' : ''}`}>
      {/* Hero: full-width art — image intentionally taller than hero to bleed into track list */}
      <div className={`track-list-hero${album.has_art ? ' has-art' : ''}`}>
        {album.has_art && (
          <HeroImage
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
          />
        )}
      </div>
      {/* Overlay spans the full view so the gradient covers both hero and the top of the track list */}
      <div className="track-list-hero-overlay" />

      {/* Breadcrumb floats over the hero */}
      <nav className="breadcrumb" aria-label="Navigation">
        <button
          onClick={() => {
            selectAlbum(null)
            selectArtist(null)
          }}
        >
          Library
        </button>
        <span className="breadcrumb-sep" aria-hidden="true">
          ›
        </span>
        <button
          onClick={() => {
            selectAlbum(null)
            selectArtist(album.album_artist)
          }}
        >
          {album.album_artist}
        </button>
        <span className="breadcrumb-sep" aria-hidden="true">
          ›
        </span>
        <span>{album.album}</span>
      </nav>

      {/* Edit toggle — separate pill, right side of the hero row */}
      <button
        className={`breadcrumb-edit-btn${albumEditMode ? ' active' : ''}`}
        aria-pressed={albumEditMode}
        onClick={() => setAlbumEditMode(!albumEditMode)}
      >
        <PencilIcon size={11} />
        {albumEditMode ? 'Done' : 'Edit tags'}
      </button>

      {/* Screen-reader announcement for edit-mode transitions */}
      <div aria-live="polite" className="sr-only">
        {albumEditMode ? 'Edit mode on. Album, artist, and track titles are editable.' : ''}
      </div>

      {/* Static identity block — does not scroll */}
      <div className="track-list-identity">
        <div className="track-list-identity-text">
          <button
            className={`track-list-album-fav-btn favorite-btn${album.favorite ? ' active' : ''}`}
            aria-label={album.favorite ? 'Remove from favorites' : 'Add to favorites'}
            aria-pressed={album.favorite}
            onClick={() => setAlbumFavorite(album.album_artist, album.album, !album.favorite)}
          >
            <FavoriteIcon active={album.favorite} size={36} />
          </button>
          <h1 ref={albumTitleRef} className="track-list-album-title" tabIndex={-1}>
            {album.album}
          </h1>
          <h2 className="track-list-album-artist">
            <button
              className="track-list-artist-link"
              onClick={() => {
                selectAlbum(null)
                selectArtist(album.album_artist)
              }}
            >
              {album.album_artist}
            </button>
          </h2>
          {album.year && <div className="track-list-album-year">{album.year}</div>}
        </div>
        <div className="album-controls">
          <button
            className="album-secondary-btn"
            title="Add to queue"
            aria-label="Add album to queue"
            onClick={() => void addAlbumToQueue(album.album_artist, album.album, album.file_path)}
          >
            <QueueAddIcon size={16} />
          </button>
          <button
            className="album-secondary-btn"
            title="Play next"
            aria-label="Play album next"
            onClick={() => void playAlbumNext(album.album_artist, album.album, album.file_path)}
          >
            <PlayNextIcon size={16} />
          </button>
          <button
            className="play-all-btn"
            aria-label={isCurrentAlbum && playing ? 'Pause' : 'Play all'}
            onClick={() =>
              isCurrentAlbum
                ? togglePlayPause()
                : playTrack(album.album_artist, album.album, 0, album.file_path)
            }
          >
            {isCurrentAlbum && playing ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
          </button>
        </div>
      </div>

      <div className="track-list-divider" />

      {/* Scrollable body */}
      <div className="track-list-body">
        <ol className="track-rows">
          {tracks.map((track, i) => {
            const isCurrent = currentTrack?.id === track.id
            return (
              <li
                key={track.id}
                className={`track-row${isCurrent ? ' current' : ''}`}
                tabIndex={0}
                onDoubleClick={() => {
                  if (isCurrent) {
                    togglePlayPause()
                  } else {
                    playTrack(album.album_artist, album.album, i, album.file_path)
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key !== 'Enter') return
                  if (isCurrent) togglePlayPause()
                  else playTrack(album.album_artist, album.album, i, album.file_path)
                }}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('text/kamp-track-path', track.file_path)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
                onContextMenu={(e) => {
                  e.preventDefault()
                  setMenu({ x: e.clientX, y: e.clientY, track })
                }}
              >
                <span className="track-row-fav">
                  {track.favorite && <FavoriteIcon active size={10} />}
                </span>
                <span className="track-row-num">
                  {isCurrent ? (
                    playing ? (
                      <PlayIcon size={11} />
                    ) : (
                      <PauseIcon size={11} />
                    )
                  ) : (
                    track.track_number
                  )}
                </span>
                <span className="track-row-title">{track.title}</span>
                <span className="track-row-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      </div>

      {menu && (
        <TrackContextMenu x={menu.x} y={menu.y} track={menu.track} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}
