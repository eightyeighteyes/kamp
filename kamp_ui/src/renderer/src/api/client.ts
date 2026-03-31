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
}

export type Album = {
  album_artist: string
  album: string
  year: string
  track_count: number
  has_art: boolean
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

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------

export const getAlbums = (sort = 'album_artist'): Promise<Album[]> =>
  get(`/api/v1/albums?sort=${encodeURIComponent(sort)}`)

// Returns the URL for an album's cover art; load it in an <img> src.
// The server returns 404 when no art is embedded — handle with onError.
export const artUrl = (albumArtist: string, album: string): string =>
  `${BASE_URL}/api/v1/album-art?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`

export const getArtists = (): Promise<string[]> => get('/api/v1/artists')

export const getTracksForAlbum = (albumArtist: string, album: string): Promise<Track[]> =>
  get(
    `/api/v1/tracks?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  )

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

export type UiState = { active_view: 'library' | 'now-playing' }

export const getUiState = (): Promise<UiState> => get('/api/v1/ui')
export const setActiveViewApi = (view: 'library' | 'now-playing'): Promise<{ ok: boolean }> =>
  post('/api/v1/ui/active-view', { view })

export type ScanProgress = { active: boolean; current: number; total: number }

export const getScanProgress = (): Promise<ScanProgress> => get('/api/v1/library/scan/progress')

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------

export const getPlayerState = (): Promise<PlayerState> => get('/api/v1/player/state')

export const playAlbum = (albumArtist: string, album: string, trackIndex = 0): Promise<unknown> =>
  post('/api/v1/player/play', { album_artist: albumArtist, album, track_index: trackIndex })

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
export const addAlbumToQueue = (albumArtist: string, album: string): Promise<unknown> =>
  post('/api/v1/player/queue/add-album', { album_artist: albumArtist, album })
export const playAlbumNext = (albumArtist: string, album: string): Promise<unknown> =>
  post('/api/v1/player/queue/play-album-next', { album_artist: albumArtist, album })
export const insertAlbumAt = (
  albumArtist: string,
  album: string,
  index: number
): Promise<unknown> =>
  post('/api/v1/player/queue/insert-album', { album_artist: albumArtist, album, index })
export const addToQueue = (filePath: string): Promise<unknown> =>
  post('/api/v1/player/queue/add', { file_path: filePath })
export const insertIntoQueue = (filePath: string, index: number): Promise<unknown> =>
  post('/api/v1/player/queue/insert', { file_path: filePath, index })
export const playNext = (filePath: string): Promise<unknown> =>
  post('/api/v1/player/queue/play-next', { file_path: filePath })
export const moveQueueTrack = (fromIndex: number, toIndex: number): Promise<unknown> =>
  post('/api/v1/player/queue/move', { from_index: fromIndex, to_index: toIndex })

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
