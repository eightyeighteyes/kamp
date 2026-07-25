import React, { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useMenuBounds } from '../hooks/useMenuBounds'

interface Props {
  x: number
  y: number
  onClose: () => void
  children: React.ReactNode
}

export function ContextMenu({ x, y, onClose, children }: Props): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onMouseDown = (e: MouseEvent): void => {
      // Close unless the click is inside this menu or any portal submenu (.track-context-menu).
      // Portal submenus are rendered in document.body, outside ref.current, so a simple
      // ref.current.contains() check would close the parent when clicking a submenu item.
      const insideAnyMenu = (e.target as Element).closest?.('.track-context-menu') !== null
      if (!insideAnyMenu) {
        onClose()
      }
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  useMenuBounds(ref, true)

  return createPortal(
    <div
      ref={ref}
      className="track-context-menu"
      style={{ top: y, left: x }}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </div>,
    document.body
  )
}
