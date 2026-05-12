import React from 'react'
import { useStore } from '../store'
import { ContextMenu } from './ContextMenu'
import { revealInFinderLabel } from '../hooks/platformLabel'
import { PlayNextIcon, QueueAddIcon } from './TransportIcons'

interface Props {
  x: number
  y: number
  filePath: string
  favorite: boolean
  onClose: () => void
}

export function TrackContextMenu({ x, y, filePath, favorite, onClose }: Props): React.JSX.Element {
  const playNext = useStore((s) => s.playNext)
  const addToQueue = useStore((s) => s.addToQueue)
  const setFavorite = useStore((s) => s.setFavorite)

  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          void playNext(filePath)
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
          void addToQueue(filePath)
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
          void setFavorite(filePath, !favorite)
          onClose()
        }}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill={favorite ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0 }}
        >
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
        </svg>
        {favorite ? 'Remove from Favorites' : 'Add to Favorites'}
      </button>
      <button
        className="track-context-menu-item"
        onClick={() => {
          window.api.showItemInFolder(filePath)
          onClose()
        }}
      >
        {revealInFinderLabel()}
      </button>
    </ContextMenu>
  )
}
