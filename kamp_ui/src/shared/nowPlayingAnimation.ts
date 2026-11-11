// KAMP-766: the Now Playing animation preference. Framework-free so `node --test`
// can exercise the migration directly; the renderer registry
// (components/nowplaying/registry.ts) is keyed by NowPlayingAnimationId, so adding
// an id here without a registry entry fails to compile.

export const NOW_PLAYING_ANIMATION_IDS = ['none', 'bokeh'] as const
export type NowPlayingAnimationId = (typeof NOW_PLAYING_ANIMATION_IDS)[number]

export const NOW_PLAYING_ANIMATION_KEY = 'kamp:nowplaying-animation'
// Pre-KAMP-766 boolean toggle. Still written on every change so a downgraded
// build (which reads only this key) keeps honouring "None".
export const LEGACY_GLOW_KEY = 'kamp:nowplaying-glow-enabled'

function isAnimationId(value: string | null): value is NowPlayingAnimationId {
  return (NOW_PLAYING_ANIMATION_IDS as readonly (string | null)[]).includes(value)
}

/**
 * Pick the animation from stored preferences. A known stored id wins; otherwise
 * (never chosen, or an id from a newer build after a downgrade) the legacy toggle
 * decides: only an explicit 'false' meant off, so everything else is Bokeh.
 */
export function resolveNowPlayingAnimation(
  stored: string | null,
  legacyGlow: string | null
): NowPlayingAnimationId {
  if (isAnimationId(stored)) return stored
  return legacyGlow === 'false' ? 'none' : 'bokeh'
}

/** The legacy toggle value that mirrors an animation choice. */
export function legacyGlowValue(id: NowPlayingAnimationId): 'true' | 'false' {
  return id === 'none' ? 'false' : 'true'
}
