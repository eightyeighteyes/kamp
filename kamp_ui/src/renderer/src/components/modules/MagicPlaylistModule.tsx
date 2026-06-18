import React, { useEffect, useState } from 'react'
import { getMagicPlaylistModuleContent, artUrl } from '../../api/client'
import type { Album, Artist, PlaylistTrack } from '../../api/client'
import { useStore } from '../../store'
import type { MagicPlaylistModuleConfig } from '../../store'
import { ArtistContextMenu } from '../ArtistContextMenu'
import { TrackContextMenu } from '../TrackContextMenu'
import { PlayIcon } from '../TransportIcons'
import { ShelfView } from './ShelfView'
import { GridView } from './GridView'
import { ListView } from './ListView'
import type { ModuleProps, DisplayStyle } from './registry'

const DEFAULT_CONFIG: MagicPlaylistModuleConfig = {
  playlistId: null,
  sort: 'random',
  contents: 'albums',
  items: 10
}

// ---------------------------------------------------------------------------
// MagicPlaylistTitle — renders the playlist title or "Magic Playlist" fallback
// ---------------------------------------------------------------------------

export function MagicPlaylistTitle({ moduleId }: { moduleId: string }): React.JSX.Element {
  const playlists = useStore((s) => s.library.playlists)
  const configs = useStore((s) => s.magicPlaylistConfigs)
  const config = configs[moduleId] ?? DEFAULT_CONFIG
  const playlist =
    config.playlistId != null ? playlists.find((p) => p.id === config.playlistId) : null
  return <>{playlist ? playlist.title : 'Magic Playlist'}</>
}

// ---------------------------------------------------------------------------
// MagicPlaylistConfig — per-instance configuration controls
// ---------------------------------------------------------------------------

export function MagicPlaylistConfig({ moduleId }: { moduleId?: string }): React.JSX.Element {
  const id = moduleId ?? ''
  const allPlaylists = useStore((s) => s.library.playlists)
  const playlists = allPlaylists.filter((p) => p.criteria != null)
  const configs = useStore((s) => s.magicPlaylistConfigs)
  const setConfig = useStore((s) => s.setMagicPlaylistConfig)
  const displayStyle = useStore((s) => s.moduleDisplayStyles[id] ?? 'shelf')
  const setDisplayStyle = useStore((s) => s.setModuleDisplayStyle)

  const config = configs[id] ?? DEFAULT_CONFIG

  const update = (patch: Partial<MagicPlaylistModuleConfig>): void => {
    setConfig(id, { ...config, ...patch })
  }

  return (
    <div className="module-config-row">
      <label className="module-config-field">
        <span>Playlist</span>
        <select
          value={config.playlistId ?? ''}
          onChange={(e) => {
            const val = e.target.value
            update({ playlistId: val === '' ? null : parseInt(val) })
          }}
        >
          <option value="">Choose a playlist…</option>
          {playlists.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </select>
      </label>
      <label className="module-config-field">
        <span>Contents</span>
        <select
          value={config.contents}
          onChange={(e) =>
            update({ contents: e.target.value as MagicPlaylistModuleConfig['contents'] })
          }
        >
          <option value="albums">Albums</option>
          <option value="artists">Artists</option>
          <option value="tracks">Tracks</option>
        </select>
      </label>
      <label className="module-config-field">
        <span>Sort</span>
        <select
          value={config.sort}
          onChange={(e) => update({ sort: e.target.value as MagicPlaylistModuleConfig['sort'] })}
        >
          <option value="random">Random</option>
          <option value="most_played">Most Played</option>
          <option value="last_played">Last Played</option>
          <option value="recently_added">Recently Added</option>
        </select>
      </label>
      <label className="module-config-field">
        <span>Items</span>
        <input
          type="number"
          min={1}
          max={50}
          value={config.items}
          onChange={(e) => update({ items: parseInt(e.target.value) || 10 })}
        />
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

// ---------------------------------------------------------------------------
// Artist sub-components (reuse art-card pattern from TopArtistsModule)
// ---------------------------------------------------------------------------

type ArtistMenuPos = { x: number; y: number; artist: Artist }

function MagicArtistCard({ artist }: { artist: Artist }): React.JSX.Element {
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artLoaded, setArtLoaded] = useState(false)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<ArtistMenuPos | null>(null)

  const navigate = (): void => {
    selectArtist(artist.name)
    void setActiveView('library')
  }

  return (
    <div
      className="track-card"
      tabIndex={0}
      draggable
      onClick={navigate}
      onKeyDown={(e) => e.key === 'Enter' && navigate()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, artist })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-artist', JSON.stringify({ name: artist.name }))
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`track-card-art${artLoaded ? ' has-art' : ''}`}>
        {!artError && artist.top_album && (
          <img
            className="track-card-art-img"
            src={artUrl(artist.name, artist.top_album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => {
              setArtLoaded(false)
              setArtError(true)
            }}
          />
        )}
      </div>
      <div className="track-card-info">
        <div className="track-card-title">{artist.name}</div>
      </div>
      {menu && (
        <ArtistContextMenu
          x={menu.x}
          y={menu.y}
          artist={menu.artist}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

function MagicArtistListRow({ artist }: { artist: Artist }): React.JSX.Element {
  const selectArtist = useStore((s) => s.selectArtist)
  const setActiveView = useStore((s) => s.setActiveView)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<ArtistMenuPos | null>(null)

  const navigate = (): void => {
    selectArtist(artist.name)
    void setActiveView('library')
  }

  return (
    <div
      className="module-list-row"
      tabIndex={0}
      draggable
      onClick={navigate}
      onKeyDown={(e) => e.key === 'Enter' && navigate()}
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, artist })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-artist', JSON.stringify({ name: artist.name }))
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className="module-list-thumb">
        {!artError && artist.top_album && (
          <img
            src={artUrl(artist.name, artist.top_album)}
            alt=""
            onError={() => setArtError(true)}
          />
        )}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">{artist.name}</div>
      </div>
      {menu && (
        <ArtistContextMenu
          x={menu.x}
          y={menu.y}
          artist={menu.artist}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

function MagicArtistShelf({ artists }: { artists: Artist[] }): React.JSX.Element {
  return (
    <div className="module-shelf-wrapper">
      <div className="module-shelf" role="region" aria-label="Playlist artists shelf">
        {artists.map((artist) => (
          <MagicArtistCard key={artist.name} artist={artist} />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Track sub-components (playlist tracks share the same Track shape)
// ---------------------------------------------------------------------------

type TrackMenuPos = { x: number; y: number; track: PlaylistTrack }

function MagicTrackCard({ track }: { track: PlaylistTrack }): React.JSX.Element {
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const [artLoaded, setArtLoaded] = useState(false)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<TrackMenuPos | null>(null)
  const isCurrent = currentTrack?.id === track.id

  return (
    <div
      className={`track-card${isCurrent ? ' playing' : ''}`}
      tabIndex={0}
      draggable
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, track })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-track-path', track.file_path)
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className={`track-card-art${artLoaded ? ' has-art' : ''}`}>
        {!artError && (
          <img
            className="track-card-art-img"
            src={artUrl(track.album_artist, track.album)}
            alt=""
            onLoad={() => setArtLoaded(true)}
            onError={() => {
              setArtLoaded(false)
              setArtError(true)
            }}
          />
        )}
        {playing && isCurrent && (
          <div className="now-playing-badge">
            <PlayIcon size={10} />
          </div>
        )}
      </div>
      <div className="track-card-info">
        <div className="track-card-title">{track.title}</div>
        <div className="track-card-artist">{track.artist}</div>
      </div>
      {menu && (
        <TrackContextMenu x={menu.x} y={menu.y} track={menu.track} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

function MagicTrackListRow({ track }: { track: PlaylistTrack }): React.JSX.Element {
  const currentTrack = useStore((s) => s.player.current_track)
  const playing = useStore((s) => s.player.playing)
  const [artError, setArtError] = useState(false)
  const [menu, setMenu] = useState<TrackMenuPos | null>(null)
  const isCurrent = currentTrack?.id === track.id

  return (
    <div
      className={`module-list-row${isCurrent ? ' playing' : ''}`}
      tabIndex={0}
      draggable
      onContextMenu={(e) => {
        e.preventDefault()
        setMenu({ x: e.clientX, y: e.clientY, track })
      }}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/kamp-track-path', track.file_path)
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className="module-list-thumb">
        {!artError && (
          <img
            src={artUrl(track.album_artist, track.album)}
            alt=""
            onError={() => setArtError(true)}
          />
        )}
        {playing && isCurrent && (
          <div className="module-list-playing-badge">
            <PlayIcon size={16} />
          </div>
        )}
      </div>
      <div className="module-list-info">
        <div className="module-list-title">{track.title}</div>
        <div className="module-list-artist">{track.artist}</div>
      </div>
      {menu && (
        <TrackContextMenu x={menu.x} y={menu.y} track={menu.track} onClose={() => setMenu(null)} />
      )}
    </div>
  )
}

function MagicTrackShelf({ tracks }: { tracks: PlaylistTrack[] }): React.JSX.Element {
  return (
    <div className="module-shelf-wrapper">
      <div className="module-shelf" role="region" aria-label="Playlist tracks shelf">
        {tracks.map((track) => (
          <MagicTrackCard key={track.id} track={track} />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MagicPlaylistModule — the main module component
// ---------------------------------------------------------------------------

export function MagicPlaylistModule({ displayStyle, moduleId }: ModuleProps): React.JSX.Element {
  const id = moduleId ?? ''
  const configs = useStore((s) => s.magicPlaylistConfigs)
  const serverStatus = useStore((s) => s.serverStatus)
  const magicPlaylistVersion = useStore((s) => s.magicPlaylistVersion)
  const config = configs[id] ?? DEFAULT_CONFIG

  const [albums, setAlbums] = useState<Album[]>([])
  const [artists, setArtists] = useState<Artist[]>([])
  const [tracks, setTracks] = useState<PlaylistTrack[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (serverStatus !== 'connected' || config.playlistId == null) return
    getMagicPlaylistModuleContent(config.playlistId, config.contents, config.sort, config.items)
      .then((data) => {
        if (config.contents === 'albums') {
          setAlbums(data as Album[])
          setArtists([])
          setTracks([])
        } else if (config.contents === 'artists') {
          setArtists(data as Artist[])
          setAlbums([])
          setTracks([])
        } else {
          setTracks(data as PlaylistTrack[])
          setAlbums([])
          setArtists([])
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [
    config.playlistId,
    config.contents,
    config.sort,
    config.items,
    serverStatus,
    magicPlaylistVersion
  ])

  if (config.playlistId == null) {
    return <div className="module-empty">Choose a playlist in the settings above.</div>
  }

  if (loading) {
    return (
      <div className="module-skeleton-row">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="module-skeleton-card" />
        ))}
      </div>
    )
  }

  // Albums
  if (config.contents === 'albums') {
    if (albums.length === 0) return <div className="module-empty">No albums found.</div>
    if (displayStyle === 'list') return <ListView albums={albums} />
    if (displayStyle === 'grid') return <GridView albums={albums} />
    return <ShelfView albums={albums} />
  }

  // Artists
  if (config.contents === 'artists') {
    if (artists.length === 0) return <div className="module-empty">No artists found.</div>
    if (displayStyle === 'list') {
      return (
        <div className="module-list">
          {artists.map((artist) => (
            <MagicArtistListRow key={artist.name} artist={artist} />
          ))}
        </div>
      )
    }
    if (displayStyle === 'grid') {
      return (
        <div className="album-grid module-grid">
          {artists.map((artist) => (
            <MagicArtistCard key={artist.name} artist={artist} />
          ))}
        </div>
      )
    }
    return <MagicArtistShelf artists={artists} />
  }

  // Tracks
  if (tracks.length === 0) return <div className="module-empty">No tracks found.</div>
  if (displayStyle === 'list') {
    return (
      <div className="module-list">
        {tracks.map((track) => (
          <MagicTrackListRow key={track.id} track={track} />
        ))}
      </div>
    )
  }
  if (displayStyle === 'grid') {
    return (
      <div className="album-grid module-grid">
        {tracks.map((track) => (
          <MagicTrackCard key={track.id} track={track} />
        ))}
      </div>
    )
  }
  return <MagicTrackShelf tracks={tracks} />
}
