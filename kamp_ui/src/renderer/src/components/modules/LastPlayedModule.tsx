import React, { useEffect, useState } from 'react'
import { getAlbums } from '../../api/client'
import type { Album } from '../../api/client'
import { useStore } from '../../store'
import { ShelfView } from './ShelfView'
import { GridView } from './GridView'
import { ListView } from './ListView'
import type { ModuleProps, DisplayStyle } from './registry'

export function LastPlayedConfig(): React.JSX.Element {
  const storeCount = useStore((s) => s.lastPlayedCount)
  const storeDays = useStore((s) => s.lastPlayedDays)
  const displayStyle = useStore((s) => s.moduleDisplayStyles['kamp.last-played'] ?? 'shelf')
  const setCount = useStore((s) => s.setLastPlayedCount)
  const setDays = useStore((s) => s.setLastPlayedDays)
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
          onChange={(e) => setDisplayStyle('kamp.last-played', e.target.value as DisplayStyle)}
        >
          <option value="shelf">Shelf</option>
          <option value="grid">Grid</option>
          <option value="list">List</option>
        </select>
      </label>
    </div>
  )
}

export function LastPlayedModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.lastPlayedCount)
  const days = useStore((s) => s.lastPlayedDays)
  // Re-fetch on track.changed events (covers both normal track transitions and
  // the 5-second debuff timer firing after a skip). lastPlayedVersion increments
  // on every track.changed push so this effect re-runs even when currentFilePath
  // stays the same (e.g. debuff timer fires while the same track is playing).
  const lastPlayedVersion = useStore((s) => s.lastPlayedVersion)
  const serverStatus = useStore((s) => s.serverStatus)
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Skip until the server is reachable; this effect re-fires when serverStatus
    // transitions to 'connected', so modules populate without a manual reload.
    if (serverStatus !== 'connected') return
    getAlbums('last_played')
      .then((all) => {
        const cutoff = days > 0 ? Date.now() / 1000 - days * 86400 : null
        const played = all
          .filter(
            (a) => a.last_played_at !== null && (cutoff === null || a.last_played_at >= cutoff)
          )
          .slice(0, count > 0 ? count : undefined)
        setAlbums(played)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [count, days, lastPlayedVersion, serverStatus])

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

  if (displayStyle === 'list') return <ListView albums={albums} />
  return displayStyle === 'grid' ? (
    <GridView albums={albums} />
  ) : (
    <ShelfView albums={albums} scrollToPlaying />
  )
}
