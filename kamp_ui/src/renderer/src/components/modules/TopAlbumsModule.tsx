import React, { useEffect, useState } from 'react'
import { getTopAlbums } from '../../api/client'
import type { Album } from '../../api/client'
import { useStore } from '../../store'
import { ShelfView } from './ShelfView'
import { GridView } from './GridView'
import { ListView } from './ListView'
import type { ModuleProps, DisplayStyle } from './registry'

export function TopAlbumsConfig(): React.JSX.Element {
  const storeCount = useStore((s) => s.topAlbumsCount)
  const displayStyle = useStore((s) => s.moduleDisplayStyles['kamp.top-albums'] ?? 'shelf')
  const setCount = useStore((s) => s.setTopAlbumsCount)
  const setDisplayStyle = useStore((s) => s.setModuleDisplayStyle)

  const [localCount, setLocalCount] = useState(storeCount)

  // Debounce: write to store 400ms after the user stops typing
  useEffect(() => {
    const id = setTimeout(() => setCount(localCount), 400)
    return () => clearTimeout(id)
  }, [localCount, setCount])

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
        <span>Style</span>
        <select
          value={displayStyle}
          onChange={(e) => setDisplayStyle('kamp.top-albums', e.target.value as DisplayStyle)}
        >
          <option value="shelf">Shelf</option>
          <option value="grid">Grid</option>
          <option value="list">List</option>
        </select>
      </label>
    </div>
  )
}

export function TopAlbumsModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.topAlbumsCount)
  // Re-fetch when the current track changes — play_count updates at EOF.
  const currentTrackId = useStore((s) => s.player?.current_track?.id ?? null)
  const serverStatus = useStore((s) => s.serverStatus)
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (serverStatus !== 'connected') return
    // KAMP-615: the server ranks + limits (only top-N albums enriched), instead
    // of us fetching the whole library and slicing here on every track change.
    getTopAlbums('most_played', count > 0 ? count : 0)
      .then(setAlbums)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [count, currentTrackId, serverStatus])

  if (loading) {
    return (
      <div className="module-skeleton-row">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="module-skeleton-card" />
        ))}
      </div>
    )
  }

  if (albums.length === 0) {
    return <div className="module-empty">No albums played yet.</div>
  }

  if (displayStyle === 'list') return <ListView albums={albums} showPlayCount />
  return displayStyle === 'grid' ? (
    <GridView albums={albums} showPlayCount />
  ) : (
    <ShelfView albums={albums} showPlayCount />
  )
}
