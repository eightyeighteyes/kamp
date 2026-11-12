// KAMP-773: React wrapper for the Castalia background. The lifecycle is the
// shared particle-engine hook; this file only names the engine.

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { useParticleEngine, type ParticleEngineFactory } from '../useParticleEngine'
import { CastaliaEngine } from './castaliaEngine'

// No reduced-motion option: under reduced motion the loop never starts, the model
// never ticks, and the prewarmed grains already are the settled still frame.
const create: ParticleEngineFactory = (canvas, initial) => new CastaliaEngine(canvas, initial)

export function CastaliaCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  const canvasRef = useParticleEngine(create, props)
  // No engine class: no CSS filter. The blur is baked into the sprites (KAMP-776/777).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
