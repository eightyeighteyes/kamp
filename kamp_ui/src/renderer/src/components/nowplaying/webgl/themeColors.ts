// KAMP-780: theme reads and the per-track palette load shared by the shader
// wrapper and its static fallback. Kept as .ts (hooks only, no components) so
// react-refresh lint stays happy.

import { useEffect, useRef } from 'react'
import { loadPalette, type Palette } from '../palette'

// Read in post-commit effects, the reliable pattern from KAMP-631.
export function accentHex(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#7c86e1'
}

export function bgHex(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#141414'
}

/**
 * Derive the palette for each track's art and hand it to `apply`. Guards skip
 * races: the in-flight fetch is aborted and a stale resolution is ignored.
 */
export function useAlbumPalette(artUrl: string, apply: (p: Palette) => void): void {
  const applyRef = useRef(apply)
  useEffect(() => {
    applyRef.current = apply
  })
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    void loadPalette(artUrl, accentHex(), controller.signal).then((pal) => {
      if (!cancelled) applyRef.current(pal)
    })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [artUrl])
}
