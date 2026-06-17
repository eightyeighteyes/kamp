import React, { useEffect, useRef, useState } from 'react'
import { getTopTracks, artUrl } from '../../api/client'
import type { Track } from '../../api/client'
import { useStore } from '../../store'
import { TrackContextMenu } from '../TrackContextMenu'
import { PlayIcon } from '../TransportIcons'
import type { ModuleProps, DisplayStyle } from './registry'

type MenuPos = { x: number; y: number; track: Track }

const SCROLL_PX = 500

function useTrackNav(): (track: Track) => void {
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const setFlashTrackId = useStore((s) => s.setFlashTrackId)
  return (track: Track): void => {
    const album = albums.find((a) =>
      a.missing_album
        ? a.file_path === track.file_path
        : a.album_artist === track.album_artist && a.album === track.album
    )
    if (!album) return
    void setActiveView('library')
    void selectAlbum(album)
    setFlashTrackId(track.id)
  }
}

function TrackCard({ track }: { track: Track }): React.JSX.Element {
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const navigateTo = useTrackNav()
  const [artLoaded, setArtLoaded] = useState(false)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)
  const isCurrent = currentTrack?.id === track.id

  return (
    <div
      className={`track-card${isCurrent ? ' playing' : ''}`}
      tabIndex={0}
      draggable
      onClick={() => navigateTo(track)}
      onKeyDown={(e) => e.key === 'Enter' && navigateTo(track)}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, track })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-track-path', track.file_path)
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`track-card-art${artLoaded ? ' has-art' : ''}`}>
        {!artError && (
          <img
            className="track-card-art-img"
            src={artUrl(track.album_artist, track.album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => {
              setArtLoaded(false)
              setArtError(true)
            }}
          />
        )}
        {playing && isCurrent && (
          <div className="now-playing-badge">
            <PlayIcon size={10} />
          </div>
        )}
      </div>
      <div className="track-card-info">
        <div className="track-card-title">{track.title}</div>
        <div className="track-card-artist">{track.artist}</div>
        <div className="track-card-play-count">{track.play_count}×</div>
      </div>
      {menu && (
        <TrackContextMenu x={menu.x} y={menu.y} track={menu.track} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

function TrackListRow({ track }: { track: Track }): React.JSX.Element {
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const navigateTo = useTrackNav()
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)
  const isCurrent = currentTrack?.id === track.id

  return (
    <div
      className={`module-list-row${isCurrent ? ' playing' : ''}`}
      tabIndex={0}
      draggable
      onClick={() => navigateTo(track)}
      onKeyDown={(e) => e.key === 'Enter' && navigateTo(track)}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, track })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-track-path', track.file_path)
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className="module-list-thumb">
        {!artError && (
          <img
            src={artUrl(track.album_artist, track.album)}
            alt=""
            onError={() => setArtError(true)}
          />
        )}
        {playing && isCurrent && (
          <div className="module-list-playing-badge">
            <PlayIcon size={16} />
          </div>
        )}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">{track.title}</div>
        <div className="module-list-artist">{track.artist}</div>
      </div>
      <span className="track-card-play-count">{track.play_count}×</span>
      {menu && (
        <TrackContextMenu x={menu.x} y={menu.y} track={menu.track} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

function TrackShelf({ tracks }: { tracks: Track[] }): React.JSX.Element {
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
      <div className="module-shelf" ref={scrollRef} role="region" aria-label="Top tracks shelf">
        {tracks.map((track) => (
          <TrackCard key={track.id} track={track} />
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

export function TopTracksConfig(): React.JSX.Element {
  const storeCount = useStore((s) => s.topTracksCount)
  const displayStyle = useStore((s) => s.moduleDisplayStyles['kamp.top-tracks'] ?? 'shelf')
  const setCount = useStore((s) => s.setTopTracksCount)
  const setDisplayStyle = useStore((s) => s.setModuleDisplayStyle)
  const [localCount, setLocalCount] = useState(storeCount)

  useEffect(() => {
    const id = setTimeout(() => setCount(localCount), 400)
    return () => clearTimeout(id)
  }, [localCount, setCount])

  return (
    <div className="module-config-row">
      <label className="module-config-field">
        <span>Tracks</span>
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
          onChange={(e) => setDisplayStyle('kamp.top-tracks', e.target.value as DisplayStyle)}
        >
          <option value="shelf">Shelf</option>
          <option value="grid">Grid</option>
          <option value="list">List</option>
        </select>
      </label>
    </div>
  )
}

export function TopTracksModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.topTracksCount)
  const currentFilePath = useStore((s) => s.player?.current_track?.file_path ?? null)
  const serverStatus = useStore((s) => s.serverStatus)
  const [tracks, setTracks] = useState<Track[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (serverStatus !== 'connected') return
    getTopTracks(count > 0 ? count : 50)
      .then(setTracks)
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

  if (tracks.length === 0) {
    return <div className="module-empty">No tracks played yet.</div>
  }

  if (displayStyle === 'list') {
    return (
      <div className="module-list">
        {tracks.map((track) => (
          <TrackListRow key={track.id} track={track} />
        ))}
      </div>
    )
  }

  if (displayStyle === 'grid') {
    return (
      <div className="album-grid module-grid">
        {tracks.map((track) => (
          <TrackCard key={track.id} track={track} />
        ))}
      </div>
    )
  }

  return <TrackShelf tracks={tracks} />
}
