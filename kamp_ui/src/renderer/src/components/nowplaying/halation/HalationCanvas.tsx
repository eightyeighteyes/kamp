// KAMP-774: registry Engine for the Halation background (see halationSpec.ts).

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { ShaderCanvas } from '../webgl/ShaderCanvas'
import { HALATION_SPEC } from './halationSpec'

export function HalationCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  return <ShaderCanvas spec={HALATION_SPEC} {...props} />
}
