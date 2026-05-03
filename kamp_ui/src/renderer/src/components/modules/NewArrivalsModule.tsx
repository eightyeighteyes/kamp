import React, { useEffect, useState } from 'react'
import { getAlbums } from '../../api/client'
import type { Album } from '../../api/client'
import { useStore } from '../../store'
import { ShelfView } from './ShelfView'
import { GridView } from './GridView'
import type { ModuleProps } from './registry'

const DAYS = 30
const CUTOFF_SECONDS = DAYS * 86400

export function NewArrivalsModule({ displayStyle }: ModuleProps): React.JSX.Element {
  const serverStatus = useStore((s) => s.serverStatus)
  const [albums, setAlbums] = useState<Album[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Skip until the server is reachable; this effect re-fires when serverStatus
    // transitions to 'connected', so modules populate without a manual reload.
    if (serverStatus !== 'connected') return
    getAlbums('date_added')
      .then((all) => {
        const cutoff = Date.now() / 1000 - CUTOFF_SECONDS
        const recent = all.filter((a) => a.added_at !== null && a.added_at >= cutoff)
        setAlbums(recent)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [serverStatus])

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
    return <div className="module-empty">No albums added in the last {DAYS} days.</div>
  }

  return displayStyle === 'grid' ? <GridView albums={albums} /> : <ShelfView albums={albums} />
}
