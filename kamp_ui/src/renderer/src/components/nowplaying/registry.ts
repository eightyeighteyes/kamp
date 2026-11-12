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
//
// KAMP-780: a WebGL background satisfies all of this by being a ShaderSpec
// wrapped in webgl/ShaderCanvas (see webgl/README.md). Its canvas is opaque and
// paints --bg itself, so it keeps the backdrop class for layout only.
// Kept as .ts (no component definitions) so react-refresh lint stays happy.

import type { ComponentType } from 'react'
import type {
  NowPlayingAnimationId,
  NowPlayingOverrideId
} from '../../../../shared/nowPlayingAnimation'
import { BokehCanvas } from './BokehCanvas'
import { BorealisCanvas } from './borealis/BorealisCanvas'
import { ChapelCanvas } from './ChapelCanvas'
import { HalationCanvas } from './halation/HalationCanvas'
import { FlatReference } from './webgl/reference/FlatReference'

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
  chapel: { label: 'Chapel', Engine: ChapelCanvas },
  halation: { label: 'Halation', Engine: HalationCanvas },
  borealis: { label: 'Borealis', Engine: BorealisCanvas }
} satisfies Record<NowPlayingAnimationId, NowPlayingAnimation>

// KAMP-780: diagnostics-only backgrounds, selected via window.__kampGL and shown
// in place of the user's choice. Never in the picker, never persisted.
export const NOW_PLAYING_OVERRIDES = {
  'webgl-reference': FlatReference
} satisfies Record<NowPlayingOverrideId, ComponentType<NowPlayingEngineProps>>
