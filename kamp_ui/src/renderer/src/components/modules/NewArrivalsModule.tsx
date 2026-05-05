import React, { useEffect, useState } from 'react'
import type { Album } from '../../api/client'
import { useStore } from '../../store'
import { ShelfView } from './ShelfView'
import { GridView } from './GridView'
import { ListView } from './ListView'
import type { ModuleProps, DisplayStyle } from './registry'

export function NewArrivalsConfig(): React.JSX.Element {
  const storeCount = useStore((s) => s.recentlyAddedCount)
  const storeDays = useStore((s) => s.recentlyAddedDays)
  const displayStyle = useStore((s) => s.moduleDisplayStyles['kamp.new-arrivals'] ?? 'shelf')
  const setCount = useStore((s) => s.setRecentlyAddedCount)
  const setDays = useStore((s) => s.setRecentlyAddedDays)
  const setDisplayStyle = useStore((s) => s.setModuleDisplayStyle)

  const [localCount, setLocalCount] = useState(storeCount)
  const [localDays, setLocalDays] = useState(storeDays)

  // Debounce: write to store 400ms after the user stops typing
  useEffect(() => {
    const id = setTimeout(() => setCount(localCount), 400)
    return () => clearTimeout(id)
  }, [localCount, setCount])

  useEffect(() => {
    const id = setTimeout(() => setDays(localDays), 400)
    return () => clearTimeout(id)
  }, [localDays, setDays])

  return (
    <div className="module-config-row">
      <label className="module-config-field">
        <span>Albums</span>
        <input
          type="number"
          min={0}
          max={50}
          value={localCount}
          onChange={(e) => setLocalCount(parseInt(e.target.value) || 0)}
        />
      </label>
      <label className="module-config-field">
        <span>Days</span>
        <input
          type="number"
          min={0}
          max={3650}
          value={localDays}
          onChange={(e) => setLocalDays(parseInt(e.target.value) || 0)}
        />
      </label>
      <label className="module-config-field">
        <span>Style</span>
        <select
          value={displayStyle}
          onChange={(e) => setDisplayStyle('kamp.new-arrivals', e.target.value as DisplayStyle)}
        >
          <option value="shelf">Shelf</option>
          <option value="grid">Grid</option>
          <option value="list">List</option>
        </select>
      </label>
    </div>
  )
}

export function NewArrivalsModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.recentlyAddedCount)
  const days = useStore((s) => s.recentlyAddedDays)
  const allAlbums = useStore((s) => s.library.albums)
  const serverStatus = useStore((s) => s.serverStatus)
  const [albums, setAlbums] = useState<Album[]>([])

  useEffect(() => {
    void Promise.resolve(allAlbums).then((all) => {
      const withDate = [...all]
        .filter((a) => a.added_at !== null)
        .sort((a, b) => (b.added_at ?? 0) - (a.added_at ?? 0))
      const cutoff = days > 0 ? Date.now() / 1000 - days * 86400 : null
      const inWindow =
        cutoff !== null ? withDate.filter((a) => (a.added_at ?? 0) >= cutoff) : withDate
      // Fall back to most-recently-added if the date window returns nothing (e.g. older libraries)
      setAlbums((inWindow.length > 0 ? inWindow : withDate).slice(0, count > 0 ? count : undefined))
    })
  }, [allAlbums, count, days])

  if (serverStatus !== 'connected') {
    return (
      <div className="module-skeleton-row">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="module-skeleton-card" />
        ))}
      </div>
    )
  }

  if (albums.length === 0) {
    return <div className="module-empty">No albums in your library.</div>
  }

  if (displayStyle === 'list') return <ListView albums={albums} />
  return displayStyle === 'grid' ? <GridView albums={albums} /> : <ShelfView albums={albums} />
}
