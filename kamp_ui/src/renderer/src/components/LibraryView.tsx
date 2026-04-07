/**
 * Library view: shows the album grid or the track list for the selected album.
 *
 * Extracted from App.tsx so it can register as a built-in slot panel
 * (kamp.library) while keeping the AlbumGrid/TrackList switching logic
 * in one place.
 */

import React from 'react'
import { useStore } from '../store'
import { AlbumGrid } from './AlbumGrid'
import { TrackList } from './TrackList'

export function LibraryView(): React.JSX.Element {
  const selectedAlbum = useStore((s) => s.library.selectedAlbum)
  return selectedAlbum ? <TrackList /> : <AlbumGrid />
}
