/**
 * Zustand store.
 *
 * All player and library state lives here. The renderer is a pure view layer:
 * components read from the store and dispatch actions — they hold no state
 * of their own that belongs in the daemon.
 */

import { create } from 'zustand'
import * as api from './api/client'
import type { RepeatMode } from './api/client'
import type {
  Album,
  AlbumTagsCollision,
  ConfigValues,
  CriteriaDoc,
  DownloadItem,
  PlayerState,
  Playlist,
  PlaylistTrack,
  GenreBackfillProgress,
  QueueState,
  ScanProgress,
  ScanResult,
  SearchResult,
  Track
} from './api/client'
import type { DisplayStyle } from './components/modules/registry'
import { themes, applyTheme } from '../../shared/theme'
import type { ThemeName } from '../../shared/theme'
import type { MagicPlaylistContents, MagicPlaylistSort } from './api/client'

export type TrackDisplaySize = 'teeny' | 'less-teeny' | 'large-print'

export type MagicPlaylistModuleConfig = {
  playlistId: number | null
  sort: MagicPlaylistSort
  contents: MagicPlaylistContents
  items: number
}
export type PlasmaMode = 'always' | 'sometimes' | 'never'
export type TraceStyle = 'clean' | 'glowy' | 'trippy'
// The Preferences dialog tabs. Shared by the store and PreferencesDialog so the
// two unions can't drift (they previously disagreed on 'tagging').
export type PrefsTab = 'about' | 'general' | 'tagging' | 'services' | 'extensions'

type LibraryState = {
  albums: Album[]
  artists: string[]
  genres: string[]
  selectedArtist: string | null
  // Active genre filter (KAMP-550). Mutually exclusive with selectedArtist:
  // picking one clears the other.
  selectedGenre: string | null
  selectedAlbum: Album | null
  tracks: Track[]
  tracksAlbumKey: string | null // "artist\0album" key for the loaded track list
  collectionType: 'albums' | 'playlists'
  playlists: Playlist[]
  selectedPlaylist: Playlist | null
  playlistTracks: PlaylistTrack[]
  playlistTracksLoading: boolean
}

type PlayerStore = {
  player: PlayerState
  library: LibraryState
  serverStatus: 'connected' | 'reconnecting' | 'disconnected'
  scanStatus: 'idle' | 'scanning' | 'done' | 'error'
  lastScanResult: ScanResult | null
  scanError: string | null
  scanProgress: ScanProgress | null
  // KAMP-591: library-wide genre backfill progress (null when never started this session).
  genreBackfill: GenreBackfillProgress | null

  leftDb: number | null
  rightDb: number | null
  crestDb: number | null
  peakDb: number | null

  configuredLibraryPath: string | null
  activeView: 'library' | 'now-playing' | 'home' | 'downloads'
  // Last view active before the current one, used so Esc in Downloads can return
  // to where the user came from. In-memory only (not persisted to daemon UI state).
  previousView: 'library' | 'now-playing' | 'home' | 'downloads' | null
  moduleOrder: string[]
  hiddenModules: string[]
  moduleDisplayStyles: Record<string, DisplayStyle>
  lastPlayedCount: number
  lastPlayedDays: number
  lastPlayedVersion: number
  recentlyAddedCount: number
  recentlyAddedDays: number
  highlightEnabled: boolean
  highlightCutoffSecs: number
  highlightStyle: string
  // KAMP-544: ephemeral, non-persisted "played this session" echo — album key ->
  // epoch-seconds captured when playback started. Masks the latency between
  // hitting play and the server's last_played_at syncing back into the album list
  // so the new-arrival sparkle vanishes instantly. Not written to localStorage:
  // on restart last_played_at alone governs, and a later content bump (added_at >
  // this timestamp) re-shows the highlight for free.
  playedHighlights: Map<string, number>
  topAlbumsCount: number
  topTracksCount: number
  topArtistsCount: number
  statsTopTracksCount: number
  favoritePlaylistsCount: number
  favoritePlaylistsSortOrder: 'last_played_at' | 'title'
  magicPlaylistConfigs: Record<string, MagicPlaylistModuleConfig>
  magicPlaylistVersion: number
  flashTrackId: number | null
  baseKampEditMode: boolean
  stereoRackTrackSize: TrackDisplaySize
  stereoRackPlasmaMode: PlasmaMode
  stereoRackTraceStyle: TraceStyle
  albumEditMode: boolean
  albumMetaExpanded: boolean
  albumRenameProgress: { done: number; total: number } | null
  sortOrder:
    | 'album_artist'
    | 'album'
    | 'date_added'
    | 'last_played'
    | 'most_played'
    | 'release_date'
  sortDir: 'asc' | 'desc'
  libraryFilter: string[]
  searchQuery: string
  searchResults: SearchResult | null
  queueVisible: boolean
  collectionPanelVisible: boolean
  collectionPanelSnapshot: boolean | null // saved visibility before entering Now Playing
  queue: QueueState | null
  albumGroupingActive: boolean
  downloadingAlbumIds: Set<string>
  queuedAlbumIds: Set<string>
  // KAMP-436: byte-progress percent (0–100) per downloading album, keyed by
  // sale_item_id. Absent entry → progress unknown → card shows the pulse.
  downloadProgress: Map<string, number>
  // KAMP-562: albums currently in the post-download pipeline (Extracting→Moving),
  // keyed by sale_item_id. Drives the pulsing tag badge until the rescan flips
  // the album to local.
  taggingAlbumIds: Set<string>
  // KAMP-568: full download-queue snapshot (Now Downloading / Queued / Failed) for
  // the Downloads view. Loaded once via GET /api/v1/downloads and kept live by the
  // `download.queue` WS event. Distinct from the per-album KAMP-436 sets above,
  // which decorate the library grid.
  downloadQueue: DownloadItem[]
  // KAMP-571: batch-anchored aggregate for the global download bar. Tracks the
  // ids seen active in the current download batch, the running total, how many
  // have completed (left the queue, not failed), and `floor` — the monotonic
  // displayed percent (0–99), ratcheted on every snapshot and byte-progress tick.
  // Null when the queue is idle (bar hidden). Maintained in setDownloadQueue (done/
  // total) and setAlbumProgress (live floor); the bar just renders `floor`.
  downloadBatch: { seenIds: string[]; total: number; done: number; floor: number } | null
  flashToast: string | null
  // KAMP-571: tone for the current flashToast; 'error' renders the red variant.
  flashToastTone: 'error' | null
  styleRailVisible: boolean
  selectedTheme: ThemeName

  // Actions
  setAudioLevel: (leftDb: number, rightDb: number, crestDb: number, peakDb: number) => void
  setServerStatus: (status: 'connected' | 'reconnecting' | 'disconnected') => void
  toggleQueuePanel: () => void
  toggleCollectionPanel: () => void
  loadQueue: () => Promise<void>
  setSearchQuery: (q: string) => void
  setSortOrder: (
    sort: 'album_artist' | 'album' | 'date_added' | 'last_played' | 'most_played' | 'release_date'
  ) => Promise<void>
  setSortDir: (dir: 'asc' | 'desc') => Promise<void>
  setLibraryFilter: (filters: string[]) => void
  setActiveView: (view: 'library' | 'now-playing' | 'home' | 'downloads') => Promise<void>
  setModuleOrder: (ids: string[]) => void
  hideModule: (id: string) => void
  showModule: (id: string) => void
  setModuleDisplayStyle: (id: string, style: DisplayStyle) => void
  setLastPlayedCount: (n: number) => void
  setLastPlayedDays: (n: number) => void
  bumpLastPlayedVersion: () => void
  markAlbumDownloading: (saleItemId: string) => void
  clearAlbumDownloading: (saleItemId: string) => void
  markAlbumQueued: (saleItemId: string) => void
  clearAlbumQueued: (saleItemId: string) => void
  setAlbumProgress: (saleItemId: string, progress: number) => void
  clearAlbumProgress: (saleItemId: string) => void
  markAlbumTagging: (saleItemId: string) => void
  clearAlbumTagging: (saleItemId: string) => void
  removeDownload: (saleItemId: string) => Promise<void>
  // KAMP-568: download-queue snapshot (Downloads view)
  loadDownloads: () => Promise<void>
  setDownloadQueue: (items: DownloadItem[]) => void
  // KAMP-570: Downloads-view interactions (optimistic; the download.queue WS
  // snapshot reconciles, or loadDownloads() reverts on API failure).
  reorderDownloadQueue: (orderedQueuedIds: string[]) => Promise<void>
  retryDownload: (providerItemId: string) => Promise<void>
  cancelDownload: (providerItemId: string) => Promise<void>
  showFlashToast: (msg: string, tone?: 'error') => void
  setRecentlyAddedCount: (n: number) => void
  setRecentlyAddedDays: (n: number) => void
  setHighlightEnabled: (enabled: boolean) => void
  setHighlightStyle: (style: string) => void
  markHighlightPlayed: (album: Album) => void
  setTopAlbumsCount: (n: number) => void
  setTopTracksCount: (n: number) => void
  setTopArtistsCount: (n: number) => void
  setStatsTopTracksCount: (n: number) => void
  setFavoritePlaylistsCount: (n: number) => void
  setFavoritePlaylistsSortOrder: (order: 'last_played_at' | 'title') => void
  setMagicPlaylistConfig: (moduleId: string, config: MagicPlaylistModuleConfig) => void
  bumpMagicPlaylistVersion: () => void
  setFlashTrackId: (id: number) => void
  insertArtistAt: (artistName: string, idx: number) => Promise<void>
  toggleBaseKampEditMode: () => void
  toggleStyleRail: () => void
  setTheme: (name: ThemeName) => void
  setStereoRackTrackSize: (v: TrackDisplaySize) => void
  setStereoRackPlasmaMode: (v: PlasmaMode) => void
  setStereoRackTraceStyle: (v: TraceStyle) => void
  setAlbumEditMode: (mode: boolean) => void
  setAlbumMetaExpanded: (expanded: boolean) => void
  patchAlbumMeta: (
    albumArtist: string,
    album: string,
    opts: {
      genre?: string
      genres?: string[]
      label?: string
      release_date?: string
      mb_release_id?: string
    }
  ) => Promise<void>
  loadLibrary: () => Promise<void>
  loadUiState: () => Promise<void>
  selectArtist: (artist: string | null) => void
  selectGenre: (genre: string | null) => void
  openGenreFilter: (genre: string) => void
  removeGenre: (name: string) => Promise<void>
  mergeGenre: (source: string, target: string) => Promise<void>
  renameGenre: (oldName: string, newName: string) => Promise<void>
  selectAlbum: (album: Album | null) => Promise<void>
  loadTracks: (albumArtist: string, album: string, trackId?: number | null) => Promise<void>
  setCollectionType: (type: 'albums' | 'playlists') => void
  playPlaylist: (playlistId: number, startIndex?: number) => Promise<void>
  playFiles: (trackIds: number[], startIndex?: number) => Promise<void>
  recordPlaylistPlayed: (playlistId: number) => Promise<void>
  loadPlaylists: () => Promise<void>
  createPlaylist: (title: string) => Promise<Playlist>
  createMagicPlaylist: (title: string, criteria: CriteriaDoc) => Promise<Playlist>
  updateMagicPlaylistCriteria: (id: number, criteria: CriteriaDoc) => Promise<Playlist>
  selectPlaylist: (playlist: Playlist | null) => Promise<void>
  loadPlaylistTracks: (playlistId: number) => Promise<void>
  addTrackToPlaylist: (playlistId: number, trackId: number) => Promise<void>
  addAlbumToPlaylist: (playlistId: number, albumArtist: string, album: string) => Promise<void>
  removeTrackFromPlaylist: (playlistId: number, playlistTrackId: number) => Promise<void>
  reorderPlaylistTracks: (playlistId: number, trackIds: number[]) => Promise<void>
  setPlaylistFavorite: (playlistId: number, favorite: boolean) => Promise<void>
  renamePlaylist: (playlistId: number, title: string) => Promise<void>
  deletePlaylist: (playlistId: number) => Promise<void>
  patchOpenPlaylist: (playlist: Playlist) => void
  playAlbum: (
    albumArtist: string,
    album: string,
    trackIndex?: number,
    trackId?: number | null
  ) => Promise<void>
  playTrack: (
    albumArtist: string,
    album: string,
    trackIndex: number,
    trackId?: number | null
  ) => Promise<void>
  togglePlayPause: () => Promise<void>
  stop: () => Promise<void>
  next: () => Promise<void>
  prev: () => Promise<void>
  seek: (position: number) => Promise<void>
  setVolume: (volume: number) => Promise<void>
  setMuted: (muted: boolean) => Promise<void>
  setAlbumGroupingActive: (active: boolean) => void
  setShuffle: (shuffle: boolean) => Promise<void>
  setRepeat: () => Promise<void>
  addAlbumToQueue: (albumArtist: string, album: string, trackId?: number | null) => Promise<void>
  playAlbumNext: (albumArtist: string, album: string, trackId?: number | null) => Promise<void>
  insertAlbumAt: (
    albumArtist: string,
    album: string,
    index: number,
    trackId?: number | null
  ) => Promise<void>
  addToQueue: (ref: api.TrackRef) => Promise<void>
  insertIntoQueue: (ref: api.TrackRef, index: number) => Promise<void>
  playNext: (ref: api.TrackRef) => Promise<void>
  moveQueueTrack: (fromIndex: number, toIndex: number) => Promise<void>
  reorderQueue: (order: number[]) => Promise<void>
  skipToQueueTrack: (position: number) => Promise<void>
  clearQueue: () => Promise<void>
  clearRemainingQueue: (position: number) => Promise<void>
  removeFromQueue: (indices: number[]) => Promise<void>
  setFavorite: (track: Track, favorite: boolean) => Promise<void>
  setFavorites: (tracks: Track[], favorite: boolean) => Promise<void>
  setAlbumFavorite: (albumArtist: string, album: string, favorite: boolean) => Promise<void>
  patchTrackTitle: (
    trackId: number,
    title: string,
    overwrite?: boolean
  ) => Promise<api.TrackTagsCollision | null>
  patchTrackDisplay: (trackId: number, fields: api.TrackDisplayFields) => Promise<void>
  patchTrackArtist: (trackId: number, artist: string) => Promise<void>
  patchAlbumTags: (
    albumArtist: string,
    album: string,
    opts: { album?: string; album_artist?: string; overwrite?: boolean; skip_conflicts?: boolean }
  ) => Promise<AlbumTagsCollision | null>
  patchAlbumDisplay: (
    albumArtist: string,
    album: string,
    displayAlbum: string | null,
    displayAlbumArtist: string | null
  ) => Promise<void>
  deferredOps: Record<number, number> // track_id → op_id
  clearDeferredOp: (trackId: number) => void
  setAlbumRenameProgress: (progress: { done: number; total: number } | null) => void
  refreshOpenAlbum: () => Promise<void>
  patchOpenAlbum: (album: Album) => void
  scanLibrary: () => Promise<void>
  startGenreBackfill: () => Promise<void>
  cancelGenreBackfill: () => Promise<void>
  refreshGenreBackfill: () => Promise<void>
  setLibraryPath: (path: string) => Promise<void>
  setWatchFolderPath: (path: string) => Promise<void>
  applyServerState: (state: PlayerState) => void

  // Preferences
  configValues: ConfigValues | null
  prefsOpen: boolean
  prefsInitialTab: PrefsTab
  loadConfig: () => Promise<void>
  setConfigValue: (key: string, value: string) => Promise<void>
  openPrefs: (tab?: PrefsTab) => void
  closePrefs: () => void

  // Update notification
  updateAvailable: { version: string; notes: string } | null
  setUpdateAvailable: (data: { version: string; notes: string } | null) => void
}

const initialPlayer: PlayerState = {
  playing: false,
  position: 0,
  duration: 0,
  volume: 100,
  muted: false,
  current_track: null,
  next_track: null,
  buffering: false
}

// Cache key for an album's track list. A missing-album card (track_id set) keys
// on its unique track id so two no-album tracks with the same title don't collide;
// a real album keys on (album_artist, album). (KAMP-554)
const _albumTracksKey = (albumArtist: string, album: string, trackId: number | null): string =>
  trackId != null ? `id:${trackId}` : `${albumArtist}\0${album}`

// KAMP-571: raw aggregate percent for the global download bar — completed items
// plus the currently-downloading item's byte-fraction, over the batch total.
// downloadProgress is keyed by sale_item_id (== provider_item_id for bandcamp);
// `?? 0` guards a downloading item with no progress entry yet (avoids NaN).
const _downloadBarRaw = (
  items: DownloadItem[],
  done: number,
  total: number,
  progress: Map<string, number>
): number => {
  if (total <= 0) return 0
  let inFlight = 0
  for (const i of items) {
    if (i.status === 'downloading') inFlight += (progress.get(i.provider_item_id) ?? 0) / 100
  }
  return ((done + inFlight) / total) * 100
}

// Ratchet the displayed floor up toward `raw`, capped at 99 so the bar never sits
// at a full-width "stall" during the last item's post-download tag/rescan tail (it
// hides when the batch drains instead). The floor keeps the fill monotonic — a
// mid-batch append grows `total` and would otherwise rewind it.
const _ratchetFloor = (prevFloor: number, raw: number): number =>
  Math.min(99, Math.max(prevFloor, raw))

export const useStore = create<PlayerStore>((set, get) => ({
  player: initialPlayer,
  library: {
    albums: [],
    artists: [],
    genres: [],
    selectedArtist: null,
    selectedGenre: null,
    selectedAlbum: null,
    tracks: [],
    tracksAlbumKey: null,
    collectionType: 'albums',
    playlists: [],
    selectedPlaylist: null,
    playlistTracks: [],
    playlistTracksLoading: false
  },
  serverStatus: 'reconnecting',
  scanStatus: 'idle',
  lastScanResult: null,
  scanError: null,
  scanProgress: null,
  genreBackfill: null,
  leftDb: null,
  rightDb: null,
  crestDb: null,
  peakDb: null,
  configuredLibraryPath: null,
  activeView: 'library',
  previousView: null,
  moduleOrder: (() => {
    const saved = localStorage.getItem('kamp:module-order')
    return saved
      ? (JSON.parse(saved) as string[])
      : ['kamp.stereo-rack', 'kamp.new-arrivals', 'kamp.last-played']
  })(),
  hiddenModules: (() => {
    const saved = localStorage.getItem('kamp:hidden-modules')
    return saved ? (JSON.parse(saved) as string[]) : []
  })(),
  moduleDisplayStyles: (() => {
    const saved = localStorage.getItem('kamp:module-display-styles')
    return saved ? (JSON.parse(saved) as Record<string, DisplayStyle>) : {}
  })(),
  lastPlayedCount: (() => {
    const saved = localStorage.getItem('kamp:last-played-count')
    return saved ? parseInt(saved) : 10
  })(),
  lastPlayedDays: (() => {
    const saved = localStorage.getItem('kamp:last-played-days')
    return saved ? parseInt(saved) : 30
  })(),
  lastPlayedVersion: 0,
  recentlyAddedCount: (() => {
    const saved = localStorage.getItem('kamp:recently-added-count')
    return saved ? parseInt(saved) : 10
  })(),
  recentlyAddedDays: (() => {
    const saved = localStorage.getItem('kamp:recently-added-days')
    return saved ? parseInt(saved) : 30
  })(),
  highlightEnabled: localStorage.getItem('kamp:highlight-enabled') !== 'false',
  highlightCutoffSecs: Date.now() / 1000 - 5 * 86400,
  highlightStyle: localStorage.getItem('kamp:highlight-style') ?? 'shiny',
  playedHighlights: new Map<string, number>(),
  topAlbumsCount: (() => {
    const saved = localStorage.getItem('kamp:top-albums-count')
    return saved ? parseInt(saved) : 10
  })(),
  topTracksCount: (() => {
    const saved = localStorage.getItem('kamp:top-tracks-count')
    return saved ? parseInt(saved) : 10
  })(),
  topArtistsCount: (() => {
    const saved = localStorage.getItem('kamp:top-artists-count')
    return saved ? parseInt(saved) : 10
  })(),
  statsTopTracksCount: (() => {
    const saved = localStorage.getItem('kamp:stats-top-tracks-count')
    return saved ? parseInt(saved) : 3
  })(),
  favoritePlaylistsCount: (() => {
    const saved = localStorage.getItem('kamp:fav-playlists-count')
    return saved ? parseInt(saved) : 10
  })(),
  favoritePlaylistsSortOrder:
    (localStorage.getItem('kamp:fav-playlists-sort') as 'last_played_at' | 'title' | null) ??
    'last_played_at',
  magicPlaylistConfigs: (() => {
    try {
      const saved = localStorage.getItem('kamp:magic-playlist-configs')
      return saved ? (JSON.parse(saved) as Record<string, MagicPlaylistModuleConfig>) : {}
    } catch {
      return {}
    }
  })(),
  magicPlaylistVersion: 0,
  flashTrackId: null,
  baseKampEditMode: false,
  stereoRackTrackSize:
    (localStorage.getItem('stereo-rack:track-size') as TrackDisplaySize) ?? 'teeny',
  stereoRackPlasmaMode:
    (localStorage.getItem('stereo-rack:plasma-mode') as PlasmaMode) ?? 'sometimes',
  stereoRackTraceStyle: (localStorage.getItem('stereo-rack:trace-style') as TraceStyle) ?? 'glowy',
  albumEditMode: false,
  albumMetaExpanded: localStorage.getItem('kamp:meta-expanded') === 'true',
  albumRenameProgress: null,
  sortOrder: 'album_artist',
  sortDir: 'asc',
  libraryFilter: [],
  searchQuery: '',
  searchResults: null,
  queueVisible: false,
  // Client-only persistence — no backend endpoint needed for this toggle.
  collectionPanelVisible: localStorage.getItem('kamp:collection-panel-visible') !== 'false',
  collectionPanelSnapshot: null,
  queue: null,
  albumGroupingActive: localStorage.getItem('kamp:album-view') === 'true',
  downloadingAlbumIds: new Set<string>(),
  queuedAlbumIds: new Set<string>(),
  downloadProgress: new Map<string, number>(),
  taggingAlbumIds: new Set<string>(),
  downloadQueue: [],
  downloadBatch: null,
  flashToast: null,
  flashToastTone: null,
  styleRailVisible: false,
  selectedTheme: (localStorage.getItem('kamp:selected-theme') as ThemeName | null) ?? 'kamp',
  configValues: null,
  prefsOpen: false,
  prefsInitialTab: 'about',
  updateAvailable: null,
  deferredOps: {},

  setAudioLevel: (leftDb, rightDb, crestDb, peakDb) => set({ leftDb, rightDb, crestDb, peakDb }),

  setServerStatus: (status) => set({ serverStatus: status }),

  toggleQueuePanel: () => {
    const next = !get().queueVisible
    set({ queueVisible: next })
    void api.setQueuePanelApi(next)
  },

  toggleCollectionPanel: () => {
    const next = !get().collectionPanelVisible
    set({ collectionPanelVisible: next })
    localStorage.setItem('kamp:collection-panel-visible', String(next))
  },

  loadQueue: async () => {
    try {
      const queue = await api.getQueue()
      set({ queue })
    } catch {
      // Best-effort — stale or empty queue is fine.
    }
  },

  setSortOrder: async (sort) => {
    // Reset direction to the natural default for the new sort key so the
    // results make intuitive sense (e.g. "Date Added" → newest first).
    const naturalDir: 'asc' | 'desc' = [
      'date_added',
      'last_played',
      'most_played',
      'release_date'
    ].includes(sort)
      ? 'desc'
      : 'asc'
    set({ sortOrder: sort, sortDir: naturalDir })
    await get().loadLibrary()
    const q = get().searchQuery
    if (q.trim()) await get().setSearchQuery(q)
    try {
      await api.setSortOrderApi(sort, naturalDir)
    } catch {
      // Best-effort — preference is already applied locally.
    }
  },

  setSortDir: async (dir) => {
    set({ sortDir: dir })
    await get().loadLibrary()
    try {
      await api.setSortOrderApi(get().sortOrder, dir)
    } catch {
      // Best-effort.
    }
  },

  setLibraryFilter: (filters) => {
    set({ libraryFilter: filters })
  },

  setSearchQuery: async (q) => {
    set({ searchQuery: q })
    if (!q.trim()) {
      set({ searchResults: null })
      return
    }
    try {
      const results = await api.search(q, get().sortOrder)
      // Only apply if the query hasn't changed since we fired the request.
      if (get().searchQuery === q) {
        set({ searchResults: results })
      }
    } catch {
      // Ignore transient errors — stale results are better than a broken UI.
    }
  },

  setActiveView: async (view) => {
    const { collectionPanelVisible, collectionPanelSnapshot, activeView } = get()
    // Remember where we came from so Esc in Downloads can return there.
    // Only advance on an actual change, so a no-op re-selection doesn't lose it.
    if (view !== activeView) set({ previousView: activeView })
    if (view === 'library') {
      // Restore the collection panel to its pre-non-library state.
      const restored = collectionPanelSnapshot ?? collectionPanelVisible
      set({ activeView: view, collectionPanelSnapshot: null, collectionPanelVisible: restored })
      localStorage.setItem('kamp:collection-panel-visible', String(restored))
    } else {
      // Hide the collection panel (only relevant in Library) on any non-library view.
      // Preserve any existing snapshot so round-trips through multiple non-library views
      // (e.g. now-playing → home → library) still restore the original user preference.
      // Persist false so a restart outside the library doesn't reopen the panel.
      const snapshot = collectionPanelSnapshot ?? collectionPanelVisible
      set({ activeView: view, collectionPanelSnapshot: snapshot, collectionPanelVisible: false })
      localStorage.setItem('kamp:collection-panel-visible', 'false')
    }
    try {
      await api.setActiveViewApi(view)
    } catch {
      // Best-effort — view is already updated locally; daemon will sync on next connect.
    }
  },

  setModuleOrder: (ids) => {
    localStorage.setItem('kamp:module-order', JSON.stringify(ids))
    set({ moduleOrder: ids })
  },

  hideModule: (id) => {
    const next = [...get().hiddenModules, id]
    localStorage.setItem('kamp:hidden-modules', JSON.stringify(next))
    set({ hiddenModules: next })
  },

  showModule: (id) => {
    const { hiddenModules, moduleOrder } = get()
    const nextHidden = hiddenModules.filter((h) => h !== id)
    // Always place the re-added module at the bottom
    const nextOrder = [...moduleOrder.filter((oid) => oid !== id), id]
    localStorage.setItem('kamp:hidden-modules', JSON.stringify(nextHidden))
    localStorage.setItem('kamp:module-order', JSON.stringify(nextOrder))
    set({ hiddenModules: nextHidden, moduleOrder: nextOrder })
  },

  setModuleDisplayStyle: (id, style) => {
    const next = { ...get().moduleDisplayStyles, [id]: style }
    localStorage.setItem('kamp:module-display-styles', JSON.stringify(next))
    set({ moduleDisplayStyles: next })
  },

  setLastPlayedCount: (n) => {
    localStorage.setItem('kamp:last-played-count', String(n))
    set({ lastPlayedCount: n })
  },

  setLastPlayedDays: (n) => {
    localStorage.setItem('kamp:last-played-days', String(n))
    set({ lastPlayedDays: n })
  },

  bumpLastPlayedVersion: () => set((s) => ({ lastPlayedVersion: s.lastPlayedVersion + 1 })),
  markAlbumDownloading: (saleItemId) =>
    set((s) => ({ downloadingAlbumIds: new Set([...s.downloadingAlbumIds, saleItemId]) })),
  clearAlbumDownloading: (saleItemId) =>
    set((s) => {
      const next = new Set(s.downloadingAlbumIds)
      next.delete(saleItemId)
      // KAMP-436: intentionally do NOT drop downloadProgress here. The reveal
      // must stay at 100% through the post-download tag/rescan window (while the
      // card is still blurred) so it doesn't flash back to full blur. The card
      // clears the entry itself via clearAlbumProgress once its blur resolves.
      return { downloadingAlbumIds: next }
    }),
  setAlbumProgress: (saleItemId, progress) =>
    set((s) => {
      const next = new Map(s.downloadProgress)
      next.set(saleItemId, progress)
      // KAMP-571: ratchet the global download bar's floor with the new byte tick.
      const b = s.downloadBatch
      if (b == null) return { downloadProgress: next }
      const floor = _ratchetFloor(b.floor, _downloadBarRaw(s.downloadQueue, b.done, b.total, next))
      return {
        downloadProgress: next,
        downloadBatch: floor === b.floor ? b : { ...b, floor }
      }
    }),
  clearAlbumProgress: (saleItemId) =>
    set((s) => {
      // No-op (return the same state ref) when absent, so repeated calls from a
      // card effect don't churn a new Map / re-render.
      if (!s.downloadProgress.has(saleItemId)) return s
      const next = new Map(s.downloadProgress)
      next.delete(saleItemId)
      return { downloadProgress: next }
    }),
  markAlbumQueued: (saleItemId) =>
    set((s) => ({ queuedAlbumIds: new Set([...s.queuedAlbumIds, saleItemId]) })),
  clearAlbumQueued: (saleItemId) =>
    set((s) => {
      const next = new Set(s.queuedAlbumIds)
      next.delete(saleItemId)
      return { queuedAlbumIds: next }
    }),
  markAlbumTagging: (saleItemId) =>
    set((s) => ({ taggingAlbumIds: new Set([...s.taggingAlbumIds, saleItemId]) })),
  clearAlbumTagging: (saleItemId) =>
    set((s) => {
      // No-op (same state ref) when absent so the card's !isRemote cleanup effect
      // doesn't churn a new Set / re-render (KAMP-562).
      if (!s.taggingAlbumIds.has(saleItemId)) return s
      const next = new Set(s.taggingAlbumIds)
      next.delete(saleItemId)
      return { taggingAlbumIds: next }
    }),
  removeDownload: async (saleItemId) => {
    try {
      await api.removeDownload(saleItemId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not remove download'
      get().showFlashToast(msg)
    }
  },
  // KAMP-568: seed the Downloads view from the REST snapshot; live updates then
  // arrive via the `download.queue` WS event (setDownloadQueue).
  loadDownloads: async () => {
    try {
      const { items } = await api.getDownloads()
      get().setDownloadQueue(items) // route through the batch-tracking setter
    } catch {
      // best-effort; the WS snapshot will populate on the next transition
    }
  },
  setDownloadQueue: (items) =>
    set((s) => {
      // KAMP-571: fold the snapshot into the batch-anchored aggregate. A batch is
      // "everything in flight until the queue drains". When nothing is active the
      // batch resets (bar hides). Otherwise every newly-seen active id extends the
      // total; `done` = seen ids that have left the queue without failing.
      const active = items.filter((i) => i.status === 'downloading' || i.status === 'queued')
      if (active.length === 0) return { downloadQueue: items, downloadBatch: null }

      const prev = s.downloadBatch
      const seenIds = prev ? prev.seenIds.slice() : []
      const seen = new Set(seenIds)
      for (const i of active) {
        if (!seen.has(i.provider_item_id)) {
          seen.add(i.provider_item_id)
          seenIds.push(i.provider_item_id)
        }
      }
      const activeSet = new Set(active.map((i) => i.provider_item_id))
      const failedSet = new Set(
        items.filter((i) => i.status === 'failed').map((i) => i.provider_item_id)
      )
      // A completed item has left the queue: seen, but no longer active and not
      // failed. (Snapshot-only counting can't tell a user-cancelled item from a
      // completed one, so a mid-batch cancel nudges `done` forward by one — never
      // backward, and cancelling the last active item empties the batch entirely.)
      let done = 0
      for (const id of seenIds) if (!activeSet.has(id) && !failedSet.has(id)) done++
      const total = seenIds.length

      const raw = _downloadBarRaw(items, done, total, s.downloadProgress)
      const floor = _ratchetFloor(prev?.floor ?? 0, raw)

      // Reuse the prior object when nothing changed to avoid a re-render per
      // snapshot (seenIds only grows, so a length match means an id match).
      const batch =
        prev &&
        prev.total === total &&
        prev.done === done &&
        prev.floor === floor &&
        prev.seenIds.length === seenIds.length
          ? prev
          : { seenIds, total, done, floor }
      return { downloadQueue: items, downloadBatch: batch }
    }),
  // KAMP-570: reorder the queued items. Optimistically rebuild the queue as
  // [downloading, ...reordered queued, failed] (the snapshot's section order),
  // then persist. The download.queue WS snapshot reconciles on success; a failed
  // call reverts via loadDownloads(). Only 'queued' items are reorderable.
  reorderDownloadQueue: async (orderedQueuedIds) => {
    set((s) => {
      const byId = new Map(s.downloadQueue.map((i) => [i.provider_item_id, i]))
      const downloading = s.downloadQueue.filter((i) => i.status === 'downloading')
      const failed = s.downloadQueue.filter((i) => i.status === 'failed')
      const queued = orderedQueuedIds
        .map((id) => byId.get(id))
        .filter((i): i is DownloadItem => i != null)
      return { downloadQueue: [...downloading, ...queued, ...failed] }
    })
    try {
      await api.reorderDownloads(orderedQueuedIds)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not reorder downloads'
      get().showFlashToast(msg)
      void get().loadDownloads() // revert to authoritative state
    }
  },
  // KAMP-570: retry a failed download — re-queue at the end. Optimistically flip
  // the item to 'queued' in place (it sits after the other queued items in the
  // array, so it renders at the end of the Queued section).
  retryDownload: async (providerItemId) => {
    set((s) => ({
      downloadQueue: s.downloadQueue.map((i) =>
        i.provider_item_id === providerItemId
          ? { ...i, status: 'queued' as const, error_text: null }
          : i
      )
    }))
    try {
      await api.retryDownload(providerItemId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not retry download'
      get().showFlashToast(msg)
      void get().loadDownloads()
    }
  },
  // KAMP-570: cancel a queued/failed item — remove it from the queue. Distinct
  // from removeDownload (which reverts a completed download to streaming).
  cancelDownload: async (providerItemId) => {
    set((s) => ({
      downloadQueue: s.downloadQueue.filter((i) => i.provider_item_id !== providerItemId)
    }))
    try {
      await api.cancelDownload(providerItemId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not cancel download'
      get().showFlashToast(msg)
      void get().loadDownloads()
    }
  },
  showFlashToast: (msg, tone) => {
    // KAMP-571: clear both message and tone on expiry, or the next neutral toast
    // would inherit a stale 'error' tone until its own re-render.
    set({ flashToast: msg, flashToastTone: tone ?? null })
    setTimeout(() => set({ flashToast: null, flashToastTone: null }), 5000)
  },

  setRecentlyAddedCount: (n) => {
    localStorage.setItem('kamp:recently-added-count', String(n))
    set({ recentlyAddedCount: n })
  },

  setRecentlyAddedDays: (n) => {
    localStorage.setItem('kamp:recently-added-days', String(n))
    set({ recentlyAddedDays: n })
  },

  setHighlightEnabled: (enabled) => {
    localStorage.setItem('kamp:highlight-enabled', String(enabled))
    set({ highlightEnabled: enabled })
  },

  setHighlightStyle: (style) => {
    localStorage.setItem('kamp:highlight-style', style)
    set({ highlightStyle: style })
  },

  markHighlightPlayed: (album) => {
    // KAMP-544: record the moment this album started playing so its new-arrival
    // sparkle clears immediately (before last_played_at round-trips from the
    // server). Ephemeral — never persisted. A later sync that adds a track bumps
    // added_at past this timestamp, which re-shows the highlight.
    const key = album.missing_album
      ? String(album.track_id ?? '')
      : `${album.album_artist}::${album.album}`
    const nowSecs = Date.now() / 1000
    const current = get().playedHighlights
    if (current.get(key) === nowSecs) return
    const next = new Map(current)
    next.set(key, nowSecs)
    set({ playedHighlights: next })
  },

  setTopAlbumsCount: (n) => {
    localStorage.setItem('kamp:top-albums-count', String(n))
    set({ topAlbumsCount: n })
  },

  setTopTracksCount: (n) => {
    localStorage.setItem('kamp:top-tracks-count', String(n))
    set({ topTracksCount: n })
  },

  setTopArtistsCount: (n) => {
    localStorage.setItem('kamp:top-artists-count', String(n))
    set({ topArtistsCount: n })
  },

  setStatsTopTracksCount: (n) => {
    localStorage.setItem('kamp:stats-top-tracks-count', String(n))
    set({ statsTopTracksCount: n })
  },

  setFavoritePlaylistsCount: (n) => {
    localStorage.setItem('kamp:fav-playlists-count', String(n))
    set({ favoritePlaylistsCount: n })
  },

  setFavoritePlaylistsSortOrder: (order) => {
    localStorage.setItem('kamp:fav-playlists-sort', order)
    set({ favoritePlaylistsSortOrder: order })
  },

  setMagicPlaylistConfig: (moduleId, config) => {
    const next = { ...get().magicPlaylistConfigs, [moduleId]: config }
    localStorage.setItem('kamp:magic-playlist-configs', JSON.stringify(next))
    set({ magicPlaylistConfigs: next })
  },

  bumpMagicPlaylistVersion: () =>
    set((s) => ({ magicPlaylistVersion: s.magicPlaylistVersion + 1 })),

  insertArtistAt: async (artistName, idx) => {
    const albums = get()
      .library.albums.filter((a) => a.album_artist === artistName)
      .sort((a, b) => (b.play_count_avg ?? 0) - (a.play_count_avg ?? 0))
    let offset = 0
    for (const album of albums) {
      await api.insertAlbumAt(album.album_artist, album.album, idx + offset, album.track_id)
      offset += album.track_count
    }
    void get().loadQueue()
  },

  setFlashTrackId: (id) => {
    set({ flashTrackId: id })
    setTimeout(() => set({ flashTrackId: null }), 2100)
  },

  toggleBaseKampEditMode: () => {
    set({ baseKampEditMode: !get().baseKampEditMode })
  },

  toggleStyleRail: () => {
    set({ styleRailVisible: !get().styleRailVisible })
  },

  setTheme: (name) => {
    localStorage.setItem('kamp:selected-theme', name)
    applyTheme(name, document.documentElement)
    window.api.setBgColor(themes[name].bg)
    set({ selectedTheme: name })
  },

  setStereoRackTrackSize: (v) => {
    localStorage.setItem('stereo-rack:track-size', v)
    set({ stereoRackTrackSize: v })
  },

  setStereoRackPlasmaMode: (v) => {
    localStorage.setItem('stereo-rack:plasma-mode', v)
    set({ stereoRackPlasmaMode: v })
  },

  setStereoRackTraceStyle: (v) => {
    localStorage.setItem('stereo-rack:trace-style', v)
    set({ stereoRackTraceStyle: v })
  },

  setAlbumEditMode: (mode) => {
    // Auto-expand the liner notes panel when entering edit mode so the
    // editable fields are always visible without a separate interaction.
    if (mode && !get().albumMetaExpanded) {
      localStorage.setItem('kamp:meta-expanded', 'true')
      set({ albumEditMode: mode, albumMetaExpanded: true })
    } else {
      set({ albumEditMode: mode })
    }
  },

  setAlbumMetaExpanded: (expanded) => {
    localStorage.setItem('kamp:meta-expanded', String(expanded))
    set({ albumMetaExpanded: expanded })
  },

  loadUiState: async () => {
    try {
      const ui = await api.getUiState()
      set({
        activeView: ui.active_view,
        sortOrder: ui.sort_order,
        sortDir: ui.sort_dir ?? 'asc',
        queueVisible: ui.queue_panel_open
      })
    } catch {
      // Server unreachable — keep default.
    }
  },

  applyServerState: (state) => {
    const prevTrack = get().player.current_track
    set({ player: state })
    // When the track changes (e.g. auto-advance at end-of-track), the queue
    // position has moved server-side — reload so the panel stays in sync.
    if (state.current_track?.id !== prevTrack?.id) {
      void get().loadQueue()
    }
  },

  loadLibrary: async () => {
    try {
      const sort = get().sortOrder
      const dir = get().sortDir
      const [albums, artists, genres, playlists] = await Promise.all([
        api.getAlbums(sort, dir),
        api.getArtists(),
        api.getGenres(),
        api.getPlaylists()
      ])
      set((s) => {
        // Refresh selectedAlbum so metadata changes (source, sale_item_id, etc.)
        // are visible immediately without needing to navigate away and back.
        const { selectedAlbum } = s.library
        const refreshedSelectedAlbum = selectedAlbum
          ? (albums.find(
              (a) =>
                a.album_artist === selectedAlbum.album_artist &&
                a.album === selectedAlbum.album &&
                a.track_id === selectedAlbum.track_id
            ) ?? selectedAlbum)
          : null
        return {
          library: {
            ...s.library,
            albums,
            artists,
            genres,
            playlists,
            selectedAlbum: refreshedSelectedAlbum
          },
          serverStatus: 'connected'
        }
      })
    } catch {
      // During initial startup or mid-session reconnect the server may not be
      // ready yet — the WebSocket retry loop owns recovery. Only signal
      // 'disconnected' if we were actually connected when the fetch failed.
      if (get().serverStatus !== 'reconnecting') {
        set({ serverStatus: 'disconnected' })
      }
    }
  },

  refreshOpenAlbum: async () => {
    // Force-reload the track list for the currently open album, bypassing the
    // key guard in loadTracks. Called after background scans so additions and
    // deletions are reflected immediately without the user having to navigate away.
    const { selectedAlbum } = get().library
    if (!selectedAlbum) return
    try {
      const tracks = await api.getTracksForAlbum(
        selectedAlbum.album_artist,
        selectedAlbum.album,
        selectedAlbum.track_id
      )
      const key = _albumTracksKey(
        selectedAlbum.album_artist,
        selectedAlbum.album,
        selectedAlbum.track_id
      )
      set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
    } catch {
      // Best-effort — stale track list is better than a broken UI.
    }
  },

  patchOpenAlbum: (album) => set((s) => ({ library: { ...s.library, selectedAlbum: album } })),

  // Artist and genre filters are mutually exclusive (KAMP-550): selecting one
  // clears the other so only one filter is ever active.
  selectArtist: (artist) =>
    set((s) => ({
      library: { ...s.library, selectedArtist: artist, selectedGenre: null, selectedAlbum: null }
    })),

  selectGenre: (genre) =>
    set((s) => ({
      library: { ...s.library, selectedGenre: genre, selectedArtist: null, selectedAlbum: null }
    })),

  // KAMP-611: navigate to a genre's filter from a genre pill. Applies the filter
  // (which clears the open album back to the grid) and opens the Collection panel
  // if it's collapsed so the active filter is visible. CollectionPanel flips to
  // the Genres tab off `selectedGenre`.
  openGenreFilter: (genre) => {
    get().selectGenre(genre)
    if (!get().collectionPanelVisible) get().toggleCollectionPanel()
  },

  // KAMP-606: remove a genre from every tagged track (DB + file tags) and the
  // DB. Clears the active filter if it was this genre, then refreshes the
  // library (genre sidebar + album grid) and any open album's chips.
  removeGenre: async (name) => {
    await api.deleteGenre(name)
    if (get().library.selectedGenre === name) get().selectGenre(null)
    await get().loadLibrary()
    await get().refreshOpenAlbum()
  },

  // KAMP-607: merge source into target. The source disappears; the active filter
  // follows it to the target if it was selected. Refresh library + open album.
  mergeGenre: async (source, target) => {
    await api.mergeGenres(source, target)
    if (get().library.selectedGenre === source) get().selectGenre(target)
    await get().loadLibrary()
    await get().refreshOpenAlbum()
  },

  // KAMP-608: rename a genre everywhere. The active filter follows the rename.
  renameGenre: async (oldName, newName) => {
    await api.renameGenre(oldName, newName)
    if (get().library.selectedGenre === oldName) get().selectGenre(newName)
    await get().loadLibrary()
    await get().refreshOpenAlbum()
  },

  selectAlbum: async (album) => {
    set((s) => ({
      library: { ...s.library, selectedAlbum: album, collectionType: 'albums' },
      albumEditMode: false
    }))
    if (album) await get().loadTracks(album.album_artist, album.album, album.track_id)
  },

  loadTracks: async (albumArtist, album, trackId = null) => {
    // For a missing-album card, track_id is the unique key so that two no-album
    // tracks with the same title don't share a cache entry.
    const key = _albumTracksKey(albumArtist, album, trackId)
    if (get().library.tracksAlbumKey === key) return
    const tracks = await api.getTracksForAlbum(albumArtist, album, trackId)
    set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
  },

  setCollectionType: (type) => set((s) => ({ library: { ...s.library, collectionType: type } })),

  playPlaylist: async (playlistId, startIndex = 0) => {
    await api.playPlaylist(playlistId, startIndex)
    void get().loadQueue()
    void get().loadPlaylists()
  },

  playFiles: async (trackIds, startIndex = 0) => {
    await api.playFiles(trackIds, startIndex)
    void get().loadQueue()
  },

  recordPlaylistPlayed: async (playlistId) => {
    await api.recordPlaylistPlayed(playlistId)
    void get().loadPlaylists()
  },

  loadPlaylists: async () => {
    const playlists = await api.getPlaylists()
    set((s) => ({ library: { ...s.library, playlists } }))
  },

  createPlaylist: async (title) => {
    const playlist = await api.createPlaylist(title)
    await get()
      .loadPlaylists()
      .catch(() => undefined)
    return playlist
  },

  createMagicPlaylist: async (title, criteria) => {
    const playlist = await api.createMagicPlaylist(title, criteria)
    await get()
      .loadPlaylists()
      .catch(() => undefined)
    return playlist
  },

  updateMagicPlaylistCriteria: async (id, criteria) => {
    const playlist = await api.updateMagicPlaylistCriteria(id, criteria)
    await get()
      .loadPlaylists()
      .catch(() => undefined)
    if (get().library.selectedPlaylist?.id === id) {
      const fresh = get().library.playlists.find((p) => p.id === id) ?? null
      set((s) => ({ library: { ...s.library, selectedPlaylist: fresh } }))
      void get().loadPlaylistTracks(id)
    }
    return playlist
  },

  selectPlaylist: async (playlist) => {
    set((s) => ({
      library: {
        ...s.library,
        selectedPlaylist: playlist,
        playlistTracks: [],
        playlistTracksLoading: !!playlist
      }
    }))
    if (playlist) await get().loadPlaylistTracks(playlist.id)
  },

  loadPlaylistTracks: async (playlistId) => {
    const playlistTracks = await api.getPlaylistTracks(playlistId)
    set((s) => ({
      library: {
        ...s.library,
        playlistTracks,
        playlistTracksLoading: false,
        // Keep the playlist card count in sync with the just-evaluated result
        playlists: s.library.playlists.map((p) =>
          p.id === playlistId && p.criteria !== null
            ? { ...p, track_count: playlistTracks.length }
            : p
        )
      }
    }))
  },

  addTrackToPlaylist: async (playlistId, trackId) => {
    await api.addTrackToPlaylist(playlistId, trackId)
    if (get().library.selectedPlaylist?.id === playlistId) {
      await get().loadPlaylistTracks(playlistId)
    }
    await get()
      .loadPlaylists()
      .catch(() => undefined)
  },

  addAlbumToPlaylist: async (playlistId, albumArtist, album) => {
    await api.addAlbumToPlaylist(playlistId, albumArtist, album)
    if (get().library.selectedPlaylist?.id === playlistId) {
      await get().loadPlaylistTracks(playlistId)
    }
    await get()
      .loadPlaylists()
      .catch(() => undefined)
  },

  removeTrackFromPlaylist: async (playlistId, playlistTrackId) => {
    await api.removeTrackFromPlaylist(playlistId, playlistTrackId)
    if (get().library.selectedPlaylist?.id === playlistId) {
      await get().loadPlaylistTracks(playlistId)
    }
    await get()
      .loadPlaylists()
      .catch(() => undefined)
  },

  reorderPlaylistTracks: async (playlistId, trackIds) => {
    // Optimistic update: reorder in-place before awaiting the API call.
    set((s) => {
      const { playlistTracks } = s.library
      const byId = new Map(playlistTracks.map((t) => [t.playlist_track_id, t]))
      const reordered = trackIds
        .map((id, pos) => {
          const t = byId.get(id)
          return t ? { ...t, position: pos } : null
        })
        .filter((t): t is PlaylistTrack => t !== null)
      return { library: { ...s.library, playlistTracks: reordered } }
    })
    await api.reorderPlaylistTracks(playlistId, trackIds)
  },

  setPlaylistFavorite: async (playlistId, favorite) => {
    await api.patchPlaylist(playlistId, { favorite })
    await get().loadPlaylists()
    if (get().library.selectedPlaylist?.id === playlistId) {
      const fresh = get().library.playlists.find((p) => p.id === playlistId) ?? null
      set((s) => ({ library: { ...s.library, selectedPlaylist: fresh } }))
    }
  },

  renamePlaylist: async (playlistId, title) => {
    await api.patchPlaylist(playlistId, { title })
    await get().loadPlaylists()
    if (get().library.selectedPlaylist?.id === playlistId) {
      // Replace selectedPlaylist from the freshly loaded list so updated_at
      // is current — the art URL includes updated_at for cache-busting.
      const fresh = get().library.playlists.find((p) => p.id === playlistId) ?? null
      set((s) => ({ library: { ...s.library, selectedPlaylist: fresh } }))
    }
  },

  deletePlaylist: async (playlistId) => {
    await api.deletePlaylist(playlistId)
    if (get().library.selectedPlaylist?.id === playlistId) {
      set((s) => ({
        library: { ...s.library, selectedPlaylist: null, playlistTracks: [] }
      }))
    }
    await get().loadPlaylists()
  },

  patchOpenPlaylist: (playlist) =>
    set((s) => ({
      library: {
        ...s.library,
        selectedPlaylist: playlist,
        playlists: s.library.playlists.map((p) => (p.id === playlist.id ? playlist : p))
      }
    })),

  playAlbum: async (albumArtist, album, trackIndex = 0, trackId = null) => {
    await api.playAlbum(albumArtist, album, trackIndex, trackId)
    void get().loadQueue()
  },

  playTrack: async (albumArtist, album, trackIndex, trackId = null) => {
    await api.playAlbum(albumArtist, album, trackIndex, trackId)
    void get().loadQueue()
  },

  togglePlayPause: async () => {
    const { playing } = get().player
    if (playing) {
      await api.pause()
    } else {
      await api.resume()
    }
  },

  stop: async () => {
    await api.stop()
  },

  next: async () => {
    await api.nextTrack()
    void get().loadQueue()
  },

  prev: async () => {
    await api.prevTrack()
    void get().loadQueue()
  },

  seek: async (position) => {
    await api.seek(position)
  },

  setVolume: async (volume) => {
    await api.setVolume(volume)
    // The daemon clears mute on any explicit volume change (drag-to-unmute,
    // KAMP-559); mirror that optimistically so the icon updates immediately.
    set((s) => ({ player: { ...s.player, volume, muted: false } }))
  },

  setMuted: async (muted) => {
    await api.setMuted(muted)
    set((s) => ({ player: { ...s.player, muted } }))
  },

  setAlbumGroupingActive: (active) => {
    localStorage.setItem('kamp:album-view', String(active))
    set({ albumGroupingActive: active })
  },

  setShuffle: async (shuffle) => {
    await api.setShuffle(shuffle, get().albumGroupingActive)
    void get().loadQueue()
  },

  setRepeat: async () => {
    const { queue, albumGroupingActive } = get()
    const current = (queue?.repeat ?? 'off') as RepeatMode
    const modes: RepeatMode[] = albumGroupingActive
      ? ['off', 'queue', 'album', 'single']
      : ['off', 'queue', 'single']
    const idx = modes.indexOf(current)
    const nextMode = modes[(idx === -1 ? 0 : idx + 1) % modes.length]
    await api.setRepeat(nextMode)
    void get().loadQueue()
  },

  addAlbumToQueue: async (albumArtist, album, trackId = null) => {
    await api.addAlbumToQueue(albumArtist, album, trackId)
    void get().loadQueue()
  },

  playAlbumNext: async (albumArtist, album, trackId = null) => {
    await api.playAlbumNext(albumArtist, album, trackId)
    void get().loadQueue()
  },

  insertAlbumAt: async (albumArtist, album, index, trackId = null) => {
    await api.insertAlbumAt(albumArtist, album, index, trackId)
    void get().loadQueue()
  },

  addToQueue: async (ref) => {
    await api.addToQueue(ref)
    void get().loadQueue()
  },

  insertIntoQueue: async (ref, index) => {
    await api.insertIntoQueue(ref, index)
    void get().loadQueue()
  },

  playNext: async (ref) => {
    await api.playNext(ref)
    void get().loadQueue()
  },

  moveQueueTrack: async (fromIndex, toIndex) => {
    await api.moveQueueTrack(fromIndex, toIndex)
    void get().loadQueue()
  },

  reorderQueue: async (order) => {
    await api.reorderQueue(order)
    void get().loadQueue()
  },

  skipToQueueTrack: async (position) => {
    await api.skipToQueueTrack(position)
    void get().loadQueue()
  },

  clearQueue: async () => {
    await api.clearQueue()
    void get().loadQueue()
  },

  clearRemainingQueue: async (position) => {
    await api.clearRemainingQueue(position)
    void get().loadQueue()
  },

  removeFromQueue: async (indices) => {
    await api.removeFromQueue(indices)
    void get().loadQueue()
  },

  setFavorite: async (track, favorite) => {
    try {
      await api.setTrackFavorite(track, favorite)
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('404')) {
        // Track no longer at its expected path — library may have been updated.
        await get().refreshOpenAlbum()
      }
      return
    }
    // Keep the player state in sync if the favorited track is currently playing.
    if (get().player.current_track?.id === track.id) {
      set((s) => ({
        player: {
          ...s.player,
          current_track: s.player.current_track ? { ...s.player.current_track, favorite } : null
        }
      }))
    }
    // Patch any matching tracks in the queue so the indicator updates immediately.
    set((s) => ({
      queue: s.queue
        ? {
            ...s.queue,
            tracks: s.queue.tracks.map((t) => (t.id === track.id ? { ...t, favorite } : t))
          }
        : s.queue
    }))
    // Patch search results so the favorite glyph updates without a re-search.
    set((s) => ({
      searchResults: s.searchResults
        ? {
            ...s.searchResults,
            tracks: s.searchResults.tracks.map((t) => (t.id === track.id ? { ...t, favorite } : t))
          }
        : s.searchResults
    }))
    // Patch the open album track list and the playlist track list in place, keyed
    // on the canonical id — which never diverges between the streaming and
    // downloaded views (KAMP-538 fixes KAMP-532). A favorite never adds or removes
    // a row, so the old refreshOpenAlbum() refetch was both unnecessary and racy:
    // for a freshly-downloaded track it could read back a not-yet-committed value
    // and clobber this optimistic patch (the very reload race KAMP-532 describes).
    set((s) => ({
      library: {
        ...s.library,
        tracks: s.library.tracks.map((t) => (t.id === track.id ? { ...t, favorite } : t)),
        playlistTracks: s.library.playlistTracks.map((t) =>
          t.id === track.id ? { ...t, favorite } : t
        )
      }
    }))
  },

  setFavorites: async (tracks, favorite) => {
    // allSettled so a partial 404 doesn't silently mis-patch state for succeeded tracks
    const results = await Promise.allSettled(tracks.map((t) => api.setTrackFavorite(t, favorite)))
    // KAMP-538: key the patch set directly on the canonical id of each succeeded track.
    const ids = new Set(tracks.filter((_, i) => results[i].status === 'fulfilled').map((t) => t.id))
    if (ids.size === 0) return
    if (get().player.current_track && ids.has(get().player.current_track!.id)) {
      set((s) => ({
        player: {
          ...s.player,
          current_track: s.player.current_track ? { ...s.player.current_track, favorite } : null
        }
      }))
    }
    set((s) => ({
      queue: s.queue
        ? {
            ...s.queue,
            tracks: s.queue.tracks.map((t) => (ids.has(t.id) ? { ...t, favorite } : t))
          }
        : s.queue
    }))
    set((s) => ({
      searchResults: s.searchResults
        ? {
            ...s.searchResults,
            tracks: s.searchResults.tracks.map((t) => (ids.has(t.id) ? { ...t, favorite } : t))
          }
        : s.searchResults
    }))
    set((s) => ({
      library: {
        ...s.library,
        tracks: s.library.tracks.map((t) => (ids.has(t.id) ? { ...t, favorite } : t)),
        playlistTracks: s.library.playlistTracks.map((t) =>
          ids.has(t.id) ? { ...t, favorite } : t
        )
      }
    }))
  },

  patchTrackTitle: async (trackId, title, overwrite = false) => {
    const result = await api.patchTrackTags(trackId, title, overwrite)
    if ('collision' in result) return result
    if ('deferred' in result) {
      set((s) => ({ deferredOps: { ...s.deferredOps, [trackId]: result.op_id } }))
      return null
    }
    await get().refreshOpenAlbum()
    void get().loadQueue()
    return null
  },

  patchTrackDisplay: async (trackId, fields) => {
    const updated = await api.patchTrackDisplay(trackId, fields)
    // Update the track in the open track list without reloading the whole library.
    set((s) => ({
      library: {
        ...s.library,
        tracks: s.library.tracks.map((t) => (t.id === trackId ? updated : t))
      }
    }))
    void get().loadQueue()
  },

  patchTrackArtist: async (trackId, artist) => {
    const result = await api.patchTrackArtist(trackId, artist)
    if ('deferred' in result) {
      // DB already updated server-side; only the file write is pending.
      set((s) => ({ deferredOps: { ...s.deferredOps, [trackId]: result.op_id } }))
      await get().refreshOpenAlbum()
    } else {
      set((s) => ({
        library: {
          ...s.library,
          tracks: s.library.tracks.map((t) => (t.id === trackId ? result : t))
        }
      }))
    }
    void get().loadQueue()
  },

  clearDeferredOp: (trackId) =>
    set((s) => {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { [trackId]: _removed, ...rest } = s.deferredOps
      return { deferredOps: rest }
    }),

  setAlbumRenameProgress: (progress) => set({ albumRenameProgress: progress }),

  patchAlbumTags: async (albumArtist, album, opts) => {
    let result: Awaited<ReturnType<typeof api.patchAlbumTags>>
    try {
      result = await api.patchAlbumTags(albumArtist, album, opts)
    } catch (err) {
      set({ albumRenameProgress: null })
      throw err
    }
    if ('collision' in result) return result
    if (result.failed.length > 0) {
      console.error('[kamp] album rename: tag write failed for', result.failed)
    }
    if (result.deferred.length > 0) {
      set((s) => {
        const updated = { ...s.deferredOps }
        for (const d of result.deferred) updated[d.track_id] = d.op_id
        return { deferredOps: updated }
      })
    }
    // Clear progress, reload library so sidebar and album card reflect new names.
    set({ albumRenameProgress: null })
    await get().loadLibrary()
    // Refresh the queue so any queued tracks show the updated artist/album name.
    void get().loadQueue()
    // Re-select the open album under its new identity so the track list refreshes.
    const newArtist = opts.album_artist ?? albumArtist
    const newAlbum = opts.album ?? album
    const updatedAlbum = get().library.albums.find(
      (a) => a.album_artist === newArtist && a.album === newAlbum
    )
    if (updatedAlbum) {
      set((s) => ({ library: { ...s.library, selectedAlbum: updatedAlbum } }))
      await get().loadTracks(newArtist, newAlbum)
    }
    return null
  },

  patchAlbumDisplay: async (albumArtist, album, displayAlbum, displayAlbumArtist) => {
    const updated = await api.patchAlbumDisplay(
      albumArtist,
      album,
      displayAlbum,
      displayAlbumArtist
    )
    // Update the album in the library list and selectedAlbum without a full reload.
    set((s) => {
      const albums = s.library.albums.map((a) =>
        a.album_artist === albumArtist && a.album === album ? { ...a, ...updated } : a
      )
      const selectedAlbum =
        s.library.selectedAlbum?.album_artist === albumArtist &&
        s.library.selectedAlbum?.album === album
          ? { ...s.library.selectedAlbum, ...updated }
          : s.library.selectedAlbum
      return { library: { ...s.library, albums, selectedAlbum } }
    })
  },

  patchAlbumMeta: async (albumArtist, album, opts) => {
    const result = await api.patchAlbumMeta(albumArtist, album, opts)
    // Refresh the open track list so genre/label/year values update in the panel.
    const { library } = get()
    if (
      library.selectedAlbum?.album_artist === albumArtist &&
      library.selectedAlbum?.album === album
    ) {
      set((s) => ({
        library: { ...s.library, tracks: result.tracks }
      }))
    }
  },

  setAlbumFavorite: async (albumArtist, album, favorite) => {
    await api.setAlbumFavorite(albumArtist, album, favorite)
    const patchAlbum = (a: api.Album): api.Album =>
      a.album_artist === albumArtist && a.album === album ? { ...a, favorite } : a
    set((s) => ({
      library: {
        ...s.library,
        albums: s.library.albums.map(patchAlbum),
        selectedAlbum:
          s.library.selectedAlbum?.album_artist === albumArtist &&
          s.library.selectedAlbum?.album === album
            ? { ...s.library.selectedAlbum, favorite }
            : s.library.selectedAlbum
      },
      searchResults: s.searchResults
        ? { ...s.searchResults, albums: s.searchResults.albums.map(patchAlbum) }
        : s.searchResults
    }))
  },

  setLibraryPath: async (path) => {
    await api.setLibraryPath(path)
    set({ configuredLibraryPath: path })
  },

  setWatchFolderPath: async (path) => {
    await api.patchConfig('paths.watch_folder', path)
    set((s) => ({
      configValues: s.configValues
        ? { ...s.configValues, 'paths.watch_folder': path }
        : s.configValues
    }))
  },

  loadConfig: async () => {
    try {
      const configValues = await api.getConfig()
      set({
        configValues,
        configuredLibraryPath: (configValues['paths.library'] as string | null) ?? null
      })
    } catch {
      // Best-effort — preferences dialog will show empty fields.
    }
  },

  setConfigValue: async (key, value) => {
    await api.patchConfig(key, value)
    // Coerce the optimistic cache to the type the server would return (booleans
    // and numbers), matching the type loadConfig() populated. Storing the raw
    // string corrupts a bool key — "false" is truthy, so a disabled toggle
    // re-renders as ON when the dialog reopens (loadConfig only runs when the
    // cache is null). Preserve the existing value's type as the source of truth.
    set((s) => {
      if (!s.configValues) return s
      const prev = s.configValues[key as keyof typeof s.configValues]
      const coerced: string | boolean | number =
        typeof prev === 'boolean'
          ? value === 'true'
          : typeof prev === 'number'
            ? Number(value)
            : value
      return { configValues: { ...s.configValues, [key]: coerced } }
    })
  },

  openPrefs: (tab) => set({ prefsOpen: true, prefsInitialTab: tab ?? 'about' }),
  closePrefs: () => set({ prefsOpen: false }),
  setUpdateAvailable: (data) => set({ updateAvailable: data }),

  scanLibrary: async () => {
    set({ scanStatus: 'scanning', scanError: null, scanProgress: null })

    // Poll the server for progress at ~2 Hz while the scan runs.
    const pollInterval = setInterval(async () => {
      try {
        const progress = await api.getScanProgress()
        set({ scanProgress: progress })
      } catch {
        // Ignore transient poll errors — the scan result is what matters.
      }
    }, 500)

    try {
      const result = await api.scanLibrary()
      set({ scanStatus: 'done', lastScanResult: result, scanProgress: null })
      await get().loadLibrary()
    } catch (err) {
      const msg =
        err instanceof Error && err.message.includes('503')
          ? 'Library path not configured. Use the "Choose Library Folder" button.'
          : 'Scan failed. Check the server logs for details.'
      set({ scanStatus: 'error', scanError: msg, scanProgress: null })
    } finally {
      clearInterval(pollInterval)
    }
  },

  // KAMP-591: library-wide genre backfill. The daemon owns the long-running task;
  // the UI kicks it off and polls the GET progress endpoint (~1 Hz) while it runs.
  // Polling — not the websocket — mirrors scanLibrary and keeps the progress
  // read self-contained: it only matters while the preferences dialog is open.
  startGenreBackfill: async () => {
    set({
      genreBackfill: { active: true, done: 0, total: 0, state: 'running' }
    })
    try {
      await api.startGenreBackfill()
    } catch {
      set({ genreBackfill: { active: false, done: 0, total: 0, state: 'error' } })
      return
    }
    void get().refreshGenreBackfill()
  },

  cancelGenreBackfill: async () => {
    try {
      await api.cancelGenreBackfill()
    } catch {
      // best-effort — the poll will reflect the true state
    }
  },

  // One-shot fetch of the current backfill progress. Cadence is owned by the
  // caller (PreferencesDialog polls while open + active) so no interval outlives
  // the dialog — a multi-hour run shouldn't poll forever after prefs is closed.
  refreshGenreBackfill: async () => {
    try {
      set({ genreBackfill: await api.getGenreBackfillProgress() })
    } catch {
      // transient — leave the last known state in place
    }
  }
}))
