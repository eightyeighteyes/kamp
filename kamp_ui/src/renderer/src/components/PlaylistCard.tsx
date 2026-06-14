import React, { useState } from 'react'
import { useStore } from '../store'
import { playlistArtUrl } from '../api/client'
import type { Playlist } from '../api/client'
import { PlaylistContextMenu } from './PlaylistContextMenu'
import { FavoriteIcon, SparkleIcon } from './TransportIcons'

type MenuPos = { x: number; y: number }

export function PlaylistCard({
  playlist,
  onAfterSelect
}: {
  playlist: Playlist
  onAfterSelect?: () => void
}): React.JSX.Element {
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const [menu, setMenu] = useState<MenuPos | null>(null)
  const [artLoaded, setArtLoaded] = useState(false)

  const handleSelect = (): void => {
    if (menu) return
    setCollectionType('playlists')
    void selectPlaylist(playlist)
    onAfterSelect?.()
  }

  return (
    <div
      className="album-card"
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
      <div className={`album-art${artLoaded ? ' has-art' : ''}`}>
        <img
          className="album-art-img"
          src={playlistArtUrl(playlist.id, playlist.updated_at)}
          alt=""
          onLoad={() => setArtLoaded(true)}
          onError={() => setArtLoaded(false)}
        />
        {playlist.criteria !== null && (
          <div className="playlist-magic-badge">
            <SparkleIcon size={10} />
            <span>Magic</span>
          </div>
        )}
      </div>
      <div className="album-info">
        <div className="album-title">{playlist.title}</div>
        <div className="album-artist">
          {playlist.track_count === 1 ? '1 track' : `${playlist.track_count} tracks`}
        </div>
        {playlist.favorite && (
          <div className="album-fav-badge">
            <FavoriteIcon active size={14} />
          </div>
        )}
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
