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
import { CastaliaCanvas } from './castalia/CastaliaCanvas'
import { ChapelCanvas } from './ChapelCanvas'
import { HalationCanvas } from './halation/HalationCanvas'
import { SparksCanvas } from './sparks/SparksCanvas'
import { TannhauserCanvas } from './tannhauser/TannhauserCanvas'
import { FlatReference } from './webgl/reference/FlatReference'
import { WintermuteCanvas } from './wintermute/WintermuteCanvas'

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

// KAMP-783: labels are lowercase to match the rest of the Style rail; the picker
// orders them via pickerOrder(). 'off' is display text only: the id stays 'none'
// because it is persisted and mirrored to the legacy glow key.
export const NOW_PLAYING_ANIMATIONS = {
  none: { label: 'off', Engine: null },
  bokeh: { label: 'bokeh', Engine: BokehCanvas },
  chapel: { label: 'chapel', Engine: ChapelCanvas },
  halation: { label: 'halation', Engine: HalationCanvas },
  borealis: { label: 'borealis', Engine: BorealisCanvas },
  tannhauser: { label: 'tannhäuser', Engine: TannhauserCanvas },
  sparks: { label: 'sparks', Engine: SparksCanvas },
  wintermute: { label: 'wintermute', Engine: WintermuteCanvas },
  castalia: { label: 'castalia', Engine: CastaliaCanvas }
} satisfies Record<NowPlayingAnimationId, NowPlayingAnimation>

// KAMP-780: diagnostics-only backgrounds, selected via window.__kampGL and shown
// in place of the user's choice. Never in the picker, never persisted.
export const NOW_PLAYING_OVERRIDES = {
  'webgl-reference': FlatReference
} satisfies Record<NowPlayingOverrideId, ComponentType<NowPlayingEngineProps>>
