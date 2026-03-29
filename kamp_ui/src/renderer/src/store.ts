/**
 * Zustand store.
 *
 * All player and library state lives here. The renderer is a pure view layer:
 * components read from the store and dispatch actions — they hold no state
 * of their own that belongs in the daemon.
 */

import { create } from 'zustand'
import * as api from './api/client'
import type { Album, PlayerState, ScanProgress, ScanResult, Track } from './api/client'

type LibraryState = {
  albums: Album[]
  artists: string[]
  selectedArtist: string | null
  selectedAlbum: Album | null
  tracks: Track[]
  tracksAlbumKey: string | null // "artist\0album" key for the loaded track list
}

type PlayerStore = {
  player: PlayerState
  library: LibraryState
  serverStatus: 'connected' | 'reconnecting' | 'disconnected'
  scanStatus: 'idle' | 'scanning' | 'done' | 'error'
  lastScanResult: ScanResult | null
  scanError: string | null
  scanProgress: ScanProgress | null

  configuredLibraryPath: string | null

  // Actions
  setServerStatus: (status: 'connected' | 'reconnecting' | 'disconnected') => void
  loadLibrary: () => Promise<void>
  selectArtist: (artist: string | null) => void
  selectAlbum: (album: Album | null) => Promise<void>
  loadTracks: (albumArtist: string, album: string) => Promise<void>
  playAlbum: (albumArtist: string, album: string, trackIndex?: number) => Promise<void>
  playTrack: (albumArtist: string, album: string, trackIndex: number) => Promise<void>
  togglePlayPause: () => Promise<void>
  stop: () => Promise<void>
  next: () => Promise<void>
  prev: () => Promise<void>
  seek: (position: number) => Promise<void>
  setVolume: (volume: number) => Promise<void>
  setShuffle: (shuffle: boolean) => Promise<void>
  setRepeat: (repeat: boolean) => Promise<void>
  scanLibrary: () => Promise<void>
  setLibraryPath: (path: string) => Promise<void>
  applyServerState: (state: PlayerState) => void
}

const initialPlayer: PlayerState = {
  playing: false,
  position: 0,
  duration: 0,
  volume: 100,
  current_track: null
}

export const useStore = create<PlayerStore>((set, get) => ({
  player: initialPlayer,
  library: {
    albums: [],
    artists: [],
    selectedArtist: null,
    selectedAlbum: null,
    tracks: [],
    tracksAlbumKey: null
  },
  serverStatus: 'reconnecting',
  scanStatus: 'idle',
  lastScanResult: null,
  scanError: null,
  scanProgress: null,
  configuredLibraryPath: null,

  setServerStatus: (status) => set({ serverStatus: status }),

  applyServerState: (state) => set({ player: state }),

  loadLibrary: async () => {
    try {
      const [albums, artists] = await Promise.all([api.getAlbums(), api.getArtists()])
      set((s) => ({ library: { ...s.library, albums, artists }, serverStatus: 'connected' }))
    } catch {
      set({ serverStatus: 'disconnected' })
    }
  },

  selectArtist: (artist) =>
    set((s) => ({ library: { ...s.library, selectedArtist: artist, selectedAlbum: null } })),

  selectAlbum: async (album) => {
    set((s) => ({ library: { ...s.library, selectedAlbum: album } }))
    if (album) await get().loadTracks(album.album_artist, album.album)
  },

  loadTracks: async (albumArtist, album) => {
    const key = `${albumArtist}\0${album}`
    if (get().library.tracksAlbumKey === key) return
    const tracks = await api.getTracksForAlbum(albumArtist, album)
    set((s) => ({ library: { ...s.library, tracks, tracksAlbumKey: key } }))
  },

  playAlbum: async (albumArtist, album, trackIndex = 0) => {
    await api.playAlbum(albumArtist, album, trackIndex)
  },

  playTrack: async (albumArtist, album, trackIndex) => {
    await api.playAlbum(albumArtist, album, trackIndex)
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
  },

  prev: async () => {
    await api.prevTrack()
  },

  seek: async (position) => {
    await api.seek(position)
  },

  setVolume: async (volume) => {
    await api.setVolume(volume)
    set((s) => ({ player: { ...s.player, volume } }))
  },

  setShuffle: async (shuffle) => {
    await api.setShuffle(shuffle)
  },

  setRepeat: async (repeat) => {
    await api.setRepeat(repeat)
  },

  setLibraryPath: async (path) => {
    await api.setLibraryPath(path)
    set({ configuredLibraryPath: path })
  },

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
  }
}))
