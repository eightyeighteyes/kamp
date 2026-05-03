import React, { useEffect, useState } from 'react'
import { getAlbums } from '../../api/client'
import type { Album } from '../../api/client'
import { useStore } from '../../store'
import { ShelfView } from './ShelfView'
import { GridView } from './GridView'
import type { ModuleProps } from './registry'

export function LastPlayedModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.lastPlayedCount)
  // Re-fetch whenever the current track changes — the server updates last_played
  // at EOF before broadcasting track.changed, so the list is already stale by
  // the time this selector fires.
  const currentFilePath = useStore((s) => s.player?.current_track?.file_path ?? null)
  const serverStatus = useStore((s) => s.serverStatus)
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Skip until the server is reachable; this effect re-fires when serverStatus
    // transitions to 'connected', so modules populate without a manual reload.
    if (serverStatus !== 'connected') return
    getAlbums('last_played')
      .then((all) => {
        const played = all.filter((a) => a.last_played_at !== null).slice(0, count)
        setAlbums(played)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [count, currentFilePath, serverStatus])

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

  return displayStyle === 'grid' ? <GridView albums={albums} /> : <ShelfView albums={albums} />
}
