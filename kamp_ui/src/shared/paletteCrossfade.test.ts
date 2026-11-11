// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-780: the CPU-side palette crossfade shared by the WebGL backgrounds.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PaletteCrossfade } from './paletteCrossfade.ts'

const RED = { r: 255, g: 0, b: 0 }
const BLUE = { r: 0, g: 0, b: 255 }

test('a palette set while stopped is shown immediately, not the previous track', () => {
  // KAMP-779: with no loop running, nothing advances the fade, so a still frame
  // painted from a 0%-faded palette shows the PREVIOUS track's colors forever.
  const x = new PaletteCrossfade([RED], 1000)
  x.set([BLUE], false)
  assert.deepEqual(x.current(), [BLUE])
  assert.equal(x.settled, true)
})

test('a palette set while running eases from the old colors to the new', () => {
  const x = new PaletteCrossfade([RED], 1000)
  x.set([BLUE], true)
  assert.deepEqual(x.current(), [RED])
  x.tick(500)
  const mid = x.current()[0]
  assert.ok(
    mid.r > 0 && mid.r < 255 && mid.b > 0 && mid.b < 255,
    `expected a blend, got ${JSON.stringify(mid)}`
  )
  x.tick(500)
  assert.deepEqual(x.current(), [BLUE])
  assert.equal(x.settled, true)
})

test('retargeting mid-fade starts from the blend on screen, without a jump', () => {
  const x = new PaletteCrossfade([RED], 1000)
  x.set([BLUE], true)
  x.tick(500)
  const onScreen = x.current()[0]
  x.set([RED], true)
  assert.deepEqual(x.current()[0], onScreen)
})

test('a palette longer than the previous one blends the extra slots from their target', () => {
  const x = new PaletteCrossfade([RED], 1000)
  x.set([BLUE, RED], true)
  x.tick(500)
  assert.deepEqual(x.current()[1], RED)
})

test('ticking past the end never overshoots the target', () => {
  const x = new PaletteCrossfade([RED], 1000)
  x.set([BLUE], true)
  x.tick(5000)
  assert.deepEqual(x.current(), [BLUE])
})
