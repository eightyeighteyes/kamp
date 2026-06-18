import React, { useEffect, useState } from 'react'
import { getStats } from '../../api/client'
import type { Stats } from '../../api/client'
import { useStore } from '../../store'
import type { ModuleProps } from './registry'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatSeconds(n: number): string {
  if (n <= 0) return '—'
  const days = Math.floor(n / 86400)
  const hours = Math.floor((n % 86400) / 3600)
  const minutes = Math.floor((n % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes} min`
  return '< 1 min'
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export function StatsConfig(): React.JSX.Element {
  const count = useStore((s) => s.statsTopTracksCount)
  const setCount = useStore((s) => s.setStatsTopTracksCount)
  return (
    <div className="module-config-row">
      <label className="module-config-field">
        <span>Top Tracks</span>
        <input
          type="number"
          min={1}
          max={10}
          value={count}
          onChange={(e) => setCount(parseInt(e.target.value) || 3)}
        />
      </label>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Module
// ---------------------------------------------------------------------------

// displayStyle is required by ModuleProps but unused — Stats has a fixed layout.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function StatsModule({ displayStyle: _ds }: ModuleProps): React.JSX.Element {
  const count = useStore((s) => s.statsTopTracksCount)
  const lastPlayedVersion = useStore((s) => s.lastPlayedVersion)
  const serverStatus = useStore((s) => s.serverStatus)

  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (serverStatus !== 'connected') return
    getStats(count)
      .then((s) => setStats(s))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [count, lastPlayedVersion, serverStatus])

  if (loading) {
    return (
      <div className="module-skeleton-row">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="module-skeleton-card" />
        ))}
      </div>
    )
  }

  if (!stats) return <div className="module-empty">Could not load stats.</div>

  const hasListeningData =
    stats.total_play_seconds > 0 || stats.total_track_plays > 0 || stats.top_artist_name !== null

  return (
    <div className="stats-sections">
      <div className="stats-section">
        <div className="stats-section-label">Your Library</div>
        <div className="stats-row">
          <div className="stats-stat">
            <div className="stats-number">{stats.track_count.toLocaleString()}</div>
            <div className="stats-label">Songs</div>
          </div>
          <div className="stats-stat">
            <div className="stats-number">{stats.album_count.toLocaleString()}</div>
            <div className="stats-label">Albums</div>
          </div>
          <div className="stats-stat">
            <div className="stats-number">{stats.artist_count.toLocaleString()}</div>
            <div className="stats-label">Artists</div>
          </div>
        </div>
      </div>

      <div className="stats-section">
        <div className="stats-section-label">Your Listening</div>
        {!hasListeningData ? (
          <div className="module-empty">Start listening to build your stats.</div>
        ) : (
          <>
            <div className="stats-row">
              <div className="stats-stat">
                <div className="stats-number">{formatSeconds(stats.total_play_seconds)}</div>
                <div className="stats-label">Time Listened</div>
              </div>
              <div className="stats-stat">
                <div className="stats-number">{stats.total_track_plays.toLocaleString()}</div>
                <div className="stats-label">Track Plays</div>
              </div>
              <div className="stats-stat">
                <div className="stats-number">{stats.albums_played.toLocaleString()}</div>
                <div className="stats-label">Albums Played</div>
              </div>
            </div>

            {stats.top_artist_name !== null && (
              <div className="stats-top-artist">
                <span className="stats-top-artist-label">Top Artist</span>
                <span className="stats-top-artist-name">{stats.top_artist_name}</span>
                {stats.top_artist_seconds !== null && (
                  <span className="stats-top-artist-time">
                    {formatSeconds(stats.top_artist_seconds)}
                  </span>
                )}
              </div>
            )}

            {stats.top_tracks.length > 0 && (
              <div className="stats-top-tracks">
                <div className="stats-section-label">Top Tracks</div>
                {stats.top_tracks.map((track, i) => (
                  <div key={track.id} className="stats-track-row">
                    <span className="stats-track-rank">{i + 1}</span>
                    <span className="stats-track-info">
                      <span className="stats-track-title">{track.title}</span>
                      <span className="stats-track-artist">{track.artist}</span>
                    </span>
                    <span className="stats-track-plays">
                      {track.play_count === 1 ? '1 play' : `${track.play_count} plays`}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
