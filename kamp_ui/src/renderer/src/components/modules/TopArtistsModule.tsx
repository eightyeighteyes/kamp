import React, { useEffect, useRef, useState } from 'react'
import { getTopArtists, artUrl } from '../../api/client'
import type { Artist } from '../../api/client'
import { useStore } from '../../store'
import { ArtistContextMenu } from '../ArtistContextMenu'
import type { ModuleProps, DisplayStyle } from './registry'

type MenuPos = { x: number; y: number; artist: Artist }

const SCROLL_PX = 500

function formatPlayTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function ArtistCard({ artist }: { artist: Artist }): React.JSX.Element {
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artLoaded, setArtLoaded] = useState(false)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const navigate = (): void => {
    selectArtist(artist.name)
    void setActiveView('library')
  }

  return (
    <div
      className="track-card"
      tabIndex={0}
      draggable
      onClick={navigate}
      onKeyDown={(e) => e.key === 'Enter' && navigate()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, artist })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-artist', JSON.stringify({ name: artist.name }))
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`track-card-art${artLoaded ? ' has-art' : ''}`}>
        {!artError && artist.top_album && (
          <img
            className="track-card-art-img"
            src={artUrl(artist.name, artist.top_album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => {
              setArtLoaded(false)
              setArtError(true)
            }}
          />
        )}
      </div>
      <div className="track-card-info">
        <div className="track-card-title">{artist.name}</div>
        <div className="track-card-play-count">{formatPlayTime(artist.play_time)}</div>
      </div>
      {menu && (
        <ArtistContextMenu
          x={menu.x}
          y={menu.y}
          artist={menu.artist}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

function ArtistListRow({ artist }: { artist: Artist }): React.JSX.Element {
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const navigate = (): void => {
    selectArtist(artist.name)
    void setActiveView('library')
  }

  return (
    <div
      className="module-list-row"
      tabIndex={0}
      draggable
      onClick={navigate}
      onKeyDown={(e) => e.key === 'Enter' && navigate()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, artist })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-artist', JSON.stringify({ name: artist.name }))
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className="module-list-thumb">
        {!artError && artist.top_album && (
          <img
            src={artUrl(artist.name, artist.top_album)}
            alt=""
            onError={() => setArtError(true)}
          />
        )}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">{artist.name}</div>
        <div className="module-list-artist">{formatPlayTime(artist.play_time)}</div>
      </div>
      <span className="track-card-play-count">{formatPlayTime(artist.play_time)}</span>
      {menu && (
        <ArtistContextMenu
          x={menu.x}
          y={menu.y}
          artist={menu.artist}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

function ArtistShelf({ artists }: { artists: Artist[] }): React.JSX.Element {
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
      <div className="module-shelf" ref={scrollRef} role="region" aria-label="Top artists shelf">
        {artists.map((artist) => (
          <ArtistCard key={artist.name} artist={artist} />
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

export function TopArtistsConfig(): React.JSX.Element {
  const storeCount = useStore((s) => s.topArtistsCount)
  const displayStyle = useStore((s) => s.moduleDisplayStyles['kamp.top-artists'] ?? 'shelf')
  const setCount = useStore((s) => s.setTopArtistsCount)
  const setDisplayStyle = useStore((s) => s.setModuleDisplayStyle)
  const [localCount, setLocalCount] = useState(storeCount)

  useEffect(() => {
    const id = setTimeout(() => setCount(localCount), 400)
    return () => clearTimeout(id)
  }, [localCount, setCount])

  return (
    <div className="module-config-row">
      <label className="module-config-field">
        <span>Artists</span>
        <input
          type="number"
          min={0}
          max={50}
          value={localCount}
          onChange={(e) => setLocalCount(parseInt(e.target.value) || 0)}
        />
      </label>
      <label className="module-config-field">
        <span>Style</span>
        <select
          value={displayStyle}
          onChange={(e) => setDisplayStyle('kamp.top-artists', e.target.value as DisplayStyle)}
        >
          <option value="shelf">Shelf</option>
          <option value="grid">Grid</option>
          <option value="list">List</option>
        </select>
      </label>
    </div>
  )
}

export function TopArtistsModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.topArtistsCount)
  const currentFilePath = useStore((s) => s.player?.current_track?.file_path ?? null)
  const serverStatus = useStore((s) => s.serverStatus)
  const [artists, setArtists] = useState<Artist[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (serverStatus !== 'connected') return
    getTopArtists(count > 0 ? count : 50)
      .then(setArtists)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [count, currentFilePath, serverStatus])

  if (loading) {
    return (
      <div className="module-skeleton-row">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="module-skeleton-card" />
        ))}
      </div>
    )
  }

  if (artists.length === 0) {
    return <div className="module-empty">No artists played yet.</div>
  }

  if (displayStyle === 'list') {
    return (
      <div className="module-list">
        {artists.map((artist) => (
          <ArtistListRow key={artist.name} artist={artist} />
        ))}
      </div>
    )
  }

  if (displayStyle === 'grid') {
    return (
      <div className="album-grid module-grid">
        {artists.map((artist) => (
          <ArtistCard key={artist.name} artist={artist} />
        ))}
      </div>
    )
  }

  return <ArtistShelf artists={artists} />
}
