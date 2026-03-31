import React, { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { artUrl } from '../api/client'
import type { Album } from '../api/client'
import { SortControl } from './SortControl'

type ContextMenu = { x: number; y: number; album: Album }

function AlbumCard({
  album,
  onContextMenu
}: {
  album: Album
  onContextMenu: (e: React.MouseEvent, album: Album) => void
}): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const [artLoaded, setArtLoaded] = useState(false)

  const isActive =
    currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  return (
    <div
      className={`album-card${isActive ? ' playing' : ''}`}
      tabIndex={0}
      draggable
      onClick={() => selectAlbum(album)}
      onKeyDown={(e) => e.key === 'Enter' && selectAlbum(album)}
      onContextMenu={(e) => onContextMenu(e, album)}
      onDragStart={(e) => {
        e.dataTransfer.setData(
          'text/kamp-album',
          JSON.stringify({ album_artist: album.album_artist, album: album.album })
        )
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            className="album-art-img"
            src={artUrl(album.album_artist, album.album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
          />
        )}
        {playing && isActive && <div className="now-playing-badge">▶</div>}
      </div>
      <div className="album-info">
        <div className="album-title">{album.album}</div>
        <div className="album-artist">{album.album_artist}</div>
        <div className="album-year">{album.year}</div>
      </div>
    </div>
  )
}

export function AlbumGrid(): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectedArtist = useStore((s) => s.library.selectedArtist)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const playAlbumNext = useStore((s) => s.playAlbumNext)

  const [menu, setMenu] = useState<ContextMenu | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

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

  const visible = selectedArtist ? albums.filter((a) => a.album_artist === selectedArtist) : albums

  return (
    <div className="album-grid-container">
      <SortControl />
      {visible.length === 0 ? (
        <div className="album-grid-empty">
          {albums.length === 0 ? 'No albums in library.' : 'No albums for this artist.'}
        </div>
      ) : (
        <div className="album-grid">
          {visible.map((album) => (
            <AlbumCard
              key={`${album.album_artist}\0${album.album}`}
              album={album}
              onContextMenu={(e, a) => {
                e.preventDefault()
                setMenu({ x: e.clientX, y: e.clientY, album: a })
              }}
            />
          ))}
        </div>
      )}

      {menu && (
        <div ref={menuRef} className="track-context-menu" style={{ top: menu.y, left: menu.x }}>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void playAlbumNext(menu.album.album_artist, menu.album.album)
              setMenu(null)
            }}
          >
            ▶ Play Next
          </button>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void addAlbumToQueue(menu.album.album_artist, menu.album.album)
              setMenu(null)
            }}
          >
            + Add to Queue
          </button>
        </div>
      )}
    </div>
  )
}
