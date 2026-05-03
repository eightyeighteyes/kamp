import React from 'react'
import { useStore } from '../store'
import { MODULE_REGISTRY } from './modules/registry'
import type { ModuleRegistration } from './modules/registry'

export function BaseKampView(): React.JSX.Element {
  const moduleOrder = useStore((s) => s.moduleOrder)
  const moduleDisplayStyles = useStore((s) => s.moduleDisplayStyles)

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
      {modules.map((mod) => (
        <section key={mod.id} className="base-kamp-module">
          <div className="base-kamp-module-label">{mod.title}</div>
          <div className="base-kamp-module-body">
            <mod.component displayStyle={moduleDisplayStyles[mod.id] ?? 'shelf'} />
          </div>
        </section>
      ))}
    </div>
  )
}
