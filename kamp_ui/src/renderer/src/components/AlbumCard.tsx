import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../store'
import { artUrl, getTracksForAlbum } from '../api/client'
import type { Album } from '../api/client'
import { useMenuBounds } from '../hooks/useMenuBounds'

type MenuPos = { x: number; y: number }

export function AlbumCard({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const playAlbumNext = useStore((s) => s.playAlbumNext)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const [artLoaded, setArtLoaded] = useState(false)
  const [menu, setMenu] = useState<MenuPos | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const isActive = album.missing_album
    ? currentTrack?.file_path === album.file_path
    : currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

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

  useMenuBounds(menuRef, menu)

  const handleSelect = (): void => {
    if (activeView !== 'library') void setActiveView('library')
    void selectAlbum(album)
  }

  return (
    <div
      className={`album-card${isActive ? ' playing' : ''}`}
      tabIndex={0}
      draggable
      onClick={handleSelect}
      onKeyDown={(e) => e.key === 'Enter' && handleSelect()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData(
          'text/kamp-album',
          JSON.stringify({
            album_artist: album.album_artist,
            album: album.album,
            file_path: album.file_path
          })
        )
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`album-art${artLoaded ? ' has-art' : ''}`}>
        {album.has_art && (
          <img
            className="album-art-img"
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => setArtLoaded(false)}
          />
        )}
        {playing && isActive && <div className="now-playing-badge">▶</div>}
      </div>
      <div className="album-info">
        {album.missing_album ? (
          <div className="album-title">
            <em>{album.album}</em>
          </div>
        ) : (
          <div className="album-title">{album.album}</div>
        )}
        <div className="album-artist">{album.album_artist}</div>
        <div className="album-year">{album.year}</div>
      </div>

      {menu &&
        createPortal(
          <div
            ref={menuRef}
            className="track-context-menu"
            style={{ top: menu.y, left: menu.x }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="track-context-menu-item"
              onClick={() => {
                void playAlbumNext(album.album_artist, album.album, album.file_path)
                setMenu(null)
              }}
            >
              ▶ Play Next
            </button>
            <button
              className="track-context-menu-item"
              onClick={() => {
                void addAlbumToQueue(album.album_artist, album.album, album.file_path)
                setMenu(null)
              }}
            >
              + Add to Queue
            </button>
            <button
              className="track-context-menu-item"
              onClick={async () => {
                let filePath = album.file_path
                if (!filePath) {
                  const tracks = await getTracksForAlbum(album.album_artist, album.album)
                  filePath = tracks[0]?.file_path ?? ''
                }
                if (filePath) window.api.showItemInFolder(filePath)
                setMenu(null)
              }}
            >
              {window.electron.process.platform === 'darwin'
                ? '↗ Reveal in Finder'
                : window.electron.process.platform === 'win32'
                  ? '↗ Show in Explorer'
                  : '↗ Show in Files'}
            </button>
          </div>,
          document.body
        )}
    </div>
  )
}
