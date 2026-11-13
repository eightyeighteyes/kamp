// KAMP-781: registry Engine for the Chapel background (see chapel/chapelSpec.ts).
// Chapel was a Canvas 2D engine with its own lifecycle (KAMP-775); it now runs on
// the shared WebGL base, which owns the lifecycle, track-change gating and pointer.

import React from 'react'
import { CHAPEL_SPEC } from './chapel/chapelSpec'
import type { NowPlayingEngineProps } from './registry'
import { ShaderCanvas } from './webgl/ShaderCanvas'

export function ChapelCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  return <ShaderCanvas spec={CHAPEL_SPEC} {...props} />
}
