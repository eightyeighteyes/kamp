import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { FavoriteIcon } from './TransportIcons'
import type { Playlist } from '../api/client'

interface Props {
  x: number
  y: number
  playlist: Playlist
  onClose: () => void
}

export function PlaylistContextMenu({ x, y, playlist, onClose }: Props): React.JSX.Element {
  const renamePlaylist = useStore((s) => s.renamePlaylist)
  const deletePlaylist = useStore((s) => s.deletePlaylist)
  const setPlaylistFavorite = useStore((s) => s.setPlaylistFavorite)

  const handleRename = (): void => {
    const newTitle = window.prompt('Rename playlist:', playlist.title)
    if (newTitle && newTitle.trim() && newTitle.trim() !== playlist.title) {
      void renamePlaylist(playlist.id, newTitle.trim())
    }
    onClose()
  }

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void setPlaylistFavorite(playlist.id, !playlist.favorite)
          onClose()
        }}
      >
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <FavoriteIcon active={!playlist.favorite} size={12} />
        </span>
        {playlist.favorite ? 'Remove from Favorites' : 'Add to Favorites'}
      </button>
      <button className="track-context-menu-item" onClick={handleRename}>
        Rename
      </button>
      <div className="track-context-menu-divider" />
      <button
        className="track-context-menu-item"
        style={{ color: 'var(--danger, #e05)' }}
        onClick={() => {
          if (window.confirm(`Delete playlist "${playlist.title}"?`)) {
            void deletePlaylist(playlist.id)
          }
          onClose()
        }}
      >
        Delete Playlist
      </button>
    </ContextMenu>
  )
}
