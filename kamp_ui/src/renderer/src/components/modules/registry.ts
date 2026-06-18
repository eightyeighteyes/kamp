import type React from 'react'
import { NewArrivalsModule, NewArrivalsConfig } from './NewArrivalsModule'
import { LastPlayedModule, LastPlayedConfig } from './LastPlayedModule'
import { TopAlbumsModule, TopAlbumsConfig } from './TopAlbumsModule'
import { TopTracksModule, TopTracksConfig } from './TopTracksModule'
import { TopArtistsModule, TopArtistsConfig } from './TopArtistsModule'
import { StereoRackModule, StereoRackConfig } from './StereoRackModule'
import { MagicPlaylistModule, MagicPlaylistConfig, MagicPlaylistTitle } from './MagicPlaylistModule'

export type DisplayStyle = 'shelf' | 'grid' | 'list'

export interface ModuleProps {
  displayStyle: DisplayStyle
  moduleId?: string
}

export interface ModuleRegistration {
  id: string
  title: string
  component: React.ComponentType<ModuleProps>
  configComponent?: React.ComponentType<{ moduleId?: string }>
  titleComponent?: React.ComponentType<{ moduleId: string }>
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
  },
  {
    id: 'kamp.top-albums',
    title: 'Top Albums',
    component: TopAlbumsModule,
    configComponent: TopAlbumsConfig
  },
  {
    id: 'kamp.top-tracks',
    title: 'Top Tracks',
    component: TopTracksModule,
    configComponent: TopTracksConfig
  },
  {
    id: 'kamp.top-artists',
    title: 'Top Artists',
    component: TopArtistsModule,
    configComponent: TopArtistsConfig
  },
  {
    // defaultVisible: false — not in the default moduleOrder, so it appears in
    // the "add module" list rather than the active Home view on first launch.
    id: 'kamp.stereo-rack',
    title: 'Stereo Rack',
    component: StereoRackModule,
    configComponent: StereoRackConfig
  },
  {
    id: 'kamp.magic-playlist-1',
    title: 'Magic Playlist',
    component: MagicPlaylistModule,
    configComponent: MagicPlaylistConfig,
    titleComponent: MagicPlaylistTitle
  },
  {
    id: 'kamp.magic-playlist-2',
    title: 'Magic Playlist',
    component: MagicPlaylistModule,
    configComponent: MagicPlaylistConfig,
    titleComponent: MagicPlaylistTitle
  },
  {
    id: 'kamp.magic-playlist-3',
    title: 'Magic Playlist',
    component: MagicPlaylistModule,
    configComponent: MagicPlaylistConfig,
    titleComponent: MagicPlaylistTitle
  },
  {
    id: 'kamp.magic-playlist-4',
    title: 'Magic Playlist',
    component: MagicPlaylistModule,
    configComponent: MagicPlaylistConfig,
    titleComponent: MagicPlaylistTitle
  },
  {
    id: 'kamp.magic-playlist-5',
    title: 'Magic Playlist',
    component: MagicPlaylistModule,
    configComponent: MagicPlaylistConfig,
    titleComponent: MagicPlaylistTitle
  }
]
