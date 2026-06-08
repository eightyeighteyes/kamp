import React, { useRef, useState } from 'react'
import { createPortal } from 'react-dom'

interface Props {
  label: string
  children: React.ReactNode
}

type SubmenuPos = { top: number; left: number }

export function ContextMenuSubmenu({ label, children }: Props): React.JSX.Element {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [pos, setPos] = useState<SubmenuPos | null>(null)

  const scheduleClose = (): void => {
    closeTimer.current = setTimeout(() => setPos(null), 120)
  }

  const cancelClose = (): void => {
    if (closeTimer.current !== null) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }

  const handleMouseEnter = (childCount: number): void => {
    cancelClose()
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect()
      const submenuWidth = 180
      const rightEdge = rect.right + submenuWidth
      const left = rightEdge <= window.innerWidth ? rect.right : rect.left - submenuWidth
      // Each menu item is ~37px; 8px top+bottom padding on the container.
      const estimatedHeight = childCount * 37 + 8
      const top = Math.min(rect.top, Math.max(0, window.innerHeight - estimatedHeight))
      setPos({ top, left })
    }
  }

  return (
    <div
      style={{ position: 'relative' }}
      onMouseEnter={() => handleMouseEnter(React.Children.count(children))}
      onMouseLeave={scheduleClose}
    >
      <button
        ref={triggerRef}
        className="track-context-menu-item"
        style={{ justifyContent: 'space-between' }}
      >
        {label}
        <span style={{ marginLeft: 8, fontSize: 10 }}>›</span>
      </button>

      {pos !== null &&
        createPortal(
          <div
            className="track-context-menu"
            style={{
              position: 'fixed',
              zIndex: 1001,
              minWidth: 180,
              top: pos.top,
              left: pos.left,
              maxHeight: 'calc(100vh - 16px)',
              overflowY: 'auto'
            }}
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            {children}
          </div>,
          document.body
        )}
    </div>
  )
}
