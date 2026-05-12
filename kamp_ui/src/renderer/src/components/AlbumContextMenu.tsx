import React from 'react'
import { useStore } from '../store'
import { getTracksForAlbum } from '../api/client'
import type { Album } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import { PlayNextIcon, QueueAddIcon } from './TransportIcons'

interface Props {
  x: number
  y: number
  album: Album
  onClose: () => void
}

export function AlbumContextMenu({ x, y, album, onClose }: Props): React.JSX.Element {
  const playAlbumNext = useStore((s) => s.playAlbumNext)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const setAlbumFavorite = useStore((s) => s.setAlbumFavorite)

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void playAlbumNext(album.album_artist, album.album, album.file_path)
          onClose()
        }}
      >
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <PlayNextIcon size={12} />
        </span>
        Play Next
      </button>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void addAlbumToQueue(album.album_artist, album.album, album.file_path)
          onClose()
        }}
      >
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <QueueAddIcon size={12} />
        </span>
        Add to Queue
      </button>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void setAlbumFavorite(album.album_artist, album.album, !album.favorite)
          onClose()
        }}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill={album.favorite ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0 }}
        >
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
        </svg>
        {album.favorite ? 'Remove from Favorites' : 'Add to Favorites'}
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
          onClose()
        }}
      >
        {revealInFinderLabel()}
      </button>
    </ContextMenu>
  )
}
