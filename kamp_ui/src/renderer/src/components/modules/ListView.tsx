import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Album } from '../../api/client'
import { artUrl, getTracksForAlbum } from '../../api/client'
import { useStore } from '../../store'
import { useMenuBounds } from '../../hooks/useMenuBounds'

type MenuPos = { x: number; y: number }

interface ListViewProps {
  albums: Album[]
}

function ListRow({ album }: { album: Album }): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const playAlbumNext = useStore((s) => s.playAlbumNext)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
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
      className={`module-list-row${isActive ? ' playing' : ''}`}
      tabIndex={0}
      onClick={handleSelect}
      onKeyDown={(e) => e.key === 'Enter' && handleSelect()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY })
      }}
    >
      <div className="module-list-thumb">
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
            alt=""
          />
        )}
        {playing && isActive && <div className="module-list-playing-badge">▶</div>}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">
          {album.missing_album ? <em>{album.album}</em> : album.album}
        </div>
        <div className="module-list-artist">{album.album_artist}</div>
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

export function ListView({ albums }: ListViewProps): React.JSX.Element {
  return (
    <div className="module-list">
      {albums.map((album) => (
        <ListRow
          key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
          album={album}
        />
      ))}
    </div>
  )
}
