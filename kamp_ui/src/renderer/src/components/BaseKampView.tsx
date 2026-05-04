import React, { useLayoutEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { MODULE_REGISTRY } from './modules/registry'
import type { ModuleRegistration } from './modules/registry'

function AnimatedConfigRow({
  visible,
  children
}: {
  visible: boolean
  children: React.ReactNode
}): React.JSX.Element {
  const innerRef = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState(0)

  useLayoutEffect(() => {
    if (innerRef.current) {
      setHeight(innerRef.current.scrollHeight)
    }
  }, [])

  return (
    <div
      style={{
        height: visible ? height : 0,
        overflow: 'hidden',
        transition: 'height 220ms ease',
        background: 'var(--surface)',
        width: '100%'
      }}
    >
      <div
        ref={innerRef}
        style={{ opacity: visible ? 1 : 0, transition: 'opacity 180ms ease' }}
      >
        {children}
      </div>
    </div>
  )
}

export function BaseKampView(): React.JSX.Element {
  const moduleOrder = useStore((s) => s.moduleOrder)
  const moduleDisplayStyles = useStore((s) => s.moduleDisplayStyles)
  const editMode = useStore((s) => s.baseKampEditMode)
  const toggleEditMode = useStore((s) => s.toggleBaseKampEditMode)

  const modules = moduleOrder
    .map((id) => MODULE_REGISTRY.find((m) => m.id === id))
    .filter((m): m is ModuleRegistration => m !== undefined)

  if (modules.length === 0) {
    return (
      <div className="base-kamp-empty">No modules configured. Add some in Preferences → Home.</div>
    )
  }

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
        <section key={mod.id} className="base-kamp-module">
          <div className="base-kamp-module-label">{mod.title}</div>
          <AnimatedConfigRow visible={editMode}>
            {mod.configComponent && <mod.configComponent />}
          </AnimatedConfigRow>
          <div className="base-kamp-module-body">
            <mod.component displayStyle={moduleDisplayStyles[mod.id] ?? 'shelf'} />
          </div>
        </section>
      ))}
    </div>
  )
}
