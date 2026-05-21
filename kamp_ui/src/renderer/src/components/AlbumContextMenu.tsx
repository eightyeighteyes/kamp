import React from 'react'
import { useStore } from '../store'
import { getTracksForAlbum } from '../api/client'
import type { Album } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import { FavoriteIcon, PlayNextIcon, QueueAddIcon } from './TransportIcons'

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
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <FavoriteIcon active={!album.favorite} size={12} />
        </span>
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
