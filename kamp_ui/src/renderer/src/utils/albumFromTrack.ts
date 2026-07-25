import type { Album, Track } from '../api/client'

// KAMP-633: resolve the Album to navigate to (for "Go to Album") from a track,
// keyed on CANONICAL album identity (the albums row via album_id), never the
// track's mutable album TAG. After a rename the tag diverges from the canonical
// key that art (get_album_art) and navigation (tracks_for_album) resolve on, so
// a tag match lands on a blank album page — this keys on canonical instead.
//
// Untagged tracks (no album_id) route to a missing-album card keyed by track id
// (KAMP-554). When the real library Album is loaded, prefer it (full metadata /
// favorite state); otherwise synthesize a minimal one from the canonical fields.
//
// Shared by TrackContextMenu and QueueContextMenu so the two never drift
// (KAMP-554 sibling-divergence trap).
export function albumForTrackNav(track: Track, albums: Album[]): Album {
  if (track.album_id == null || track.canonical_album == null) {
    return {
      album_artist: track.album_artist || track.artist,
      album: track.title, // missing-album cards display the track title
      release_date: track.release_date,
      track_count: 1,
      has_art: track.embedded_art || track.source !== 'local',
      missing_album: true,
      track_id: track.id,
      art_version: null,
      added_at: null,
      last_played_at: null,
      play_count_avg: 0,
      favorite: false,
      has_favorite_track: track.favorite,
      source: track.source === 'bandcamp' ? 'bandcamp' : 'local',
      has_remote_tracks: track.source !== 'local',
      genres: []
    }
  }
  const albumArtist = track.canonical_album_artist ?? track.album_artist
  const album = track.canonical_album
  return (
    albums.find((a) => a.album_artist === albumArtist && a.album === album) ?? {
      album_artist: albumArtist,
      album,
      // display_* render the (possibly renamed) name; album/album_artist stay
      // canonical for art/nav.
      display_album: track.display_album ?? undefined,
      display_album_artist: track.display_album_artist ?? undefined,
      release_date: track.release_date,
      track_count: 0,
      has_art: track.embedded_art || track.source !== 'local',
      missing_album: false,
      track_id: null,
      art_version: track.album_art_version,
      added_at: null,
      last_played_at: null,
      play_count_avg: 0,
      favorite: false,
      has_favorite_track: track.favorite,
      source: track.source === 'bandcamp' ? 'bandcamp' : 'local',
      has_remote_tracks: track.source !== 'local',
      genres: []
    }
  )
}
