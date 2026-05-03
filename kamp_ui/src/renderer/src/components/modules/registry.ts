import type React from 'react'
import { NewArrivalsModule } from './NewArrivalsModule'
import { LastPlayedModule } from './LastPlayedModule'

export interface ModuleProps {}

export interface ModuleRegistration {
  id: string
  title: string
  component: React.ComponentType<ModuleProps>
}

export const MODULE_REGISTRY: ModuleRegistration[] = [
  { id: 'kamp.new-arrivals', title: 'New Arrivals', component: NewArrivalsModule },
  { id: 'kamp.last-played', title: 'Last Played', component: LastPlayedModule }
]
