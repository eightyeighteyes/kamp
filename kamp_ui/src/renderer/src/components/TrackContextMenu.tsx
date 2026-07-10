import React, { useState } from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { ContextMenuSubmenu } from './ContextMenuSubmenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import {
  FavoriteIcon,
  GoToAlbumIcon,
  GoToArtistIcon,
  PlayNextIcon,
  QueueAddIcon
} from './TransportIcons'
import type { Track } from '../api/client'
import { getPlaylistTracks } from '../api/client'
import { truncateTitle } from '../utils/truncateTitle'
import { DuplicatePlaylistTrackModal } from './DuplicatePlaylistTrackModal'

interface Props {
  x: number
  y: number
  track: Track
  onClose: () => void
  onRemoveFromPlaylist?: () => void
  // When provided (e.g. from PlaylistView multi-select), bulk actions use this
  // list instead of the single right-clicked track.
  selectedTracks?: Track[]
}

type DuplicateModalState = {
  playlistId: number
  playlistName: string
  allIds: number[]
  uniqueIds: number[]
}

export function TrackContextMenu({
  x,
  y,
  track,
  onClose,
  onRemoveFromPlaylist,
  selectedTracks
}: Props): React.JSX.Element {
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const setFavorite = useStore((s) => s.setFavorite)
  const setFavorites = useStore((s) => s.setFavorites)
  const showFlashToast = useStore((s) => s.showFlashToast)
  const playlists = useStore((s) => s.library.playlists)
  const addTrackToPlaylist = useStore((s) => s.addTrackToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)

  const [duplicateModal, setDuplicateModal] = useState<DuplicateModalState | null>(null)

  // Bulk targets: the full selection when available, otherwise just this track.
  const targets = selectedTracks && selectedTracks.length > 0 ? selectedTracks : [track]
  const allFavorited = targets.every((t) => t.favorite)

  const handleAddToPlaylist = (playlistId: number): void => {
    const pl = playlists.find((p) => p.id === playlistId)
    const allIds = targets.map((t) => t.id)
    void (async () => {
      const existing = await getPlaylistTracks(playlistId)
      const existingSet = new Set(existing.map((t) => t.id))
      const uniqueIds = allIds.filter((id) => !existingSet.has(id))
      if (uniqueIds.length === allIds.length) {
        allIds.forEach((id) => void addTrackToPlaylist(playlistId, id))
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
    allIds.forEach((id) => void addTrackToPlaylist(playlistId, id))
    showFlashToast(`Added to ${truncateTitle(playlistName, 35)}`)
    setDuplicateModal(null)
    onClose()
  }

  const handleDuplicateConfirmUnique = (): void => {
    if (!duplicateModal) return
    const { playlistId, uniqueIds, playlistName } = duplicateModal
    uniqueIds.forEach((id) => void addTrackToPlaylist(playlistId, id))
    showFlashToast(`Added to ${truncateTitle(playlistName, 35)}`)
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
      setCollectionType('playlists')
      await selectPlaylist(pl)
      for (const t of targets) {
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
              onClose()
              void (async () => {
                for (let i = targets.length - 1; i >= 0; i--) await playNext({ id: targets[i].id })
              })()
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
              onClose()
              void (async () => {
                for (const t of targets) await addToQueue({ id: t.id })
              })()
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
              if (targets.length > 1) {
                void setFavorites(targets, !allFavorited)
              } else {
                void setFavorite(track, !track.favorite)
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
              <FavoriteIcon active={!allFavorited} size={12} />
            </span>
            {allFavorited ? 'Remove from Favorites' : 'Add to Favorites'}
          </button>
          {targets.length === 1 && (
            <>
              <button
                className="track-context-menu-item"
                onClick={() => {
                  const found = albums.find(
                    (a) => a.album_artist === track.album_artist && a.album === track.album
                  ) ?? {
                    album_artist: track.album_artist,
                    album: track.album,
                    release_date: '',
                    track_count: 0,
                    has_art: false,
                    missing_album: false,
                    file_path: '',
                    art_version: null,
                    added_at: null,
                    last_played_at: null,
                    play_count_avg: 0,
                    favorite: false,
                    has_favorite_track: false,
                    source: 'local',
                    has_remote_tracks: false
                  }
                  void setActiveView('library')
                  void selectAlbum(found)
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
                  <GoToAlbumIcon size={12} />
                </span>
                Go to Album
              </button>
              <button
                className="track-context-menu-item"
                onClick={() => {
                  void setActiveView('library')
                  setCollectionType('albums')
                  selectArtist(track.album_artist)
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
                  <GoToArtistIcon size={12} />
                </span>
                Go to Artist
              </button>
            </>
          )}
          {track.source === 'local' && (
            <button
              className="track-context-menu-item"
              onClick={() => {
                window.api.showItemInFolder(track.file_path)
                onClose()
              }}
            >
              {revealInFinderLabel()}
            </button>
          )}
          <ContextMenuSubmenu label="Add to Playlist">
            {playlists
              .filter((pl) => !pl.criteria)
              .map((pl) => (
                <button
                  key={pl.id}
                  className="track-context-menu-item"
                  onClick={() => handleAddToPlaylist(pl.id)}
                >
                  {truncateTitle(pl.title)}
                </button>
              ))}
            {playlists.some((pl) => !pl.criteria) && <div className="track-context-menu-divider" />}
            <button className="track-context-menu-item" onClick={handleNewPlaylist}>
              New Playlist
            </button>
          </ContextMenuSubmenu>
          {onRemoveFromPlaylist && (
            <>
              <div className="track-context-menu-divider" />
              <button
                className="track-context-menu-item"
                onClick={() => {
                  onRemoveFromPlaylist()
                  onClose()
                }}
              >
                Remove from Playlist
              </button>
            </>
          )}
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
