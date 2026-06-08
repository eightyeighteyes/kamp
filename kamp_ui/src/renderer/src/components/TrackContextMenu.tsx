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
}

export function TrackContextMenu({
  x,
  y,
  track,
  onClose,
  onRemoveFromPlaylist
}: Props): React.JSX.Element {
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const setFavorite = useStore((s) => s.setFavorite)
  const playlists = useStore((s) => s.library.playlists)
  const addTrackToPlaylist = useStore((s) => s.addTrackToPlaylist)
  const createPlaylist = useStore((s) => s.createPlaylist)
  const selectPlaylist = useStore((s) => s.selectPlaylist)
  const setCollectionType = useStore((s) => s.setCollectionType)

  const handleAddToPlaylist = (playlistId: number): void => {
    void addTrackToPlaylist(playlistId, track.file_path)
    onClose()
  }

  const handleNewPlaylist = (): void => {
    onClose()
    void (async () => {
      const pl = await createPlaylist('New Playlist')
      // Navigate first so the user sees the playlist immediately;
      // then add the track in the background.
      setCollectionType('playlists')
      await selectPlaylist(pl)
      await addTrackToPlaylist(pl.id, track.file_path)
    })()
  }

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void playNext(track.file_path)
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
          void addToQueue(track.file_path)
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
          void setFavorite(track, !track.favorite)
          onClose()
        }}
      >
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <FavoriteIcon active={!track.favorite} size={12} />
        </span>
        {track.favorite ? 'Remove from Favorites' : 'Add to Favorites'}
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
