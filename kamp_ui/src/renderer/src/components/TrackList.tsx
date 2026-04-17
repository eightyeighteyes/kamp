import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'

type ContextMenu = { x: number; y: number; filePath: string; favorite: boolean }

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
  const addToQueue = useStore((s) => s.addToQueue)
  const playNext = useStore((s) => s.playNext)
  const setFavorite = useStore((s) => s.setFavorite)

  const [menu, setMenu] = useState<ContextMenu | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // Dismiss the context menu on any click outside it.
  useEffect(() => {
    if (!menu) return
    const handler = (e: MouseEvent): void => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menu])

  // Flip the menu toward the cursor if it would overflow the window edge.
  useLayoutEffect(() => {
    if (!menu || !menuRef.current) return
    const el = menuRef.current
    const rect = el.getBoundingClientRect()
    if (rect.right > window.innerWidth) el.style.left = `${menu.x - rect.width}px`
    if (rect.bottom > window.innerHeight) el.style.top = `${menu.y - rect.height}px`
  }, [menu])

  if (!album) return null

  const isCurrentAlbum =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div className="track-list-view">
      {/* Hero: full-width art — image intentionally taller than hero to bleed into track list */}
      <div className={`track-list-hero${album.has_art ? ' has-art' : ''}`}>
        {album.has_art && <HeroImage src={artUrl(album.album_artist, album.album, album.file_path)} />}
      </div>
      {/* Overlay spans the full view so the gradient covers both hero and the top of the track list */}
      <div className="track-list-hero-overlay" />

      {/* Back button floats over the hero */}
      <button className="back-btn" onClick={() => selectAlbum(null)} aria-label="Back to albums">
        ←
      </button>

      {/* Static identity block — does not scroll */}
      <div className="track-list-identity">
        <div className="track-list-identity-text">
          <h1 className="track-list-album-title">{album.album}</h1>
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
        <button
          className="play-all-btn"
          aria-label="Play all"
          onClick={() => playTrack(album.album_artist, album.album, 0, album.file_path)}
        >
          ▶
        </button>
      </div>

      <div className="track-list-divider" />

      {/* Scrollable body */}
      <div className="track-list-body">
        <ol className="track-rows">
          {tracks.map((track, i) => {
            const isCurrent = isCurrentAlbum && currentTrack?.track_number === track.track_number
            return (
              <li
                key={`${track.disc_number}-${track.track_number}`}
                className={`track-row${isCurrent ? ' current' : ''}`}
                tabIndex={0}
                onClick={() => {
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
                  setMenu({
                    x: e.clientX,
                    y: e.clientY,
                    filePath: track.file_path,
                    favorite: track.favorite
                  })
                }}
              >
                <span className="track-row-fav" aria-hidden="true">
                  {track.favorite ? '♥' : ''}
                </span>
                <span className="track-row-num">
                  {isCurrent ? (playing ? '▶' : '▐▐') : track.track_number}
                </span>
                <span className="track-row-title">{track.title}</span>
                <span className="track-row-artist">{track.artist}</span>
              </li>
            )
          })}
        </ol>
      </div>

      {menu && (
        <div ref={menuRef} className="track-context-menu" style={{ top: menu.y, left: menu.x }}>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void playNext(menu.filePath)
              setMenu(null)
            }}
          >
            ▶ Play Next
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void addToQueue(menu.filePath)
              setMenu(null)
            }}
          >
            + Add to Queue
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void setFavorite(menu.filePath, !menu.favorite)
              setMenu(null)
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24"
              fill={menu.favorite ? 'currentColor' : 'none'}
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0 }}>
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            {menu.favorite ? 'Remove from Favorites' : 'Add to Favorites'}
          </button>
        </div>
      )}
    </div>
  )
}
