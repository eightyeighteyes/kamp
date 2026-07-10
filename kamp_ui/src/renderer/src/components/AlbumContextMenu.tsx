import React, { useState } from 'react'
import { useStore } from '../store'
import { getTracksForAlbum, downloadAlbum, getPlaylistTracks } from '../api/client'
import { RemoveFromQueueIcon } from './TransportIcons'
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
import { DuplicatePlaylistTrackModal } from './DuplicatePlaylistTrackModal'

interface Props {
  x: number
  y: number
  album: Album
  onClose: () => void
}

type DuplicateModalState = {
  playlistId: number
  playlistName: string
  allIds: number[]
  uniqueIds: number[]
}

export function AlbumContextMenu({ x, y, album, onClose }: Props): React.JSX.Element {
  const playAlbumNext = useStore((s) => s.playAlbumNext)
  const addAlbumToQueue = useStore((s) => s.addAlbumToQueue)
  const setAlbumFavorite = useStore((s) => s.setAlbumFavorite)
  const markAlbumDownloading = useStore((s) => s.markAlbumDownloading)
  const removeDownload = useStore((s) => s.removeDownload)
  const showFlashToast = useStore((s) => s.showFlashToast)
  const playlists = useStore((s) => s.library.playlists)
  const addTrackToPlaylist = useStore((s) => s.addTrackToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const setActiveView = useStore((s) => s.setActiveView)

  const [duplicateModal, setDuplicateModal] = useState<DuplicateModalState | null>(null)

  // Fetch tracks client-side so the album's per-track ids are available (the
  // album card itself carries no track ids). This also handles missing-album
  // tracks, which are looked up by album.file_path (a separate album-identity key).
  const handleAddToPlaylist = (playlistId: number): void => {
    const pl = playlists.find((p) => p.id === playlistId)
    void (async () => {
      const albumTracks = await getTracksForAlbum(album.album_artist, album.album, album.file_path)
      const allIds = albumTracks.map((t) => t.id)
      const existing = await getPlaylistTracks(playlistId)
      const existingSet = new Set(existing.map((t) => t.id))
      const uniqueIds = allIds.filter((id) => !existingSet.has(id))
      if (uniqueIds.length === allIds.length) {
        for (const id of allIds) await addTrackToPlaylist(playlistId, id)
        if (pl) showFlashToast(`Added to ${truncateTitle(pl.title, 35)}`)
        onClose()
      } else {
        setDuplicateModal({
          playlistId,
          playlistName: pl?.title ?? '',
          allIds,
          uniqueIds
        })
      }
    })()
  }

  const handleDuplicateConfirmAll = (): void => {
    if (!duplicateModal) return
    const { playlistId, allIds, playlistName } = duplicateModal
    void (async () => {
      for (const id of allIds) await addTrackToPlaylist(playlistId, id)
      showFlashToast(`Added to ${truncateTitle(playlistName, 35)}`)
    })()
    setDuplicateModal(null)
    onClose()
  }

  const handleDuplicateConfirmUnique = (): void => {
    if (!duplicateModal) return
    const { playlistId, uniqueIds, playlistName } = duplicateModal
    void (async () => {
      for (const id of uniqueIds) await addTrackToPlaylist(playlistId, id)
      showFlashToast(`Added to ${truncateTitle(playlistName, 35)}`)
    })()
    setDuplicateModal(null)
    onClose()
  }

  const handleDuplicateCancel = (): void => {
    setDuplicateModal(null)
    onClose()
  }

  const handleNewPlaylist = (): void => {
    onClose()
    void (async () => {
      const pl = await createPlaylist('New Playlist')
      void setActiveView('library')
      setCollectionType('playlists')
      await selectPlaylist(pl)
      const albumTracks = await getTracksForAlbum(album.album_artist, album.album, album.file_path)
      for (const t of albumTracks) {
        await addTrackToPlaylist(pl.id, t.id)
      }
    })()
  }

  return (
    <>
      {!duplicateModal && (
        <ContextMenu x={x} y={y} onClose={onClose}>
          <button
            className="track-context-menu-item"
            onClick={() => {
              void playAlbumNext(album.album_artist, album.album, album.file_path)
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
              style={{
                marginRight: 6,
                verticalAlign: 'middle',
                flexShrink: 0,
                display: 'inline-flex'
              }}
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
              style={{
                marginRight: 6,
                verticalAlign: 'middle',
                flexShrink: 0,
                display: 'inline-flex'
              }}
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
                  tracks[0]?.file_path.split('bandcamp:')[1]?.replace(/^\/+/, '').split('/')[0] ??
                  null
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
          {album.source === 'local' &&
            album.sale_item_id &&
            (album.num_streamable_tracks ?? 0) > 0 && (
              <button
                className="track-context-menu-item track-context-menu-item--action"
                onClick={() => {
                  void removeDownload(album.sale_item_id!)
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
                  <RemoveFromQueueIcon size={12} />
                </span>
                Remove download
              </button>
            )}
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
        </ContextMenu>
      )}
      {duplicateModal && (
        <DuplicatePlaylistTrackModal
          playlistName={duplicateModal.playlistName}
          hasMixed={duplicateModal.uniqueIds.length > 0}
          onAddAll={handleDuplicateConfirmAll}
          onAddUnique={handleDuplicateConfirmUnique}
          onCancel={handleDuplicateCancel}
        />
      )}
    </>
  )
}
