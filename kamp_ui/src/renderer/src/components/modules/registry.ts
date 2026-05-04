import type React from 'react'
import { NewArrivalsModule, NewArrivalsConfig } from './NewArrivalsModule'
import { LastPlayedModule, LastPlayedConfig } from './LastPlayedModule'

export type DisplayStyle = 'shelf' | 'grid' | 'list'

export interface ModuleProps {
  displayStyle: DisplayStyle
}

export interface ModuleRegistration {
  id: string
  title: string
  component: React.ComponentType<ModuleProps>
  configComponent?: React.ComponentType
}

export const MODULE_REGISTRY: ModuleRegistration[] = [
  {
    id: 'kamp.new-arrivals',
    title: 'New Arrivals',
    component: NewArrivalsModule,
    configComponent: NewArrivalsConfig
  },
  {
    id: 'kamp.last-played',
    title: 'Last Played',
    component: LastPlayedModule,
    configComponent: LastPlayedConfig
  }
]
