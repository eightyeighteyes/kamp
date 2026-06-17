/**
 * Kamp API client.
 *
 * All fetch() and WebSocket calls live here. Components never touch fetch()
 * directly — this module is the single place to change if the base URL or
 * wire format changes.
 */

export type Track = {
  id: number
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
  genre: string
  label: string
  favorite: boolean
  play_count: number
  source: string
  reachable: boolean
  is_available: boolean
  duration: number
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
  // MAX(file_mtime) across the album's tracks; appended to art URLs as ?v=
  // so the browser caches by URL and only re-fetches when files change on disk.
  art_version: number | null
  // MIN(date_added) across the album's tracks — used by the New Arrivals module.
  added_at: number | null
  // MAX(last_played) across the album's tracks — used by the Last Played module.
  last_played_at: number | null
  // SUM(play_count) / COUNT(*) across tracks — used by the Top Albums module.
  play_count_avg: number
  // True when the user has favorited this album (KAMP-293).
  favorite: boolean
  // True when any track in this album is individually favorited (KAMP-294).
  has_favorite_track: boolean
  // 'local' | 'bandcamp' | 'mixed' — derived from constituent track sources.
  source: 'local' | 'bandcamp' | 'mixed'
  // True when any track in this album has source !== 'local'.
  has_remote_tracks: boolean
  // Bandcamp sale_item_id parsed from constituent track file paths; undefined for local albums.
  sale_item_id?: string
  // True when this album is a Bandcamp pre-order (some tracks not yet released).
  is_preorder?: boolean
  // Bandcamp album page URL — non-empty for Bandcamp albums; used for sharing.
  album_url?: string
  // User-set display overrides for streaming albums (KAMP-467). Undefined means no override.
  display_album?: string
  display_album_artist?: string
}

export type PlayerState = {
  playing: boolean
  position: number
  duration: number
  volume: number
  current_track: Track | null
  next_track: Track | null
  buffering: boolean
}

export type ScanResult = {
  added: number
  removed: number
  unchanged: number
  updated: number
}

export type CriteriaField =
  | 'track.favorite'
  | 'album.favorite'
  | 'track.play_count'
  | 'track.year'
  | 'track.last_played'
  | 'track.date_added'
  | 'track.genre'
  | 'track.artist'
  | 'track.album_artist'
  | 'track.album'
  | 'track.source'
  | 'in_playlist'

export type CriteriaOperator =
  | 'is'
  | 'is_not'
  | 'gt'
  | 'lt'
  | 'gte'
  | 'lte'
  | 'contains'
  | 'not_contains'

export type CriteriaCondition = { field: CriteriaField; op: CriteriaOperator; value: string }
export type CriteriaGroup = {
  match: 'all' | 'any'
  negate: boolean
  conditions: CriteriaCondition[]
}
export type CriteriaDoc = { match: 'all' | 'any'; groups: CriteriaGroup[] }

export type Playlist = {
  id: number
  title: string
  favorite: boolean
  track_count: number
  created_at: number
  updated_at: number
  last_played_at: number | null
  criteria: CriteriaDoc | null
}

export type PlaylistTrack = Track & {
  playlist_track_id: number
  position: number
  last_played: number | null
}

// Configurable base URL: defaults to localhost but can be overridden via
// environment variable for remote / mobile use cases.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:47483'
const WS_BASE = BASE_URL.replace(/^http/, 'ws')

// Re-read on each call so a daemon restart's fresh token is always used.
function _getToken(): string | null {
  return window.api?.getApiToken?.() ?? null
}

function _authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = _getToken()
  return token ? { 'X-Kamp-Token': token, ...extra } : { ...extra }
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers:
      body !== undefined ? _authHeaders({ 'Content-Type': 'application/json' }) : _authHeaders(),
    body: body !== undefined ? JSON.stringify(body) : undefined
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: _authHeaders() })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { method: 'DELETE', headers: _authHeaders() })
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
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
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

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'PUT',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body)
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------

export const getAlbums = (sort = 'album_artist', dir = ''): Promise<Album[]> =>
  get(
    `/api/v1/albums?sort=${encodeURIComponent(sort)}${dir ? `&direction=${encodeURIComponent(dir)}` : ''}`
  )

// Returns the URL for an album's cover art; load it in an <img> src.
// The server returns 404 when no art is embedded — handle with onError.
// Pass filePath for missing-album tracks to look up by file instead of album key.
// artVersion (MAX file_mtime across the album's tracks) is appended as ?v=
// so the browser caches by URL and only re-fetches when files change on disk.
export const artUrl = (
  albumArtist: string,
  album: string,
  filePath = '',
  artVersion: number | null = null
): string => {
  const base = `${BASE_URL}/api/v1/album-art?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  const withPath = filePath ? `${base}&file_path=${encodeURIComponent(filePath)}` : base
  return artVersion != null ? `${withPath}&v=${artVersion}` : withPath
}

export const playlistArtUrl = (playlistId: number, version?: number): string => {
  const base = `${BASE_URL}/api/v1/playlists/${playlistId}/art`
  return version != null ? `${base}?v=${version}` : base
}

export type Artist = {
  name: string
  play_time: number // total elapsed playback seconds
  top_album: string | null
}

export const getArtists = (): Promise<string[]> => get('/api/v1/artists')

export const getTopArtists = (limit: number): Promise<Artist[]> =>
  get(`/api/v1/artists/top?limit=${limit}`)

export const getTopTracks = (limit: number): Promise<Track[]> =>
  get(`/api/v1/tracks/top?limit=${limit}`)

export const getTracksForAlbum = (
  albumArtist: string,
  album: string,
  filePath = ''
): Promise<Track[]> => {
  const base = `/api/v1/tracks?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  return get(filePath ? `${base}&file_path=${encodeURIComponent(filePath)}` : base)
}

export type PlaylistSearchResult = Playlist & { source: string }

export type SearchResult = {
  albums: Album[]
  tracks: Track[]
  playlists: PlaylistSearchResult[]
}

export const search = (q: string, sort = 'album_artist'): Promise<SearchResult> =>
  get(`/api/v1/search?q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}`)

export type QueueState = {
  tracks: Track[]
  position: number
  shuffle: boolean
  repeat: boolean
}

export const getQueue = (): Promise<QueueState> => get('/api/v1/player/queue')

export const scanLibrary = (): Promise<ScanResult> => post('/api/v1/library/scan')

export const setLibraryPath = (path: string): Promise<{ ok: boolean }> =>
  post('/api/v1/config/library-path', { path })

export type UiState = {
  active_view: 'library' | 'now-playing' | 'home'
  sort_order: 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played'
  sort_dir: 'asc' | 'desc'
  queue_panel_open: boolean
}

export const getUiState = (): Promise<UiState> => get('/api/v1/ui')
export const setActiveViewApi = (
  view: 'library' | 'now-playing' | 'home'
): Promise<{ ok: boolean }> => post('/api/v1/ui/active-view', { view })
export const setSortOrderApi = (
  sortOrder: 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played',
  sortDir: 'asc' | 'desc'
): Promise<{ ok: boolean }> =>
  post('/api/v1/ui/sort-order', { sort_order: sortOrder, sort_dir: sortDir })
export const setQueuePanelApi = (open: boolean): Promise<{ ok: boolean }> =>
  post('/api/v1/ui/queue-panel', { open })

export type ScanProgress = {
  active: boolean
  current: number
  total: number
  current_file?: string | null
  current_artist?: string | null
  top_artist?: string | null
  num_albums?: number | null
  num_artists?: number | null
}

export const getScanProgress = (): Promise<ScanProgress> => get('/api/v1/library/scan/progress')

export type ConfigValues = {
  'paths.watch_folder': string | null
  'paths.library': string | null
  'musicbrainz.contact': string | null
  'musicbrainz.trust-musicbrainz-when-tags-conflict': boolean | null
  'artwork.min_dimension': number | null
  'artwork.max_bytes': number | null
  'artwork.save_format': string | null
  'library.path_template': string | null
  'bandcamp.connected': boolean | null
  'bandcamp.username': string | null
  'bandcamp.ever_connected': boolean | null
  'bandcamp.format': string | null
  'bandcamp.poll_interval_minutes': number | null
  'bandcamp.collection_mode': string | null
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

export const downloadAlbum = (saleItemId: string): Promise<{ ok: boolean }> =>
  post(`/api/v1/bandcamp/collection/${encodeURIComponent(saleItemId)}/download`)

export const removeDownload = (saleItemId: string): Promise<{ ok: boolean }> =>
  del(`/api/v1/bandcamp/collection/${encodeURIComponent(saleItemId)}/download`)

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
export const reorderQueue = (order: number[]): Promise<unknown> =>
  post('/api/v1/player/queue/reorder', { order })
export const skipToQueueTrack = (position: number): Promise<unknown> =>
  post('/api/v1/player/queue/skip-to', { position })
export const clearQueue = (): Promise<unknown> => post('/api/v1/player/queue/clear', {})
export const clearRemainingQueue = (position: number): Promise<unknown> =>
  post('/api/v1/player/queue/clear-remaining', { position })
export const removeFromQueue = (indices: number[]): Promise<unknown> =>
  post('/api/v1/player/queue/remove', { indices })
export const setTrackFavorite = (track: Track, favorite: boolean): Promise<unknown> =>
  post('/api/v1/tracks/favorite', { file_path: track.file_path, favorite })

export type TrackTagsCollision = {
  collision: true
  target_path: string
  existing_track_id: number | null
}

export type TrackTagsDeferred = { deferred: true; op_id: number }

export async function patchTrackTags(
  trackId: number,
  title: string,
  overwrite = false
): Promise<Track | TrackTagsCollision | TrackTagsDeferred> {
  const res = await fetch(`${BASE_URL}/api/v1/tracks/${trackId}/tags`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ title, overwrite })
  })
  if (res.status === 202) return res.json() as Promise<TrackTagsDeferred>
  if (res.status === 409) {
    const detail = (await res.json()) as {
      detail: { target_path: string; existing_track_id: number | null }
    }
    return { collision: true, ...detail.detail }
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const json = (await res.json()) as { detail?: string }
      if (json.detail && typeof json.detail === 'string') message = json.detail
    } catch {
      // ignore
    }
    throw new Error(message)
  }
  return res.json() as Promise<Track>
}
export async function patchTrackDisplay(
  trackId: number,
  displayTitle: string | null
): Promise<Track> {
  const res = await fetch(`${BASE_URL}/api/v1/tracks/${trackId}/display`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ display_title: displayTitle || null })
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<Track>
}

export async function patchAlbumDisplay(
  albumArtist: string,
  album: string,
  displayAlbum: string | null,
  displayAlbumArtist: string | null
): Promise<Album> {
  const res = await fetch(`${BASE_URL}/api/v1/albums/display`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      album_artist: albumArtist,
      album,
      display_album: displayAlbum || null,
      display_album_artist: displayAlbumArtist || null
    })
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<Album>
}

export const setAlbumFavorite = (
  albumArtist: string,
  album: string,
  favorite: boolean
): Promise<unknown> =>
  post('/api/v1/albums/favorite', { album_artist: albumArtist, album, favorite })

export type AlbumTagsCollision = {
  collision: true
  collision_count: number
  first_path: string
}

export type AlbumTagsResult = {
  moved: Track[]
  deferred: { track_id: number; op_id: number; old_path: string; new_path: string }[]
  skipped: string[]
  failed: { track_id: number; old_path: string; new_path: string; error: string | null }[]
}

export async function patchAlbumTags(
  albumArtist: string,
  album: string,
  opts: { album?: string; album_artist?: string; overwrite?: boolean; skip_conflicts?: boolean }
): Promise<AlbumTagsResult | AlbumTagsCollision> {
  const params = new URLSearchParams({ album_artist: albumArtist, album })
  const res = await fetch(`${BASE_URL}/api/v1/albums/tags?${params}`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(opts)
  })
  if (res.status === 409) {
    const body = (await res.json()) as {
      detail: { collision_count: number; first_path: string } | string
    }
    // Filesystem collision (overwritable) — detail is an object with collision_count.
    if (typeof body.detail === 'object' && body.detail !== null) {
      return { collision: true, ...body.detail }
    }
    // Non-overridable conflict (e.g. album name already exists in streaming library).
    throw new Error(typeof body.detail === 'string' ? body.detail : '409 Conflict')
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const json = (await res.json()) as { detail?: string }
      if (json.detail && typeof json.detail === 'string') message = json.detail
    } catch {
      // ignore
    }
    throw new Error(message)
  }
  return res.json() as Promise<AlbumTagsResult>
}

export type AlbumMetaResult = {
  tracks: Track[]
}

export const patchAlbumMeta = (
  albumArtist: string,
  album: string,
  opts: { genre?: string; label?: string; year?: string; mb_release_id?: string }
): Promise<AlbumMetaResult> => {
  const params = new URLSearchParams({ album_artist: albumArtist, album })
  return patch(`/api/v1/albums/meta?${params}`, opts)
}

// ---------------------------------------------------------------------------
// MusicBrainz lookup (KAMP-230)
// ---------------------------------------------------------------------------

export type MusicBrainzTrack = {
  track_number: number
  disc_number: number
  title: string
  recording_mbid: string
}

export type MusicBrainzRelease = {
  mbid: string
  release_group_mbid: string
  title: string
  album_artist: string
  year: string
  label: string
  release_type: string
  tracks: MusicBrainzTrack[]
}

export async function fetchMusicBrainzCandidates(
  albumArtist: string,
  album: string,
  signal: AbortSignal
): Promise<MusicBrainzRelease[]> {
  const params = new URLSearchParams({ album_artist: albumArtist, album })
  const res = await fetch(`${BASE_URL}/api/v1/albums/musicbrainz?${params}`, {
    headers: _authHeaders(),
    signal
  })
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const json = (await res.json()) as { detail?: string }
      if (json.detail && typeof json.detail === 'string') message = json.detail
    } catch {
      // ignore
    }
    throw new Error(message)
  }
  const data = (await res.json()) as { candidates: MusicBrainzRelease[] }
  return data.candidates
}

export async function patchTrackMeta(trackId: number, mbRecordingId: string): Promise<Track> {
  const res = await fetch(`${BASE_URL}/api/v1/tracks/${trackId}/meta`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ mb_recording_id: mbRecordingId })
  })
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const json = (await res.json()) as { detail?: string }
      if (json.detail && typeof json.detail === 'string') message = json.detail
    } catch {
      // ignore
    }
    throw new Error(message)
  }
  return res.json() as Promise<Track>
}

// ---------------------------------------------------------------------------
// WebSocket state stream
// ---------------------------------------------------------------------------

export type StateMessage = PlayerState & { type: 'player.state' }
export type TrackChangedMessage = PlayerState & { type: 'track.changed' }
export type LibraryChangedMessage = { type: 'library.changed' }
export type AlbumRenameProgressMessage = {
  type: 'album.rename.progress'
  done: number
  total: number
}
export type DeferredOpCompletedMessage = {
  type: 'deferred_op.completed'
  op_id: number
  track_id: number
}
export type AudioLevelMessage = {
  type: 'audio.level'
  left_db: number
  right_db: number
  crest_db: number
  peak_db: number
}
export type AlbumDownloadMessage = {
  type: 'bandcamp.album-download'
  sale_item_id: string
  state: 'queued' | 'downloading' | 'done' | 'error' | 'removed'
}
export type MagicPlaylistUpdatedMessage = {
  type: 'magic_playlist.updated'
  id: number
}
export type ServerMessage =
  | StateMessage
  | TrackChangedMessage
  | LibraryChangedMessage
  | AlbumRenameProgressMessage
  | DeferredOpCompletedMessage
  | AudioLevelMessage
  | AlbumDownloadMessage
  | MagicPlaylistUpdatedMessage

export async function getDeferredOps(): Promise<{ op_id: number; track_id: number }[]> {
  const res = await fetch(`${BASE_URL}/api/v1/deferred-ops`, {
    headers: _authHeaders()
  })
  if (!res.ok) return []
  return res.json() as Promise<{ op_id: number; track_id: number }[]>
}

export function connectStateStream(
  onState: (state: PlayerState) => void,
  onClose?: () => void,
  onOpen?: () => void,
  onLibraryChanged?: () => void,
  onAlbumRenameProgress?: (done: number, total: number) => void,
  onDeferredOpCompleted?: (trackId: number, opId: number) => void,
  onAudioLevel?: (leftDb: number, rightDb: number, crestDb: number, peakDb: number) => void,
  onTrackChanged?: () => void,
  onAlbumDownload?: (
    saleItemId: string,
    state: 'queued' | 'downloading' | 'done' | 'error' | 'removed'
  ) => void,
  onMagicPlaylistUpdated?: (id: number) => void
): () => void {
  const ws = new WebSocket(`${WS_BASE}/api/v1/ws`)

  ws.onopen = () => onOpen?.()

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data as string) as ServerMessage
      if (msg.type === 'player.state') onState(msg)
      else if (msg.type === 'track.changed') onTrackChanged?.()
      else if (msg.type === 'library.changed') onLibraryChanged?.()
      else if (msg.type === 'album.rename.progress') onAlbumRenameProgress?.(msg.done, msg.total)
      else if (msg.type === 'deferred_op.completed')
        onDeferredOpCompleted?.(msg.track_id, msg.op_id)
      else if (msg.type === 'audio.level')
        onAudioLevel?.(msg.left_db, msg.right_db, msg.crest_db, msg.peak_db)
      else if (msg.type === 'bandcamp.album-download')
        onAlbumDownload?.(msg.sale_item_id, msg.state)
      else if (msg.type === 'magic_playlist.updated') onMagicPlaylistUpdated?.(msg.id)
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

// ---------------------------------------------------------------------------
// iTunes album art search / apply (KAMP-341)
// ---------------------------------------------------------------------------

export type ItunesArtCandidate = {
  title: string
  artist: string
  preview_url: string
  // mzstatic URL with "{size}" placeholder (e.g. replace with "600x600bb")
  artwork_url_template: string
}

export async function searchAlbumArt(
  albumArtist: string,
  album: string,
  signal: AbortSignal
): Promise<ItunesArtCandidate[]> {
  const params = new URLSearchParams({ album_artist: albumArtist, album })
  const res = await fetch(`${BASE_URL}/api/v1/albums/art/search?${params}`, {
    headers: _authHeaders(),
    signal
  })
  if (!res.ok) throw new Error(`art search failed: ${res.status}`)
  const data = await res.json()
  return data.candidates as ItunesArtCandidate[]
}

export async function applyAlbumArt(
  albumArtist: string,
  album: string,
  artworkUrlTemplate: string
): Promise<Album> {
  const res = await fetch(`${BASE_URL}/api/v1/albums/art/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._authHeaders() },
    body: JSON.stringify({
      album_artist: albumArtist,
      album,
      artwork_url_template: artworkUrlTemplate
    })
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail?.detail ?? res.statusText)
  }
  return res.json() as Promise<Album>
}

export async function applyAlbumArtLocal(
  albumArtist: string,
  album: string,
  file: File
): Promise<Album> {
  const form = new FormData()
  form.append('album_artist', albumArtist)
  form.append('album', album)
  form.append('file', file)
  const res = await fetch(`${BASE_URL}/api/v1/albums/art/apply-local`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail?.detail ?? res.statusText)
  }
  return res.json() as Promise<Album>
}

// ---------------------------------------------------------------------------
// Playlists (KAMP-441)
// ---------------------------------------------------------------------------

export async function applyPlaylistArtLocal(playlistId: number, file: File): Promise<Playlist> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE_URL}/api/v1/playlists/${playlistId}/art`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail?.detail ?? res.statusText)
  }
  return res.json() as Promise<Playlist>
}

export const playPlaylist = (playlistId: number, startIndex = 0): Promise<void> =>
  post('/api/v1/player/play-playlist', { playlist_id: playlistId, start_index: startIndex })

export const playFiles = (filePaths: string[], startIndex = 0): Promise<void> =>
  post('/api/v1/player/play-files', { file_paths: filePaths, start_index: startIndex })

export const getPlaylists = (): Promise<Playlist[]> => get('/api/v1/playlists')

export const getPlaylist = (id: number): Promise<Playlist> => get(`/api/v1/playlists/${id}`)

export const createPlaylist = (title: string): Promise<Playlist> =>
  post('/api/v1/playlists', { title })

export const patchPlaylist = (
  id: number,
  updates: { title?: string; favorite?: boolean }
): Promise<Playlist> => patch(`/api/v1/playlists/${id}`, updates)

export const deletePlaylist = (id: number): Promise<void> => del(`/api/v1/playlists/${id}`)

export const getPlaylistTracks = (id: number): Promise<PlaylistTrack[]> =>
  get(`/api/v1/playlists/${id}/tracks`)

export const addTrackToPlaylist = (id: number, filePath: string): Promise<void> =>
  post(`/api/v1/playlists/${id}/tracks`, { file_path: filePath })

export const addAlbumToPlaylist = (id: number, albumArtist: string, album: string): Promise<void> =>
  post(`/api/v1/playlists/${id}/tracks`, { album_artist: albumArtist, album })

export const removeTrackFromPlaylist = (id: number, playlistTrackId: number): Promise<void> =>
  del(`/api/v1/playlists/${id}/tracks/${playlistTrackId}`)

export const reorderPlaylistTracks = (id: number, trackIds: number[]): Promise<void> =>
  put(`/api/v1/playlists/${id}/order`, { track_ids: trackIds })

export const recordPlaylistPlayed = (id: number): Promise<void> =>
  post(`/api/v1/playlists/${id}/played`, {})

export const previewCriteria = (criteria: CriteriaDoc): Promise<{ count: number }> =>
  post('/api/v1/criteria/preview', { criteria })

export const createMagicPlaylist = (title: string, criteria: CriteriaDoc): Promise<Playlist> =>
  post('/api/v1/playlists', { title, criteria })

export const updateMagicPlaylistCriteria = (id: number, criteria: CriteriaDoc): Promise<Playlist> =>
  put(`/api/v1/playlists/${id}/criteria`, { criteria })
