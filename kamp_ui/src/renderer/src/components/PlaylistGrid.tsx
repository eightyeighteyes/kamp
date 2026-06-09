import React, { useState } from 'react'
import { useStore } from '../store'
import { PlaylistCard } from './PlaylistCard'
import { SortControl } from './SortControl'
import type { Playlist } from '../api/client'

const PLAYLIST_SORT_OPTIONS = [
  { key: 'title', label: 'Name' },
  { key: 'track_count', label: 'Track Count' },
  { key: 'updated_at', label: 'Last Updated' },
]

type PlaylistSortOrder = 'title' | 'track_count' | 'updated_at'

const STORAGE_KEY = 'kamp:playlists-sort'

function loadStoredSort(): { order: PlaylistSortOrder; dir: 'asc' | 'desc' } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as { order: PlaylistSortOrder; dir: 'asc' | 'desc' }
      if (parsed.order && parsed.dir) return parsed
    }
  } catch {
    // ignore malformed storage
  }
  return { order: 'title', dir: 'asc' }
}

function sortPlaylists(
  playlists: Playlist[],
  order: PlaylistSortOrder,
  dir: 'asc' | 'desc'
): Playlist[] {
  const sorted = [...playlists].sort((a, b) => {
    switch (order) {
      case 'track_count':
        return a.track_count - b.track_count
      case 'updated_at':
        return a.updated_at - b.updated_at
      default:
        return a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })
    }
  })
  return dir === 'desc' ? sorted.reverse() : sorted
}

export function PlaylistGrid(): React.JSX.Element {
  const playlists = useStore((s) => s.library.playlists)
  const stored = loadStoredSort()
  const [sortOrder, setSortOrderLocal] = useState<PlaylistSortOrder>(stored.order)
  const [sortDir, setSortDirLocal] = useState<'asc' | 'desc'>(stored.dir)

  const persist = (order: PlaylistSortOrder, dir: 'asc' | 'desc'): void => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ order, dir }))
    } catch {
      // ignore storage errors
    }
  }

  const handleOrderChange = (key: string): void => {
    const order = key as PlaylistSortOrder
    setSortOrderLocal(order)
    persist(order, sortDir)
  }

  const handleDirChange = (dir: 'asc' | 'desc'): void => {
    setSortDirLocal(dir)
    persist(sortOrder, dir)
  }

  const visible = sortPlaylists(playlists, sortOrder, sortDir)

  if (playlists.length === 0) {
    return (
      <div className="album-grid-container">
        <div className="album-grid-empty">
          No playlists yet. Right-click any track or album and choose Add to Playlist &rsaquo; New
          Playlist.
        </div>
      </div>
    )
  }

  return (
    <div className="album-grid-container">
      <div className="album-grid-toolbar">
        <SortControl
          value={sortOrder}
          options={PLAYLIST_SORT_OPTIONS}
          dir={sortDir}
          onChange={handleOrderChange}
          onDirChange={handleDirChange}
        />
      </div>
      <div className="album-grid">
        {visible.map((pl) => (
          <PlaylistCard key={pl.id} playlist={pl} />
        ))}
      </div>
    </div>
  )
}
