// KAMP-771: what the Runout background needs from playback, mapped from the
// store's player snapshot and queue. Framework-free so `node --test` covers it:
// kamp_ui has no React test runner, so the hook's wiring is kept to a call of
// playbackOf() and every decision lives here.

import { fnv1a } from './particleUtil.ts'
import { albumKeyOf } from './trackIdentity.ts'

/** The fields of a track that a side needs (a subset of the renderer's Track). */
export interface SideTrack {
  id: number
  album_id: number | null
  disc_number: number
}

/** The player snapshot fields Runout reads (a subset of the renderer's PlayerState). */
export interface PlayerSnapshot {
  playing: boolean
  position: number
  duration: number
  current_track: SideTrack | null
  next_track: SideTrack | null
}

/** What the Runout engine is told on every snapshot. */
export interface RunoutPlayback {
  playing: boolean
  position: number // s
  duration: number // s; 0 while unknown
  /** The playing track is the last one on its side (see isEndOfSide). */
  endOfSide: boolean
}

/**
 * The playing track is the last of its side: nothing follows it, or what follows
 * is on another album or another disc. Under shuffle the server's next track is
 * the next one in the shuffled order, so an album change there says nothing about
 * the side ending; shuffle never ends a side. Repeat album/queue loops back into
 * the same album, so no runout shows there: accepted.
 */
export function isEndOfSide(
  current: SideTrack | null,
  next: SideTrack | null,
  shuffle: boolean
): boolean {
  if (!current || shuffle) return false
  if (!next) return true
  return albumKeyOf(next) !== albumKeyOf(current) || next.disc_number !== current.disc_number
}

/**
 * Map a player snapshot and the queue to Runout's playback. An unknown queue
 * (not loaded yet) is read as unshuffled. An unknown duration (a stream still
 * loading reports 0) is never the end of a side, or remaining time reads negative
 * and the runout fires at once.
 */
export function playbackOf(
  player: PlayerSnapshot,
  queue: { shuffle: boolean } | null
): RunoutPlayback {
  const known = player.duration > 0
  return {
    playing: player.playing,
    position: player.position,
    duration: player.duration,
    endOfSide:
      known && isEndOfSide(player.current_track, player.next_track, queue?.shuffle ?? false)
  }
}

/**
 * The hand-scratched matrix number in the runout area, e.g. "A-3". Decorative:
 * the side letter comes from the disc (floored at 1, as untagged tracks report 0)
 * and the digit is hashed from the album key, so an album always shows the same one.
 */
export function matrixNumber(albumKey: string, disc: number): string {
  const side = String.fromCharCode(64 + Math.min(26, Math.max(1, Math.floor(disc) || 1)))
  return `${side}-${(fnv1a(albumKey) % 9) + 1}`
}
