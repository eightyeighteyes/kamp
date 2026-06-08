import React from 'react'
import { useStore } from '../store'
import { getTracksForAlbum, downloadAlbum } from '../api/client'
import type { Album } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { ContextMenuSubmenu } from './ContextMenuSubmenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import {
  DownloadArrowIcon,
  FavoriteIcon,
  PlayNextIcon,
  QueueAddIcon,
  ShareIcon
} from './TransportIcons'

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
  const markAlbumDownloading = useStore((s) => s.markAlbumDownloading)
  const showFlashToast = useStore((s) => s.showFlashToast)
  const playlists = useStore((s) => s.library.playlists)
  const addAlbumToPlaylist = useStore((s) => s.addAlbumToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)

  const handleAddToPlaylist = (playlistId: number): void => {
    void addAlbumToPlaylist(playlistId, album.album_artist, album.album)
    onClose()
  }

  const handleNewPlaylist = (): void => {
    onClose()
    void (async () => {
      const pl = await createPlaylist('New Playlist')
      // Navigate first so the user sees the playlist immediately;
      // then add the tracks in the background.
      setCollectionType('playlists')
      await selectPlaylist(pl)
      await addAlbumToPlaylist(pl.id, album.album_artist, album.album)
    })()
  }

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
      {album.album_url && (
        <button
          className="track-context-menu-item"
          onClick={() => {
            void navigator.clipboard.writeText(album.album_url!)
            showFlashToast(`Copied link to ${album.album}`)
            onClose()
          }}
        >
          <span
            style={{
              marginRight: 6,
              verticalAlign: 'middle',
              flexShrink: 0,
              display: 'inline-flex'
            }}
          >
            <ShareIcon size={12} />
          </span>
          Copy Bandcamp link
        </button>
      )}
      {album.source !== 'local' && (
        <button
          className="track-context-menu-item track-context-menu-item--action"
          onClick={async () => {
            const tracks = await getTracksForAlbum(album.album_artist, album.album)
            const saleItemId =
              tracks[0]?.file_path.split('bandcamp:')[1]?.replace(/^\/+/, '').split('/')[0] ?? null
            if (saleItemId) {
              markAlbumDownloading(saleItemId)
              void downloadAlbum(saleItemId)
            }
            onClose()
          }}
        >
          <span
            style={{
              marginRight: 6,
              verticalAlign: 'middle',
              flexShrink: 0,
              display: 'inline-flex'
            }}
          >
            <DownloadArrowIcon size={12} />
          </span>
          Download this album
        </button>
      )}
      <ContextMenuSubmenu label="Add to Playlist">
        {playlists.map((pl) => (
          <button
            key={pl.id}
            className="track-context-menu-item"
            onClick={() => handleAddToPlaylist(pl.id)}
          >
            {pl.title}
          </button>
        ))}
        {playlists.length > 0 && <div className="track-context-menu-divider" />}
        <button className="track-context-menu-item" onClick={handleNewPlaylist}>
          New Playlist
        </button>
      </ContextMenuSubmenu>
      {album.source === 'local' && (
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
      )}
    </ContextMenu>
  )
}
