// KAMP-780: registry Engine for the diagnostics-only flat reference background.

import React from 'react'
import type { NowPlayingEngineProps } from '../../registry'
import { ShaderCanvas } from '../ShaderCanvas'
import { FLAT_REFERENCE_SPEC } from './flatSpec'

export function FlatReference(props: NowPlayingEngineProps): React.JSX.Element {
  return <ShaderCanvas spec={FLAT_REFERENCE_SPEC} {...props} />
}
