// KAMP-768: React wrapper for the Wintermute background. The lifecycle is the
// shared particle-engine hook; this file only names the engine.

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { useParticleEngine, type ParticleEngineFactory } from '../useParticleEngine'
import { WintermuteEngine } from './wintermuteEngine'

const create: ParticleEngineFactory = (canvas, initial, opts) =>
  new WintermuteEngine(canvas, initial, opts)

export function WintermuteCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  const canvasRef = useParticleEngine(create, props)
  // No engine class: no CSS filter. The blur is baked into the sprites (KAMP-776/777).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
