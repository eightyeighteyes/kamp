import React, { useState } from 'react'
import { useStore } from '../store'
import { MODULE_REGISTRY } from './modules/registry'
import type { ModuleRegistration } from './modules/registry'
import { ContextMenu } from './ContextMenu'

type Menu = { x: number; y: number; id: string }

export function BaseKampView(): React.JSX.Element {
  const moduleOrder = useStore((s) => s.moduleOrder)
  const setModuleOrder = useStore((s) => s.setModuleOrder)
  const moduleDisplayStyles = useStore((s) => s.moduleDisplayStyles)
  const editMode = useStore((s) => s.baseKampEditMode)
  const toggleEditMode = useStore((s) => s.toggleBaseKampEditMode)
  const [menu, setMenu] = useState<Menu | null>(null)
  const [dragId, setDragId] = useState<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)

  const modules = moduleOrder
    .map((id) => MODULE_REGISTRY.find((m) => m.id === id))
    .filter((m): m is ModuleRegistration => m !== undefined)

  function moveModule(id: string, direction: 'top' | 'up' | 'down' | 'bottom'): void {
    const idx = moduleOrder.indexOf(id)
    if (idx === -1) return
    const next = [...moduleOrder]
    next.splice(idx, 1)
    if (direction === 'top') next.unshift(id)
    else if (direction === 'bottom') next.push(id)
    else if (direction === 'up') next.splice(idx - 1, 0, id)
    else next.splice(idx + 1, 0, id)
    setModuleOrder(next)
  }

  function dropModule(targetId: string): void {
    if (!dragId || dragId === targetId) return
    const fromIdx = moduleOrder.indexOf(dragId)
    const toIdx = moduleOrder.indexOf(targetId)
    if (fromIdx === -1 || toIdx === -1) return
    const next = [...moduleOrder]
    next.splice(fromIdx, 1)
    next.splice(toIdx, 0, dragId)
    setModuleOrder(next)
  }

  if (modules.length === 0) {
    return <div className="base-kamp-empty">No modules configured.</div>
  }

  const menuIdx = menu ? moduleOrder.indexOf(menu.id) : -1
  const menuAtTop = menuIdx === 0
  const menuAtBottom = menuIdx === moduleOrder.length - 1

  return (
    <div className="base-kamp">
      <div className="base-kamp-header">
        <button
          className={`base-kamp-gear${editMode ? ' active' : ''}`}
          onClick={toggleEditMode}
          title={editMode ? 'Done' : 'Customize'}
        >
          ⚙
        </button>
      </div>
      {modules.map((mod) => (
        <section
          key={mod.id}
          className={`base-kamp-module${dragOverId === mod.id && dragId !== mod.id ? ' drag-over' : ''}`}
          onDragOver={(e) => {
            if (!dragId) return
            e.preventDefault()
            e.dataTransfer.dropEffect = 'move'
            if (dragId !== mod.id) setDragOverId(mod.id)
          }}
          onDragLeave={() => setDragOverId(null)}
          onDrop={(e) => {
            e.preventDefault()
            dropModule(mod.id)
            setDragId(null)
            setDragOverId(null)
          }}
        >
          <div className="base-kamp-module-label">
            {editMode && (
              <button
                className="base-kamp-drag-handle"
                title="Drag to reorder, right-click for options"
                draggable
                onDragStart={(e) => {
                  setDragId(mod.id)
                  e.dataTransfer.setData('text/kamp-module-id', mod.id)
                  e.dataTransfer.effectAllowed = 'move'
                }}
                onDragEnd={() => {
                  setDragId(null)
                  setDragOverId(null)
                }}
                onContextMenu={(e) => {
                  e.preventDefault()
                  setMenu({ x: e.clientX, y: e.clientY, id: mod.id })
                }}
              >
                <svg width="10" height="14" viewBox="0 0 10 14" aria-hidden="true">
                  <circle cx="3" cy="3" r="1.5" fill="currentColor" />
                  <circle cx="7" cy="3" r="1.5" fill="currentColor" />
                  <circle cx="3" cy="7" r="1.5" fill="currentColor" />
                  <circle cx="7" cy="7" r="1.5" fill="currentColor" />
                  <circle cx="3" cy="11" r="1.5" fill="currentColor" />
                  <circle cx="7" cy="11" r="1.5" fill="currentColor" />
                </svg>
              </button>
            )}
            {mod.title}
          </div>
          <div className={`base-kamp-config-row${editMode ? ' visible' : ''}`}>
            {mod.configComponent && <mod.configComponent />}
          </div>
          <div className="base-kamp-module-body">
            <mod.component displayStyle={moduleDisplayStyles[mod.id] ?? 'shelf'} />
          </div>
        </section>
      ))}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          <button
            className="track-context-menu-item"
            disabled={menuAtTop}
            onClick={() => {
              moveModule(menu.id, 'top')
              setMenu(null)
            }}
          >
            Move to Top
          </button>
          <button
            className="track-context-menu-item"
            disabled={menuAtTop}
            onClick={() => {
              moveModule(menu.id, 'up')
              setMenu(null)
            }}
          >
            Move Up
          </button>
          <button
            className="track-context-menu-item"
            disabled={menuAtBottom}
            onClick={() => {
              moveModule(menu.id, 'down')
              setMenu(null)
            }}
          >
            Move Down
          </button>
          <button
            className="track-context-menu-item"
            disabled={menuAtBottom}
            onClick={() => {
              moveModule(menu.id, 'bottom')
              setMenu(null)
            }}
          >
            Move to Bottom
          </button>
        </ContextMenu>
      )}
    </div>
  )
}
