// KAMP-767: React wrapper for the Sparks background. The lifecycle is the shared
// particle-engine hook (KAMP-768); this file only names the engine.

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { useParticleEngine, type ParticleEngineFactory } from '../useParticleEngine'
import { SparksEngine } from './sparksEngine'

const create: ParticleEngineFactory = (canvas, initial, opts) =>
  new SparksEngine(canvas, initial, opts)

export function SparksCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  const canvasRef = useParticleEngine(create, props)
  // No engine class: no CSS filter. The blur is baked into the sprites (KAMP-776/777).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
