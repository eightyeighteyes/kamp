// KAMP-765: registry Engine for the Borealis background (see borealisSpec.ts).

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { ShaderCanvas } from '../webgl/ShaderCanvas'
import { BOREALIS_SPEC } from './borealisSpec'

export function BorealisCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  return <ShaderCanvas spec={BOREALIS_SPEC} {...props} />
}
