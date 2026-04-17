/**
 * Kamp API client.
 *
 * All fetch() and WebSocket calls live here. Components never touch fetch()
 * directly — this module is the single place to change if the base URL or
 * wire format changes.
 */

export type Track = {
  title: string
  artist: string
  album_artist: string
  album: string
  year: string
  track_number: number
  disc_number: number
  file_path: string
  ext: string
  embedded_art: boolean
  mb_release_id: string
  mb_recording_id: string
  favorite: boolean
  play_count: number
}

export type Album = {
  album_artist: string
  album: string
  year: string
  track_count: number
  has_art: boolean
  missing_album: boolean
  // Non-empty when missing_album=true; used as the unique lookup key.
  file_path: string
}

export type PlayerState = {
  playing: boolean
  position: number
  duration: number
  volume: number
  current_track: Track | null
}

export type ScanResult = {
  added: number
  removed: number
  unchanged: number
  updated: number
}

// Configurable base URL: defaults to localhost but can be overridden via
// environment variable for remote / mobile use cases.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
const WS_BASE = BASE_URL.replace(/^http/, 'ws')

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { method: 'DELETE' })
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const json = (await res.json()) as { detail?: string }
      if (json.detail) message = json.detail
    } catch {
      // JSON parse failed — fall back to the HTTP status message.
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  if (!res.ok) {
    // Prefer the server's detail message over the raw HTTP status text.
    let message = `${res.status} ${res.statusText}`
    try {
      const json = (await res.json()) as { detail?: string }
      if (json.detail) message = json.detail
    } catch {
      // JSON parse failed — fall back to the HTTP status message.
    }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------

export const getAlbums = (sort = 'album_artist'): Promise<Album[]> =>
  get(`/api/v1/albums?sort=${encodeURIComponent(sort)}`)

// Returns the URL for an album's cover art; load it in an <img> src.
// The server returns 404 when no art is embedded — handle with onError.
// Pass filePath for missing-album tracks to look up by file instead of album key.
export const artUrl = (albumArtist: string, album: string, filePath = ''): string => {
  const base = `${BASE_URL}/api/v1/album-art?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  return filePath ? `${base}&file_path=${encodeURIComponent(filePath)}` : base
}

export const getArtists = (): Promise<string[]> => get('/api/v1/artists')

export const getTracksForAlbum = (
  albumArtist: string,
  album: string,
  filePath = ''
): Promise<Track[]> => {
  const base = `/api/v1/tracks?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  return get(filePath ? `${base}&file_path=${encodeURIComponent(filePath)}` : base)
}

export type SearchResult = {
  albums: Album[]
  tracks: Track[]
}

export const search = (q: string, sort = 'album_artist'): Promise<SearchResult> =>
  get(`/api/v1/search?q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}`)

export type QueueState = {
  tracks: Track[]
  position: number
}

export const getQueue = (): Promise<QueueState> => get('/api/v1/player/queue')

export const scanLibrary = (): Promise<ScanResult> => post('/api/v1/library/scan')

export const setLibraryPath = (path: string): Promise<{ ok: boolean }> =>
  post('/api/v1/config/library-path', { path })

export type UiState = {
  active_view: 'library' | 'now-playing'
  sort_order: 'album_artist' | 'album' | 'date_added' | 'last_played'
  queue_panel_open: boolean
}

export const getUiState = (): Promise<UiState> => get('/api/v1/ui')
export const setActiveViewApi = (view: 'library' | 'now-playing'): Promise<{ ok: boolean }> =>
  post('/api/v1/ui/active-view', { view })
export const setSortOrderApi = (
  sortOrder: 'album_artist' | 'album' | 'date_added' | 'last_played'
): Promise<{ ok: boolean }> => post('/api/v1/ui/sort-order', { sort_order: sortOrder })
export const setQueuePanelApi = (open: boolean): Promise<{ ok: boolean }> =>
  post('/api/v1/ui/queue-panel', { open })

export type ScanProgress = { active: boolean; current: number; total: number }

export const getScanProgress = (): Promise<ScanProgress> => get('/api/v1/library/scan/progress')

export type ConfigValues = {
  'paths.watch_folder': string | null
  'paths.library': string | null
  'musicbrainz.contact': string | null
  'musicbrainz.trust-musicbrainz-when-tags-conflict': boolean | null
  'artwork.min_dimension': number | null
  'artwork.max_bytes': number | null
  'library.path_template': string | null
  'bandcamp.username': string | null
  'bandcamp.format': string | null
  'bandcamp.poll_interval_minutes': number | null
  'lastfm.username': string | null
}

export const getConfig = (): Promise<ConfigValues> => get('/api/v1/config')

export const patchConfig = (key: string, value: string): Promise<{ ok: boolean }> =>
  patch('/api/v1/config', { key, value })

export const connectLastfm = (
  username: string,
  password: string
): Promise<{ ok: boolean; username: string }> =>
  post('/api/v1/lastfm/connect', { username, password })

export const disconnectLastfm = (): Promise<{ ok: boolean }> => del('/api/v1/lastfm/connect')

export const getBandcampStatus = (): Promise<{ connected: boolean; username: string | null }> =>
  get('/api/v1/bandcamp/status')

export const disconnectBandcamp = (): Promise<{ ok: boolean }> => del('/api/v1/bandcamp/connect')

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------

export const getPlayerState = (): Promise<PlayerState> => get('/api/v1/player/state')

export const playAlbum = (
  albumArtist: string,
  album: string,
  trackIndex = 0,
  filePath = ''
): Promise<unknown> =>
  post('/api/v1/player/play', {
    album_artist: albumArtist,
    album,
    track_index: trackIndex,
    file_path: filePath
  })

export const pause = (): Promise<unknown> => post('/api/v1/player/pause')
export const resume = (): Promise<unknown> => post('/api/v1/player/resume')
export const stop = (): Promise<unknown> => post('/api/v1/player/stop')
export const seek = (position: number): Promise<unknown> =>
  post('/api/v1/player/seek', { position })
export const setVolume = (volume: number): Promise<unknown> =>
  post('/api/v1/player/volume', { volume })
export const nextTrack = (): Promise<unknown> => post('/api/v1/player/next')
export const prevTrack = (): Promise<unknown> => post('/api/v1/player/prev')
export const setShuffle = (shuffle: boolean): Promise<unknown> =>
  post('/api/v1/player/shuffle', { shuffle })
export const setRepeat = (repeat: boolean): Promise<unknown> =>
  post('/api/v1/player/repeat', { repeat })
export const addAlbumToQueue = (
  albumArtist: string,
  album: string,
  filePath = ''
): Promise<unknown> =>
  post('/api/v1/player/queue/add-album', { album_artist: albumArtist, album, file_path: filePath })
export const playAlbumNext = (
  albumArtist: string,
  album: string,
  filePath = ''
): Promise<unknown> =>
  post('/api/v1/player/queue/play-album-next', {
    album_artist: albumArtist,
    album,
    file_path: filePath
  })
export const insertAlbumAt = (
  albumArtist: string,
  album: string,
  index: number,
  filePath = ''
): Promise<unknown> =>
  post('/api/v1/player/queue/insert-album', {
    album_artist: albumArtist,
    album,
    index,
    file_path: filePath
  })
export const addToQueue = (filePath: string): Promise<unknown> =>
  post('/api/v1/player/queue/add', { file_path: filePath })
export const insertIntoQueue = (filePath: string, index: number): Promise<unknown> =>
  post('/api/v1/player/queue/insert', { file_path: filePath, index })
export const playNext = (filePath: string): Promise<unknown> =>
  post('/api/v1/player/queue/play-next', { file_path: filePath })
export const moveQueueTrack = (fromIndex: number, toIndex: number): Promise<unknown> =>
  post('/api/v1/player/queue/move', { from_index: fromIndex, to_index: toIndex })
export const skipToQueueTrack = (position: number): Promise<unknown> =>
  post('/api/v1/player/queue/skip-to', { position })
export const clearQueue = (): Promise<unknown> => post('/api/v1/player/queue/clear', {})
export const clearRemainingQueue = (position: number): Promise<unknown> =>
  post('/api/v1/player/queue/clear-remaining', { position })
export const setTrackFavorite = (filePath: string, favorite: boolean): Promise<unknown> =>
  post('/api/v1/tracks/favorite', { file_path: filePath, favorite })

// ---------------------------------------------------------------------------
// WebSocket state stream
// ---------------------------------------------------------------------------

export type StateMessage = PlayerState & { type: 'player.state' }
export type LibraryChangedMessage = { type: 'library.changed' }
export type ServerMessage = StateMessage | LibraryChangedMessage

export function connectStateStream(
  onState: (state: PlayerState) => void,
  onClose?: () => void,
  onOpen?: () => void,
  onLibraryChanged?: () => void
): () => void {
  const ws = new WebSocket(`${WS_BASE}/api/v1/ws`)

  ws.onopen = () => onOpen?.()

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data as string) as ServerMessage
      if (msg.type === 'player.state') onState(msg)
      else if (msg.type === 'library.changed') onLibraryChanged?.()
    } catch {
      // malformed message — ignore
    }
  }

  ws.onclose = () => onClose?.()

  // Keep state fresh while playing: poll at ~4 Hz.
  const interval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) ws.send('ping')
  }, 250)

  return () => {
    clearInterval(interval)
    ws.close()
  }
}
