import React, { useState } from 'react'
import { useStore } from '../store'
import { PlaylistCard } from './PlaylistCard'
import { SortControl } from './SortControl'
import { SparkleIcon } from './TransportIcons'
import type { Playlist } from '../api/client'

const PLAYLIST_SORT_OPTIONS = [
  { key: 'title', label: 'Name' },
  { key: 'track_count', label: 'Track Count' },
  { key: 'updated_at', label: 'Last Updated' },
  { key: 'last_played_at', label: 'Last Played' }
]

type PlaylistSortOrder = 'title' | 'track_count' | 'updated_at' | 'last_played_at'
type TypeFilter = 'all' | 'magic' | 'simple'

const STORAGE_KEY = 'kamp:playlists-sort'
const TYPE_FILTER_KEY = 'kamp:playlists-type-filter'

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

function loadStoredTypeFilter(): TypeFilter {
  try {
    const raw = localStorage.getItem(TYPE_FILTER_KEY)
    if (raw === 'magic' || raw === 'simple') return raw
  } catch {
    // ignore malformed storage
  }
  return 'all'
}

function sortPlaylists(
  playlists: Playlist[],
  order: PlaylistSortOrder,
  dir: 'asc' | 'desc'
): Playlist[] {
  // last_played_at: nulls always last regardless of direction
  if (order === 'last_played_at') {
    const withVal = playlists.filter((p) => p.last_played_at !== null)
    const nulls = playlists.filter((p) => p.last_played_at === null)
    withVal.sort((a, b) => a.last_played_at! - b.last_played_at!)
    if (dir === 'desc') withVal.reverse()
    return [...withVal, ...nulls]
  }
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
  const createPlaylist = useStore((s) => s.createPlaylist)
  const stored = loadStoredSort()
  const [sortOrder, setSortOrderLocal] = useState<PlaylistSortOrder>(stored.order)
  const [sortDir, setSortDirLocal] = useState<'asc' | 'desc'>(stored.dir)
  const [typeFilter, setTypeFilterLocal] = useState<TypeFilter>(loadStoredTypeFilter)

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

  const handleTypeFilter = (f: TypeFilter): void => {
    setTypeFilterLocal(f)
    try {
      localStorage.setItem(TYPE_FILTER_KEY, f)
    } catch {
      // ignore storage errors
    }
  }

  const handleNewPlaylist = async (): Promise<void> => {
    const title = window.prompt('Playlist name:')
    if (!title?.trim()) return
    await createPlaylist(title.trim())
  }

  const handleNewMagicPlaylist = (): void => {
    // KAMP-464 opens the criteria builder modal here
  }

  const typeFiltered =
    typeFilter === 'magic'
      ? playlists.filter((p) => p.criteria !== null)
      : typeFilter === 'simple'
        ? playlists.filter((p) => p.criteria === null)
        : playlists

  const visible = sortPlaylists(typeFiltered, sortOrder, sortDir)

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
        <div className="playlist-type-filter">
          <button
            className={`playlist-type-btn${typeFilter === 'all' ? ' active' : ''}`}
            onClick={() => handleTypeFilter('all')}
          >
            All
          </button>
          <button
            className={`playlist-type-btn${typeFilter === 'magic' ? ' active' : ''}`}
            onClick={() => handleTypeFilter('magic')}
          >
            Magic
          </button>
          <button
            className={`playlist-type-btn${typeFilter === 'simple' ? ' active' : ''}`}
            onClick={() => handleTypeFilter('simple')}
          >
            Simple
          </button>
        </div>
        <SortControl
          value={sortOrder}
          options={PLAYLIST_SORT_OPTIONS}
          dir={sortDir}
          onChange={handleOrderChange}
          onDirChange={handleDirChange}
        />
        <div className="playlist-cta-group">
          <button className="playlist-cta-btn" onClick={() => void handleNewPlaylist()}>
            New Playlist
          </button>
          <button className="playlist-cta-btn playlist-cta-btn--magic" onClick={handleNewMagicPlaylist}>
            <SparkleIcon size={12} />
            New Magic Playlist
          </button>
        </div>
      </div>
      {visible.length === 0 && typeFilter === 'magic' ? (
        <div className="album-grid-empty">
          No magic playlists yet. Set some rules and Kamp builds the playlist for you &mdash; automatically.
        </div>
      ) : (
        <div className="album-grid">
          {visible.map((pl) => (
            <PlaylistCard key={pl.id} playlist={pl} />
          ))}
        </div>
      )}
    </div>
  )
}
