// KAMP-766: the Now Playing animation registry. Adding an animation = add its id
// to NOW_PLAYING_ANIMATION_IDS (shared/nowPlayingAnimation.ts) and an entry here;
// `satisfies Record<…>` makes a missing entry a compile error, so the two lists
// cannot drift.
//
// Engine contract — each Engine component owns, for its whole mounted lifetime:
//   * rendering a canvas with the shared `now-playing-backdrop` class (KAMP-631
//     opaque backing; without it non-default palettes show a #141414 seam), plus
//     its own class for engine-specific styling such as blur;
//   * running its loop only while `active`, the window is visible, and
//     reduced-motion is off (reduced-motion paints a static frame instead);
//   * stopping every loop and listener on unmount. NowPlayingBackground keys the
//     engine by id, so switching animations always unmounts the old one.
// Kept as .ts (no component definitions) so react-refresh lint stays happy.

import type { ComponentType } from 'react'
import type { NowPlayingAnimationId } from '../../../../shared/nowPlayingAnimation'
import { BokehCanvas } from './BokehCanvas'
import { ThalassaCanvas } from './ThalassaCanvas'

export interface NowPlayingEngineProps {
  /** The Now Playing pane is the visible view. */
  active: boolean
  /** Current track's album art, for palette extraction. */
  artUrl: string
}

interface NowPlayingAnimation {
  label: string
  /** null = no background at all (no canvas, no GPU). */
  Engine: ComponentType<NowPlayingEngineProps> | null
}

export const NOW_PLAYING_ANIMATIONS = {
  none: { label: 'None', Engine: null },
  bokeh: { label: 'Bokeh', Engine: BokehCanvas },
  thalassa: { label: 'Thalassa (spike)', Engine: ThalassaCanvas }
} satisfies Record<NowPlayingAnimationId, NowPlayingAnimation>
