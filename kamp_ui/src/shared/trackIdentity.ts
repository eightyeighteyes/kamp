// KAMP-773: the identity a Now Playing background can key on, so it can tell a
// skip within an album from a new album. Framework-free so `node --test` covers it.

/** The fields of a track that identity needs (a subset of the renderer's Track). */
export interface IdentityTrack {
  id: number
  album_id: number | null
}

/**
 * A stable album key. Uses the canonical album row, never the display or tag
 * strings (KAMP-633). Untagged (missing-album) tracks have no album row; each is
 * its own one-track album, keyed by the track (KAMP-554).
 */
export function albumKeyOf(t: IdentityTrack): string {
  return t.album_id != null ? `album:${t.album_id}` : `track:${t.id}`
}

/** The track's own key. */
export function trackKeyOf(t: IdentityTrack): string {
  return String(t.id)
}
