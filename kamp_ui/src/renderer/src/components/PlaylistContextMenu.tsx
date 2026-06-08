import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { FavoriteIcon, PlayNextIcon, QueueAddIcon } from './TransportIcons'
import type { Playlist } from '../api/client'

interface Props {
  x: number
  y: number
  playlist: Playlist
  onClose: () => void
}

export function PlaylistContextMenu({ x, y, playlist, onClose }: Props): React.JSX.Element {
  const deletePlaylist = useStore((s) => s.deletePlaylist)
  const setPlaylistFavorite = useStore((s) => s.setPlaylistFavorite)
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const loadPlaylistTracks = useStore((s) => s.loadPlaylistTracks)

  const iconStyle = {
    marginRight: 6,
    verticalAlign: 'middle',
    flexShrink: 0,
    display: 'inline-flex'
  }

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          onClose()
          void (async () => {
            await loadPlaylistTracks(playlist.id)
            const paths = useStore.getState().library.playlistTracks.map((t) => t.file_path)
            for (let i = paths.length - 1; i >= 0; i--) await playNext(paths[i])
          })()
        }}
      >
        <span style={iconStyle}>
          <PlayNextIcon size={12} />
        </span>
        Play Next
      </button>
      <button
        className="track-context-menu-item"
        onClick={() => {
          onClose()
          void (async () => {
            await loadPlaylistTracks(playlist.id)
            const paths = useStore.getState().library.playlistTracks.map((t) => t.file_path)
            for (const p of paths) await addToQueue(p)
          })()
        }}
      >
        <span style={iconStyle}>
          <QueueAddIcon size={12} />
        </span>
        Add to Queue
      </button>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void setPlaylistFavorite(playlist.id, !playlist.favorite)
          onClose()
        }}
      >
        <span style={iconStyle}>
          <FavoriteIcon active={!playlist.favorite} size={12} />
        </span>
        {playlist.favorite ? 'Remove from Favorites' : 'Add to Favorites'}
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
