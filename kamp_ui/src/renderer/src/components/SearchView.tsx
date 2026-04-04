import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album, Track } from '../api/client'
import { SortControl } from './SortControl'

type AlbumMenu = { x: number; y: number; album: Album }
type TrackMenu = { x: number; y: number; filePath: string; favorite: boolean }

function SearchAlbumCard({
  album,
  onContextMenu
}: {
  album: Album
  onContextMenu: (e: React.MouseEvent, album: Album) => void
}): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artLoaded, setArtLoaded] = useState(false)

  const handleClick = (): void => {
    void setActiveView('library')
    void selectAlbum(album)
    void setSearchQuery('')
  }

  return (
    <div
      className="search-album-card"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
      onContextMenu={(e) => onContextMenu(e, album)}
    >
      <div className={`search-album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
          />
        )}
      </div>
      <div className="search-album-info">
        <div className="search-album-title">{album.album}</div>
        <div className="search-album-artist">{album.album_artist}</div>
        <div className="search-album-year">{album.year}</div>
      </div>
    </div>
  )
}

function SearchTrackRow({
  track,
  onContextMenu
}: {
  track: Track
  onContextMenu: (e: React.MouseEvent, track: Track) => void
}): React.JSX.Element {
  const playTrack = useStore((s) => s.playTrack)
  const setSearchQuery = useStore((s) => s.setSearchQuery)

  const handleClick = (): void => {
    void playTrack(track.album_artist, track.album, track.track_number - 1)
    void setSearchQuery('')
  }

  return (
    <div
      className="search-track-row"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
      onContextMenu={(e) => onContextMenu(e, track)}
    >
      <span className="search-track-fav" aria-hidden="true">
        {track.favorite ? '♥' : ''}
      </span>
      <span className="search-track-title">{track.title}</span>
      <span className="search-track-meta">
        {track.artist} — {track.album}
      </span>
    </div>
  )
}

export function SearchView(): React.JSX.Element {
  const results = useStore((s) => s.searchResults)
  const query = useStore((s) => s.searchQuery)
  const playAlbumNext = useStore((s) => s.playAlbumNext)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const setFavorite = useStore((s) => s.setFavorite)

  const [albumMenu, setAlbumMenu] = useState<AlbumMenu | null>(null)
  const [trackMenu, setTrackMenu] = useState<TrackMenu | null>(null)
  const albumMenuRef = useRef<HTMLDivElement>(null)
  const trackMenuRef = useRef<HTMLDivElement>(null)

  // Dismiss album menu on click outside.
  useEffect(() => {
    if (!albumMenu) return
    const handler = (e: MouseEvent): void => {
      if (albumMenuRef.current && !albumMenuRef.current.contains(e.target as Node)) {
        setAlbumMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [albumMenu])

  // Dismiss track menu on click outside.
  useEffect(() => {
    if (!trackMenu) return
    const handler = (e: MouseEvent): void => {
      if (trackMenuRef.current && !trackMenuRef.current.contains(e.target as Node)) {
        setTrackMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [trackMenu])

  // Flip menus toward the cursor if they would overflow the window edge.
  useLayoutEffect(() => {
    if (!albumMenu || !albumMenuRef.current) return
    const el = albumMenuRef.current
    const rect = el.getBoundingClientRect()
    if (rect.right > window.innerWidth) el.style.left = `${albumMenu.x - rect.width}px`
    if (rect.bottom > window.innerHeight) el.style.top = `${albumMenu.y - rect.height}px`
  }, [albumMenu])

  useLayoutEffect(() => {
    if (!trackMenu || !trackMenuRef.current) return
    const el = trackMenuRef.current
    const rect = el.getBoundingClientRect()
    if (rect.right > window.innerWidth) el.style.left = `${trackMenu.x - rect.width}px`
    if (rect.bottom > window.innerHeight) el.style.top = `${trackMenu.y - rect.height}px`
  }, [trackMenu])

  if (!results) {
    return <div className="search-empty">Searching…</div>
  }

  const hasAlbums = results.albums.length > 0
  const hasTracks = results.tracks.length > 0

  if (!hasAlbums && !hasTracks) {
    return <div className="search-empty">No results for &ldquo;{query}&rdquo;</div>
  }

  return (
    <div className="search-view">
      {hasAlbums && <SortControl />}
      {hasAlbums && (
        <section className="search-section">
          <h2 className="search-section-title">Albums</h2>
          <div className="search-album-grid">
            {results.albums.map((album) => (
              <SearchAlbumCard
                key={`${album.album_artist}\0${album.album}`}
                album={album}
                onContextMenu={(e, a) => {
                  e.preventDefault()
                  setTrackMenu(null)
                  setAlbumMenu({ x: e.clientX, y: e.clientY, album: a })
                }}
              />
            ))}
          </div>
        </section>
      )}
      {hasTracks && (
        <section className="search-section">
          <h2 className="search-section-title">Tracks</h2>
          <div className="search-track-list">
            {results.tracks.map((track) => (
              <SearchTrackRow
                key={track.file_path}
                track={track}
                onContextMenu={(e, t) => {
                  e.preventDefault()
                  setAlbumMenu(null)
                  setTrackMenu({
                    x: e.clientX,
                    y: e.clientY,
                    filePath: t.file_path,
                    favorite: t.favorite
                  })
                }}
              />
            ))}
          </div>
        </section>
      )}

      {albumMenu && (
        <div
          ref={albumMenuRef}
          className="track-context-menu"
          style={{ top: albumMenu.y, left: albumMenu.x }}
        >
          <button
            className="track-context-menu-item"
            onClick={() => {
              void playAlbumNext(albumMenu.album.album_artist, albumMenu.album.album)
              setAlbumMenu(null)
            }}
          >
            ▶ Play Next
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void addAlbumToQueue(albumMenu.album.album_artist, albumMenu.album.album)
              setAlbumMenu(null)
            }}
          >
            + Add to Queue
          </button>
        </div>
      )}

      {trackMenu && (
        <div
          ref={trackMenuRef}
          className="track-context-menu"
          style={{ top: trackMenu.y, left: trackMenu.x }}
        >
          <button
            className="track-context-menu-item"
            onClick={() => {
              void playNext(trackMenu.filePath)
              setTrackMenu(null)
            }}
          >
            ▶ Play Next
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void addToQueue(trackMenu.filePath)
              setTrackMenu(null)
            }}
          >
            + Add to Queue
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void setFavorite(trackMenu.filePath, !trackMenu.favorite)
              setTrackMenu(null)
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24"
              fill={trackMenu.favorite ? 'currentColor' : 'none'}
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0 }}>
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            {trackMenu.favorite ? 'Remove from Favorites' : 'Add to Favorites'}
          </button>
        </div>
      )}
    </div>
  )
}
