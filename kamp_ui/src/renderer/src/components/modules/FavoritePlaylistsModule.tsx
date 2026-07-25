import React from 'react'
import type { Playlist } from '../../api/client'
import { useStore } from '../../store'
import { PlaylistShelfView, PlaylistGridView, PlaylistListView } from './PlaylistViews'
import type { ModuleProps, DisplayStyle } from './registry'

function sortFavPlaylists(playlists: Playlist[], sort: 'last_played_at' | 'title'): Playlist[] {
  if (sort === 'last_played_at') {
    const withVal = playlists.filter((p) => p.last_played_at !== null)
    const nulls = playlists.filter((p) => p.last_played_at === null)
    withVal.sort((a, b) => b.last_played_at! - a.last_played_at!)
    return [...withVal, ...nulls]
  }
  return [...playlists].sort((a, b) =>
    a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })
  )
}

export function FavoritePlaylistsConfig({ moduleId }: { moduleId?: string }): React.JSX.Element {
  const id = moduleId ?? 'kamp.favorite-playlists'
  const count = useStore((s) => s.favoritePlaylistsCount)
  const sortOrder = useStore((s) => s.favoritePlaylistsSortOrder)
  const displayStyle = useStore((s) => s.moduleDisplayStyles[id] ?? 'shelf')
  const setCount = useStore((s) => s.setFavoritePlaylistsCount)
  const setSortOrder = useStore((s) => s.setFavoritePlaylistsSortOrder)
  const setDisplayStyle = useStore((s) => s.setModuleDisplayStyle)

  return (
    <div className="module-config-row">
      <label className="module-config-field">
        <span>Playlists</span>
        <input
          type="number"
          min={1}
          max={50}
          value={count}
          onChange={(e) => setCount(parseInt(e.target.value) || 10)}
        />
      </label>
      <label className="module-config-field">
        <span>Sort</span>
        <select
          value={sortOrder}
          onChange={(e) => setSortOrder(e.target.value as 'last_played_at' | 'title')}
        >
          <option value="last_played_at">Last Played</option>
          <option value="title">A→Z</option>
        </select>
      </label>
      <label className="module-config-field">
        <span>Style</span>
        <select
          value={displayStyle}
          onChange={(e) => setDisplayStyle(id, e.target.value as DisplayStyle)}
        >
          <option value="shelf">Shelf</option>
          <option value="grid">Grid</option>
          <option value="list">List</option>
        </select>
      </label>
    </div>
  )
}

export function FavoritePlaylistsModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const allPlaylists = useStore((s) => s.library.playlists)
  const count = useStore((s) => s.favoritePlaylistsCount)
  const sortOrder = useStore((s) => s.favoritePlaylistsSortOrder)

  const favorites = sortFavPlaylists(
    allPlaylists.filter((p) => p.favorite),
    sortOrder
  ).slice(0, count)

  if (favorites.length === 0) {
    return <div className="module-empty">No favorite playlists yet.</div>
  }

  if (displayStyle === 'list') return <PlaylistListView playlists={favorites} />
  if (displayStyle === 'grid') return <PlaylistGridView playlists={favorites} />
  return <PlaylistShelfView playlists={favorites} />
}
