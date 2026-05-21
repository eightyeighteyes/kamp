import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import { FavoriteIcon, PlayNextIcon, QueueAddIcon } from './TransportIcons'
import type { Track } from '../api/client'

interface Props {
  x: number
  y: number
  track: Track
  onClose: () => void
}

export function TrackContextMenu({ x, y, track, onClose }: Props): React.JSX.Element {
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const setFavorite = useStore((s) => s.setFavorite)

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
      <button
        className="track-context-menu-item"
        onClick={() => {
          window.api.showItemInFolder(track.file_path)
          onClose()
        }}
      >
        {revealInFinderLabel()}
      </button>
    </ContextMenu>
  )
}
