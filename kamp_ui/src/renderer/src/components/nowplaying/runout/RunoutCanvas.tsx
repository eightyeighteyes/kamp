// KAMP-771: React wrapper for the Runout background. The lifecycle is the shared
// particle-engine hook (which also feeds it playback, as it implements
// setPlayback); this file only names the engine.

import React from 'react'
import type { NowPlayingEngineProps } from '../registry'
import { useParticleEngine, type ParticleEngineFactory } from '../useParticleEngine'
import { RunoutEngine } from './runoutEngine'

const create: ParticleEngineFactory = (canvas, initial) => new RunoutEngine(canvas, initial)

export function RunoutCanvas(props: NowPlayingEngineProps): React.JSX.Element {
  const canvasRef = useParticleEngine(create, props)
  // No engine class: no CSS filter (KAMP-776/777).
  return <canvas ref={canvasRef} className="now-playing-backdrop" aria-hidden="true" />
}
