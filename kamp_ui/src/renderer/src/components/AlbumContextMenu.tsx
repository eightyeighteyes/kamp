import React from 'react'
import { useStore } from '../store'
import { getTracksForAlbum, downloadAlbum } from '../api/client'
import type { Album } from '../api/client'
import { ContextMenu } from './ContextMenu'
import { ContextMenuSubmenu } from './ContextMenuSubmenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import { truncateTitle } from '../utils/truncateTitle'
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
  const addTrackToPlaylist = useStore((s) => s.addTrackToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const setActiveView = useStore((s) => s.setActiveView)

  // Fetch tracks client-side so the full file_path list is available.
  // This handles missing-album tracks (keyed by file_path, not album_artist+album)
  // and avoids the server-side tracks_for_album path which has no file_path fallback.
  const addAlbumTracksToPlaylist = async (playlistId: number): Promise<void> => {
    const tracks = await getTracksForAlbum(album.album_artist, album.album, album.file_path)
    for (const t of tracks) {
      await addTrackToPlaylist(playlistId, t.file_path)
    }
  }

  const handleAddToPlaylist = (playlistId: number): void => {
    void addAlbumTracksToPlaylist(playlistId)
    const pl = playlists.find((p) => p.id === playlistId)
    if (pl) showFlashToast(`Added to ${truncateTitle(pl.title, 35)}`)
    onClose()
  }

  const handleNewPlaylist = (): void => {
    onClose()
    void (async () => {
      const pl = await createPlaylist('New Playlist')
      void setActiveView('library')
      setCollectionType('playlists')
      await selectPlaylist(pl)
      await addAlbumTracksToPlaylist(pl.id)
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
            {truncateTitle(pl.title)}
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
