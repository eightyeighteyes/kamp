import React, { useState } from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { ContextMenuSubmenu } from './ContextMenuSubmenu'
import { truncateTitle } from '../utils/truncateTitle'
import {
  FavoriteIcon,
  GoToAlbumIcon,
  GoToArtistIcon,
  PlayNextIcon,
  RemoveFromQueueIcon
} from './TransportIcons'
import type { Track } from '../api/client'
import { getPlaylistTracks } from '../api/client'
import { DuplicatePlaylistTrackModal } from './DuplicatePlaylistTrackModal'

interface Props {
  x: number
  y: number
  trackIdx: number | null
  track?: Track // right-clicked item — used for navigation only
  selectedTracks: Track[] // all selected items — used for bulk favorites
  unplayedSelectedIndices: number[] // display indices of unplayed selected tracks
  position: number // currently playing track's display index
  onClearSelection: () => void
  onClose: () => void
}

type DuplicateModalState = {
  playlistId: number
  playlistName: string
  allIds: number[]
  uniqueIds: number[]
}

export function QueueContextMenu({
  x,
  y,
  trackIdx,
  track,
  selectedTracks,
  unplayedSelectedIndices,
  position,
  onClearSelection,
  onClose
}: Props): React.JSX.Element {
  const albums = useStore((s) => s.library.albums)
  const selectAlbum = useStore((s) => s.selectAlbum)
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const clearQueue = useStore((s) => s.clearQueue)
  const clearRemainingQueue = useStore((s) => s.clearRemainingQueue)
  const removeFromQueue = useStore((s) => s.removeFromQueue)
  const moveQueueTrack = useStore((s) => s.moveQueueTrack)
  const reorderQueue = useStore((s) => s.reorderQueue)
  const queueLength = useStore((s) => s.queue?.tracks.length ?? 0)
  const setFavorites = useStore((s) => s.setFavorites)
  const playlists = useStore((s) => s.library.playlists)
  const addTrackToPlaylist = useStore((s) => s.addTrackToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)
  const showFlashToast = useStore((s) => s.showFlashToast)

  const [duplicateModal, setDuplicateModal] = useState<DuplicateModalState | null>(null)

  // Targets for playlist operations: all selected tracks, or the right-clicked
  // track alone when there is no selection (consistent with favorites/remove).
  const playlistTargets = selectedTracks.length > 0 ? selectedTracks : track ? [track] : []

  const handleAddToPlaylist = (playlistId: number): void => {
    const pl = playlists.find((p) => p.id === playlistId)
    const allIds = playlistTargets.map((t) => t.id)
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
    if (playlistTargets.length === 0) return
    onClose()
    void (async () => {
      const pl = await createPlaylist('New Playlist')
      setCollectionType('playlists')
      await selectPlaylist(pl)
      for (const t of playlistTargets) {
        await addTrackToPlaylist(pl.id, t.id)
      }
    })()
  }

  // For the favorites label: apply to all selected; label reflects majority state.
  const allFavorited = selectedTracks.length > 0 && selectedTracks.every((t) => t.favorite)

  return (
    <>
      {!duplicateModal && (
        <ContextMenu x={x} y={y} onClose={onClose}>
          {track && (
            <>
              <button
                className="track-context-menu-item"
                onClick={() => {
                  const found = albums.find(
                    (a) =>
                      (a.album === track.album || a.display_album === track.album) &&
                      (a.album_artist === track.album_artist ||
                        a.display_album_artist === track.album_artist)
                  ) ?? {
                    album_artist: track.album_artist,
                    album: track.album,
                    release_date: '',
                    track_count: 0,
                    has_art: false,
                    missing_album: false,
                    track_id: null,
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
              {selectedTracks.length > 0 && (
                <button
                  className="track-context-menu-item"
                  onClick={() => {
                    void setFavorites(selectedTracks, !allFavorited)
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
              {unplayedSelectedIndices.length > 0 && position >= 0 && (
                <button
                  className="track-context-menu-item"
                  onClick={() => {
                    if (unplayedSelectedIndices.length === 1) {
                      void moveQueueTrack(unplayedSelectedIndices[0], position + 1)
                    } else {
                      const selectedSet = new Set(unplayedSelectedIndices)
                      const nonSelected = Array.from({ length: queueLength }, (_, i) => i).filter(
                        (i) => !selectedSet.has(i)
                      )
                      const insertAt = nonSelected.indexOf(position) + 1
                      const newOrder = [
                        ...nonSelected.slice(0, insertAt),
                        ...unplayedSelectedIndices,
                        ...nonSelected.slice(insertAt)
                      ]
                      void reorderQueue(newOrder)
                    }
                    onClearSelection()
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
                  Queue Next
                </button>
              )}
              {unplayedSelectedIndices.length > 0 && (
                <button
                  className="track-context-menu-item"
                  onClick={() => {
                    void removeFromQueue(unplayedSelectedIndices)
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
                  Remove from Queue
                </button>
              )}
              <div className="track-context-menu-divider" />
            </>
          )}
          <button
            className="track-context-menu-item"
            onClick={() => {
              void clearQueue()
              onClose()
            }}
          >
            Clear Queue
          </button>
          {trackIdx !== null && (
            <button
              className="track-context-menu-item"
              onClick={() => {
                void clearRemainingQueue(trackIdx)
                onClose()
              }}
            >
              Clear Remaining
            </button>
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
