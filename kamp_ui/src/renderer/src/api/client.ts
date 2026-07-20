/**
 * Kamp API client.
 *
 * All fetch() and WebSocket calls live here. Components never touch fetch()
 * directly — this module is the single place to change if the base URL or
 * wire format changes.
 */

// One way to obtain a track's bytes (KAMP-537). A track has one row per
// delivery — a local file and/or a bandcamp stream.
export type TrackSource = {
  kind: 'file' | 'stream'
  provider: string
  uri: string
  is_available: boolean
  duration: number
}

export type Track = {
  id: number
  title: string
  artist: string
  album_artist: string
  album: string
  release_date: string
  track_number: number
  disc_number: number
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
  // KAMP-552: identity is the canonical `id`; the delivery paths/uris live here
  // (there is no more track-level file_path).
  sources: TrackSource[]
}

// The uri a track is reached by: prefer a local file source, else the first
// source (e.g. a bandcamp:// stream), else '' (a synthetic queue-restore stub).
export function trackUri(t: Pick<Track, 'sources'>): string {
  const srcs = t.sources ?? []
  return (srcs.find((s) => s.kind === 'file') ?? srcs[0])?.uri ?? ''
}

// The bandcamp sale_item_id encoded in a track's stream uri (bandcamp://<sid>/<n>),
// or null when the track has no bandcamp source.
export function trackSaleItemId(t: Pick<Track, 'sources'>): string | null {
  const uri = (t.sources ?? []).find((s) => s.uri.startsWith('bandcamp:'))?.uri
  return uri ? (uri.split('bandcamp:')[1]?.replace(/^\/+/, '').split('/')[0] ?? null) : null
}

export type Album = {
  album_artist: string
  album: string
  release_date: string
  track_count: number
  has_art: boolean
  missing_album: boolean
  // Non-null when missing_album=true: the canonical id of the card's single track,
  // used as the unique lookup key instead of (album_artist, album) (KAMP-554).
  track_id: number | null
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
  // Streamable-track count Bandcamp reports; 0 => no streamable version, so
  // "Remove download" is hidden (it would strand the album) — KAMP-527.
  num_streamable_tracks?: number
  // Bandcamp album page URL — non-empty for Bandcamp albums; used for sharing.
  album_url?: string
  // User-set display overrides for streaming albums (KAMP-467). Undefined means no override.
  display_album?: string
  display_album_artist?: string
  // DISTINCT union of the album's track genres (KAMP-550); backs the library
  // genre filter. Canonical names, sorted NOCASE.
  genres: string[]
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
  | 'album.play_count_avg'
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
  | 'in_last_days'
  | 'in_last_weeks'
  | 'in_last_months'

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
  date_added: number | null
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
// Pass trackId for a missing-album card (album tag empty) to resolve the single
// track by its canonical id instead of the (album_artist, album) key (KAMP-554).
// version (MAX file_mtime across the album's tracks) is appended as ?v=
// so the browser caches by URL and only re-fetches when files change on disk.
export const artUrl = (
  albumArtist: string,
  album: string,
  opts: { trackId?: number | null; version?: number | null } = {}
): string => {
  const base = `${BASE_URL}/api/v1/album-art?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  const withId = opts.trackId != null ? `${base}&track_id=${opts.trackId}` : base
  return opts.version != null ? `${withId}&v=${opts.version}` : withId
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

export type Stats = {
  track_count: number
  album_count: number
  artist_count: number
  total_play_seconds: number
  total_track_plays: number
  albums_played: number
  top_artist_name: string | null
  top_artist_seconds: number | null
  top_tracks: Track[]
}

export const getStats = (topTracks = 3): Promise<Stats> =>
  get(`/api/v1/stats?top_tracks=${topTracks}`)

export type MagicPlaylistContents = 'albums' | 'artists' | 'tracks'
export type MagicPlaylistSort = 'random' | 'last_played' | 'recently_added' | 'most_played'

export const getMagicPlaylistModuleContent = (
  playlistId: number,
  contents: MagicPlaylistContents,
  sort: MagicPlaylistSort,
  limit: number
): Promise<Album[] | Artist[] | PlaylistTrack[]> =>
  get(
    `/api/v1/playlists/${playlistId}/module-content?contents=${contents}&sort=${sort}&limit=${limit}`
  )

export const getTracksForAlbum = (
  albumArtist: string,
  album: string,
  trackId: number | null = null
): Promise<Track[]> => {
  const base = `/api/v1/tracks?album_artist=${encodeURIComponent(albumArtist)}&album=${encodeURIComponent(album)}`
  return get(trackId != null ? `${base}&track_id=${trackId}` : base)
}

export type PlaylistSearchResult = Playlist & { source: string }

export type SearchResult = {
  albums: Album[]
  tracks: Track[]
  playlists: PlaylistSearchResult[]
}

export const search = (q: string, sort = 'album_artist'): Promise<SearchResult> =>
  get(`/api/v1/search?q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}`)

export type RepeatMode = 'off' | 'queue' | 'album' | 'single'

export type QueueState = {
  tracks: Track[]
  position: number
  shuffle: boolean
  repeat: RepeatMode
}

export const getQueue = (): Promise<QueueState> => get('/api/v1/player/queue')

export const scanLibrary = (): Promise<ScanResult> => post('/api/v1/library/scan')

export const setLibraryPath = (path: string): Promise<{ ok: boolean }> =>
  post('/api/v1/config/library-path', { path })

export type UiState = {
  active_view: 'library' | 'now-playing' | 'home' | 'downloads'
  sort_order:
    | 'album_artist'
    | 'album'
    | 'date_added'
    | 'last_played'
    | 'most_played'
    | 'release_date'
  sort_dir: 'asc' | 'desc'
  queue_panel_open: boolean
}

export const getUiState = (): Promise<UiState> => get('/api/v1/ui')
export const setActiveViewApi = (
  view: 'library' | 'now-playing' | 'home' | 'downloads'
): Promise<{ ok: boolean }> => post('/api/v1/ui/active-view', { view })
export const setSortOrderApi = (
  sortOrder:
    | 'album_artist'
    | 'album'
    | 'date_added'
    | 'last_played'
    | 'most_played'
    | 'release_date',
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

// KAMP-591: library-wide genre backfill (re-fetch genres for every album).
export type GenreBackfillProgress = {
  active: boolean
  done: number
  total: number
  state: 'idle' | 'running' | 'done' | 'cancelled' | 'error'
}

export const startGenreBackfill = (): Promise<{ ok: boolean; started: boolean }> =>
  post('/api/v1/genres/backfill')

export const cancelGenreBackfill = (): Promise<{ ok: boolean }> =>
  post('/api/v1/genres/backfill/cancel')

export const getGenreBackfillProgress = (): Promise<GenreBackfillProgress> =>
  get('/api/v1/genres/backfill/progress')

export type ConfigValues = {
  'paths.watch_folder': string | null
  'paths.library': string | null
  'musicbrainz.contact': string | null
  'artwork.min_dimension': number | null
  'artwork.max_bytes': number | null
  'artwork.save_format': string | null
  'tagging.lastfm_genres': boolean | null
  'tagging.bandcamp_genres': boolean | null
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
// Download queue (KAMP-568) — provider-neutral /api/v1/downloads surface
// ---------------------------------------------------------------------------

// One row of the download queue; mirrors the daemon's download_queue_items()
// (KAMP-564/566). Also the payload shape of the `download.queue` WS snapshot.
export type DownloadItem = {
  provider: string
  provider_item_id: string
  status: 'queued' | 'downloading' | 'failed'
  position: number
  size_bytes: number | null
  size_is_estimate: boolean
  error_text: string | null
  album_name: string | null
  album_artist: string | null
  artwork_ref: string | null
  queued_at: number
}

export const getDownloads = (): Promise<{ items: DownloadItem[] }> => get('/api/v1/downloads')

export const reorderDownloads = (providerItemIds: string[]): Promise<{ ok: boolean }> =>
  post('/api/v1/downloads/reorder', { provider_item_ids: providerItemIds })

export const retryDownload = (id: string): Promise<{ ok: boolean }> =>
  post(`/api/v1/downloads/${encodeURIComponent(id)}/retry`)

export const cancelDownload = (id: string): Promise<{ ok: boolean }> =>
  del(`/api/v1/downloads/${encodeURIComponent(id)}`)

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------

export const getPlayerState = (): Promise<PlayerState> => get('/api/v1/player/state')

export const playAlbum = (
  albumArtist: string,
  album: string,
  trackIndex = 0,
  trackId: number | null = null
): Promise<unknown> =>
  post('/api/v1/player/play', {
    album_artist: albumArtist,
    album,
    track_index: trackIndex,
    id: trackId
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
export const setShuffle = (shuffle: boolean, albumShuffle = false): Promise<unknown> =>
  post('/api/v1/player/shuffle', { shuffle, album_shuffle: albumShuffle })
export const setRepeat = (mode: RepeatMode): Promise<unknown> =>
  post('/api/v1/player/repeat', { mode })
export const addAlbumToQueue = (
  albumArtist: string,
  album: string,
  trackId: number | null = null
): Promise<unknown> =>
  post('/api/v1/player/queue/add-album', { album_artist: albumArtist, album, id: trackId })
export const playAlbumNext = (
  albumArtist: string,
  album: string,
  trackId: number | null = null
): Promise<unknown> =>
  post('/api/v1/player/queue/play-album-next', {
    album_artist: albumArtist,
    album,
    id: trackId
  })
export const insertAlbumAt = (
  albumArtist: string,
  album: string,
  index: number,
  trackId: number | null = null
): Promise<unknown> =>
  post('/api/v1/player/queue/insert-album', {
    album_artist: albumArtist,
    album,
    index,
    id: trackId
  })
// KAMP-552: a track is addressed by its canonical id only (the file_path fallback
// is gone). id wins server-side.
export type TrackRef = { id: number }

export const addToQueue = (ref: TrackRef): Promise<unknown> =>
  post('/api/v1/player/queue/add', { id: ref.id })
export const insertIntoQueue = (ref: TrackRef, index: number): Promise<unknown> =>
  post('/api/v1/player/queue/insert', { id: ref.id, index })
export const playNext = (ref: TrackRef): Promise<unknown> =>
  post('/api/v1/player/queue/play-next', { id: ref.id })
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
  // KAMP-538: identify the track by its canonical id (server is id-preferred).
  post('/api/v1/tracks/favorite', { id: track.id, favorite })

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
// Partial update (KAMP-582): send ONLY the field being edited — the server
// leaves absent fields untouched, so a display_artist edit can't clear a
// display_title override (or vice versa). Explicit null clears a field.
export type TrackDisplayFields = {
  display_title?: string | null
  display_artist?: string | null
}

export async function patchTrackDisplay(
  trackId: number,
  fields: TrackDisplayFields
): Promise<Track> {
  const res = await fetch(`${BASE_URL}/api/v1/tracks/${trackId}/display`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(fields)
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<Track>
}

export type TrackArtistDeferred = { deferred: true; op_id: number }

export async function patchTrackArtist(
  trackId: number,
  artist: string
): Promise<Track | TrackArtistDeferred> {
  const res = await fetch(`${BASE_URL}/api/v1/tracks/${trackId}/artist`, {
    method: 'PATCH',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ artist })
  })
  // 202: the track is playing — DB updated, file write deferred to track-end.
  if (res.status === 202) return res.json() as Promise<TrackArtistDeferred>
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
  opts: {
    genre?: string
    // Multi-value genre (KAMP-586): the album's full genre set, applied to every track.
    genres?: string[]
    label?: string
    release_date?: string
    mb_release_id?: string
  }
): Promise<AlbumMetaResult> => {
  const params = new URLSearchParams({ album_artist: albumArtist, album })
  return patch(`/api/v1/albums/meta?${params}`, opts)
}

// Every distinct genre in the library (KAMP-586), for the edit-panel autocomplete.
export async function getGenres(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/api/v1/genres`, { headers: _authHeaders() })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as string[]
}

// Remove a genre from every tagged track (DB + file tags) and the DB (KAMP-606).
// Query param, not path — genre names can contain '/'.
export const deleteGenre = (name: string): Promise<{ ok: boolean; tracks_updated: number }> =>
  del(`/api/v1/genres?name=${encodeURIComponent(name)}`)

// Merge one genre into another (KAMP-607): retro-retags tracks + persists the
// source->target map. Rejects chains/self-merge (400) and backfill races (409).
export const mergeGenres = (
  source: string,
  target: string
): Promise<{ ok: boolean; tracks_updated: number }> =>
  post('/api/v1/genres/merge', { source, target })

export interface GenreMerge {
  source: string
  target: string
}

export const getGenreMerges = (): Promise<GenreMerge[]> => get('/api/v1/genres/merges')

// Rename a genre everywhere (DB + file tags) — KAMP-608. Renaming onto an
// existing genre folds the two (one-time). Rejects invalid names (400) and
// backfill races (409).
export const renameGenre = (
  old: string,
  next: string
): Promise<{ ok: boolean; tracks_updated: number }> =>
  post('/api/v1/genres/rename', { old, new: next })

// Delete a genre merge rule (KAMP-610) — future-only; already-merged tracks keep
// the target. Query param — names can contain '/'.
export const deleteGenreMerge = (source: string): Promise<{ ok: boolean }> =>
  del(`/api/v1/genres/merge?source=${encodeURIComponent(source)}`)

// Genre allow-list management (KAMP-610).
export interface GenreAllowlist {
  extras: string[]
  defaults: string[]
}

export const getGenreAllowlist = (): Promise<GenreAllowlist> => get('/api/v1/genres/allowlist')

export const addAllowlistEntry = (name: string): Promise<{ ok: boolean; extras: string[] }> =>
  post('/api/v1/genres/allowlist', { name })

export const revertAllowlist = (): Promise<{ ok: boolean }> =>
  post('/api/v1/genres/allowlist/revert')

// ---------------------------------------------------------------------------
// MusicBrainz lookup (KAMP-230, shallow candidates + lazy hydration KAMP-584)
// ---------------------------------------------------------------------------

export type MusicBrainzTrack = {
  track_number: number
  disc_number: number
  title: string
  recording_mbid: string
  // Credited track artist (KAMP-583) — diverges from the album artist on
  // compilations. Empty when MB has no credit for the track.
  artist: string
}

// Shallow search candidate — deliberately has no tracks field, so an
// un-hydrated candidate can never be mistaken for a release with zero
// matching tracks. Hydrate via fetchMusicBrainzRelease before applying.
export type MusicBrainzCandidate = {
  mbid: string
  release_group_mbid: string
  title: string
  album_artist: string
  release_date: string
  label: string
  release_type: string
  is_current: boolean
}

export type MusicBrainzRelease = {
  mbid: string
  release_group_mbid: string
  title: string
  album_artist: string
  release_date: string
  label: string
  release_type: string
  tracks: MusicBrainzTrack[]
}

export async function fetchMusicBrainzCandidates(
  albumArtist: string,
  album: string,
  signal: AbortSignal
): Promise<MusicBrainzCandidate[]> {
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
  const data = (await res.json()) as { candidates: MusicBrainzCandidate[] }
  return data.candidates
}

export async function fetchMusicBrainzRelease(
  mbid: string,
  signal: AbortSignal
): Promise<MusicBrainzRelease> {
  const res = await fetch(
    `${BASE_URL}/api/v1/albums/musicbrainz/release/${encodeURIComponent(mbid)}`,
    { headers: _authHeaders(), signal }
  )
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
  return (await res.json()) as MusicBrainzRelease
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
  // 'failed' is emitted by the queue worker (process_next_download); 'error' by the
  // direct single-album download path. Both signal a failure.
  state: 'queued' | 'downloading' | 'done' | 'error' | 'failed' | 'removed'
  // KAMP-436: byte-progress percent (0–100). Present only on 'downloading'
  // updates when the server knows the total size; absent → keep the pulse.
  progress?: number
}
export type MagicPlaylistUpdatedMessage = {
  type: 'magic_playlist.updated'
  id: number
}
export type PipelineStageMessage = {
  type: 'pipeline.stage'
  stage: string
  // KAMP-562: the album being processed (null for non-download drops). `committed`
  // is true on the terminal empty-stage reset only when the item reached the
  // library, so the UI can tell success (rescan coming) from quarantine.
  sale_item_id?: string | null
  committed?: boolean
  // KAMP-558: human-readable album label for the pipeline indicator tooltip
  // ("" before extraction, and for pre-558 daemons).
  album?: string
}
// KAMP-566/568: full download-queue snapshot for the Downloads view, broadcast
// on every queue transition. `items` is the same shape as GET /api/v1/downloads.
export type DownloadQueueMessage = {
  type: 'download.queue'
  items: DownloadItem[]
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
  | PipelineStageMessage
  | DownloadQueueMessage

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
    state: 'queued' | 'downloading' | 'done' | 'error' | 'failed' | 'removed',
    progress?: number
  ) => void,
  onMagicPlaylistUpdated?: (id: number) => void,
  // KAMP-562: per-album pipeline stage. Named distinctly from the preload's
  // global `onPipelineStage` (which drives the nav-bar indicator).
  onAlbumPipelineStage?: (saleItemId: string | null, stage: string, committed: boolean) => void,
  // KAMP-568: full download-queue snapshot for the Downloads view. Appended last
  // so the existing positional callbacks keep their indices.
  onDownloadQueue?: (items: DownloadItem[]) => void
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
        onAlbumDownload?.(msg.sale_item_id, msg.state, msg.progress)
      else if (msg.type === 'magic_playlist.updated') onMagicPlaylistUpdated?.(msg.id)
      else if (msg.type === 'pipeline.stage')
        onAlbumPipelineStage?.(msg.sale_item_id ?? null, msg.stage, msg.committed ?? false)
      else if (msg.type === 'download.queue') onDownloadQueue?.(msg.items)
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

export const playFiles = (trackIds: number[], startIndex = 0): Promise<void> =>
  // KAMP-538: play-files by canonical track ids (server accepts `ids`, id-preferred).
  post('/api/v1/player/play-files', { ids: trackIds, start_index: startIndex })

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

export const addTrackToPlaylist = (playlistId: number, trackId: number): Promise<void> =>
  // KAMP-538: add by canonical track id (server is id-preferred).
  post(`/api/v1/playlists/${playlistId}/tracks`, { id: trackId })

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
