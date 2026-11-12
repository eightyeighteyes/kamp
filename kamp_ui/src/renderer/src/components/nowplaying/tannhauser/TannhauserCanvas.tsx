// KAMP-772: registry Engine for the Tannhäuser background (see tannhauserSpec.ts).

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { ShaderCanvas } from '../webgl/ShaderCanvas'
import { TANNHAUSER_SPEC } from './tannhauserSpec'

export function TannhauserCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  return <ShaderCanvas spec={TANNHAUSER_SPEC} {...props} />
}
