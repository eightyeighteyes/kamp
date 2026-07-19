import React from 'react'
import { ContextMenu } from './ContextMenu'
import { RemoveFromQueueIcon } from './TransportIcons'

interface Props {
  x: number
  y: number
  genre: string
  onRemove: () => void
  onClose: () => void
}

// Right-click menu for a genre entry in the Collection panel (KAMP-606). The
// remove action is destructive (strips the genre from every track's DB row and
// file tag), so the caller gates it behind a confirmation modal.
export function GenreContextMenu({ x, y, onRemove, onClose }: Props): React.JSX.Element {
  return (
    <ContextMenu x={x} y={y} onClose={onClose}>
      <button
        className="track-context-menu-item"
        onClick={() => {
          onRemove()
          onClose()
        }}
      >
        <span
          style={{ marginRight: 6, verticalAlign: 'middle', flexShrink: 0, display: 'inline-flex' }}
        >
          <RemoveFromQueueIcon size={12} />
        </span>
        Remove Genre from Collection
      </button>
    </ContextMenu>
  )
}
