import React from 'react'
import { ContextMenu } from './ContextMenu'
import { RemoveFromQueueIcon, MergeIcon } from './TransportIcons'

interface Props {
  x: number
  y: number
  genre: string
  onMerge: () => void
  onRemove: () => void
  onClose: () => void
}

const ICON_SPAN: React.CSSProperties = {
  marginRight: 6,
  verticalAlign: 'middle',
  flexShrink: 0,
  display: 'inline-flex'
}

// Right-click menu for a genre entry in the Collection panel (KAMP-606/607).
// Both actions are destructive (they retag every track's DB row and file tag),
// so the caller gates each behind a modal.
export function GenreContextMenu({ x, y, onMerge, onRemove, onClose }: Props): React.JSX.Element {
  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          onMerge()
          onClose()
        }}
      >
        <span style={ICON_SPAN}>
          <MergeIcon size={12} />
        </span>
        Merge Genre
      </button>
      <button
        className="track-context-menu-item"
        onClick={() => {
          onRemove()
          onClose()
        }}
      >
        <span style={ICON_SPAN}>
          <RemoveFromQueueIcon size={12} />
        </span>
        Remove Genre from Collection
      </button>
    </ContextMenu>
  )
}
