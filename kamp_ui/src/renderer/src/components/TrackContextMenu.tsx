import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { ContextMenuSubmenu } from './ContextMenuSubmenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import { FavoriteIcon, PlayNextIcon, QueueAddIcon } from './TransportIcons'
import type { Track } from '../api/client'
import { truncateTitle } from '../utils/truncateTitle'

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
  const playlists = useStore((s) => s.library.playlists)
  const addTrackToPlaylist = useStore((s) => s.addTrackToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)

  // Bulk targets: the full selection when available, otherwise just this track.
  const targets = selectedTracks && selectedTracks.length > 0 ? selectedTracks : [track]
  const allFavorited = targets.every((t) => t.favorite)

  const handleAddToPlaylist = (playlistId: number): void => {
    targets.forEach((t) => void addTrackToPlaylist(playlistId, t.file_path))
    onClose()
  }

  const handleNewPlaylist = (): void => {
    onClose()
    void (async () => {
      const pl = await createPlaylist('New Playlist')
      setCollectionType('playlists')
      await selectPlaylist(pl)
      for (const t of targets) {
        await addTrackToPlaylist(pl.id, t.file_path)
      }
    })()
  }

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          onClose()
          void (async () => {
            for (let i = targets.length - 1; i >= 0; i--) await playNext(targets[i].file_path)
          })()
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
          onClose()
          void (async () => {
            for (const t of targets) await addToQueue(t.file_path)
          })()
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
          if (targets.length > 1) {
            void setFavorites(targets, !allFavorited)
          } else {
            void setFavorite(track, !track.favorite)
          }
          onClose()
        }}
      >
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <FavoriteIcon active={!allFavorited} size={12} />
        </span>
        {allFavorited ? 'Remove from Favorites' : 'Add to Favorites'}
      </button>
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
    </ContextMenu>
  )
}
