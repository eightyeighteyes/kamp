import React, { useState } from 'react'
import type { Album } from '../../api/client'
import { artUrl } from '../../api/client'
import { useStore } from '../../store'
import { AlbumContextMenu } from '../AlbumContextMenu'
import { PlayIcon } from '../TransportIcons'

type MenuPos = { x: number; y: number }

interface ListViewProps {
  albums: Album[]
  showPlayCount?: boolean
}

function ListRow({
  album,
  showPlayCount = false
}: {
  album: Album
  showPlayCount?: boolean
}): React.JSX.Element {
  const selectAlbum = useStore((s) => s.selectAlbum)
  const setActiveView = useStore((s) => s.setActiveView)
  const activeView = useStore((s) => s.activeView)
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const [menu, setMenu] = useState<MenuPos | null>(null)

  const isActive = album.missing_album
    ? currentTrack?.file_path === album.file_path
    : currentTrack?.album === album.album && currentTrack?.album_artist === album.album_artist

  const handleSelect = (): void => {
    if (activeView !== 'library') void setActiveView('library')
    void selectAlbum(album)
  }

  return (
    <div
      className={`module-list-row${isActive ? ' playing' : ''}`}
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
      <div className="module-list-thumb">
        {album.has_art && (
          <img
            src={artUrl(album.album_artist, album.album, album.file_path, album.art_version)}
            alt=""
          />
        )}
        {playing && isActive && (
          <div className="module-list-playing-badge">
            <PlayIcon size={16} />
          </div>
        )}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">
          {album.missing_album ? <em>{album.album}</em> : album.album}
        </div>
        <div className="module-list-artist">{album.album_artist}</div>
      </div>
      {showPlayCount && album.play_count_avg > 0 && (
        <span className="module-list-play-count">avg {album.play_count_avg.toFixed(1)}</span>
      )}
      {menu && (
        <AlbumContextMenu x={menu.x} y={menu.y} album={album} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

export function ListView({ albums, showPlayCount = false }: ListViewProps): React.JSX.Element {
  return (
    <div className="module-list">
      {albums.map((album) => (
        <ListRow
          key={album.missing_album ? album.file_path : `${album.album_artist}\0${album.album}`}
          album={album}
          showPlayCount={showPlayCount}
        />
      ))}
    </div>
  )
}
