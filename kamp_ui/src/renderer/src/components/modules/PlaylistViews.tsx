import React, { useRef, useState } from 'react'
import { playlistArtUrl } from '../../api/client'
import type { Playlist } from '../../api/client'
import { useStore } from '../../store'
import { PlaylistCard } from '../PlaylistCard'
import { PlaylistContextMenu } from '../PlaylistContextMenu'
import { SparkleIcon } from '../TransportIcons'

const SCROLL_PX = 500

// ---------------------------------------------------------------------------
// Shelf
// ---------------------------------------------------------------------------

export function PlaylistShelfView({ playlists }: { playlists: Playlist[] }): React.JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null)
  const scroll = (dir: 'left' | 'right'): void => {
    scrollRef.current?.scrollBy({
      left: dir === 'right' ? SCROLL_PX : -SCROLL_PX,
      behavior: 'smooth'
    })
  }
  return (
    <div className="module-shelf-wrapper">
      <button
        className="module-shelf-arrow module-shelf-arrow--left"
        onClick={() => scroll('left')}
        aria-label="Scroll left"
        tabIndex={-1}
      >
        ‹
      </button>
      <div
        className="module-shelf"
        ref={scrollRef}
        role="region"
        aria-label="Favorite playlists shelf"
      >
        {playlists.map((pl) => (
          <PlaylistCard key={pl.id} playlist={pl} />
        ))}
      </div>
      <button
        className="module-shelf-arrow module-shelf-arrow--right"
        onClick={() => scroll('right')}
        aria-label="Scroll right"
        tabIndex={-1}
      >
        ›
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Grid
// ---------------------------------------------------------------------------

export function PlaylistGridView({ playlists }: { playlists: Playlist[] }): React.JSX.Element {
  return (
    <div className="album-grid module-grid">
      {playlists.map((pl) => (
        <PlaylistCard key={pl.id} playlist={pl} />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

type MenuPos = { x: number; y: number }

function PlaylistListRow({ playlist }: { playlist: Playlist }): React.JSX.Element {
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artLoaded, setArtLoaded] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const handleSelect = (): void => {
    if (menu) return
    setCollectionType('playlists')
    void selectPlaylist(playlist)
    void setActiveView('library')
  }

  return (
    <div
      className="module-list-row"
      tabIndex={0}
      draggable
      onClick={handleSelect}
      onKeyDown={(e) => e.key === 'Enter' && handleSelect()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-playlist', String(playlist.id))
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`module-list-thumb${artLoaded ? ' has-art' : ''}`}>
        <img
          src={playlistArtUrl(playlist.id, playlist.updated_at)}
          alt=""
          onLoad={() => setArtLoaded(true)}
          onError={() => setArtLoaded(false)}
        />
      </div>
      <div className="module-list-info">
        <div className="module-list-title">{playlist.title}</div>
        <div className="module-list-artist">
          {playlist.track_count === 1 ? '1 track' : `${playlist.track_count} tracks`}
          {playlist.criteria !== null && (
            <span className="module-list-magic-badge">
              <SparkleIcon size={10} />
            </span>
          )}
        </div>
      </div>
      {menu && (
        <PlaylistContextMenu
          x={menu.x}
          y={menu.y}
          playlist={playlist}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

export function PlaylistListView({ playlists }: { playlists: Playlist[] }): React.JSX.Element {
  return (
    <div className="module-list">
      {playlists.map((pl) => (
        <PlaylistListRow key={pl.id} playlist={pl} />
      ))}
    </div>
  )
}
