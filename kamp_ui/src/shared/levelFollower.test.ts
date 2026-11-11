// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-776 spike: the audio-level follower that drives a shader uniform.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LevelFollower, dbToLevel } from './levelFollower.ts'

// Advance the follower in 33ms frames (the engines' ~30fps cap) and return the last value.
function run(f: LevelFollower, fromMs: number, toMs: number): number {
  let v = 0
  for (let t = fromMs; t <= toMs; t += 33) v = f.valueAt(t)
  return v
}

test('dbToLevel maps the floor to 0 and full scale to 1', () => {
  assert.equal(dbToLevel(0, -60), 1)
  assert.equal(dbToLevel(-60, -60), 0)
  assert.equal(dbToLevel(-30, -60), 0.5)
})

test('silence, -Infinity, null and NaN all read as 0', () => {
  assert.equal(dbToLevel(-90, -60), 0)
  assert.equal(dbToLevel(-Infinity, -60), 0)
  assert.equal(dbToLevel(null, -60), 0)
  assert.equal(dbToLevel(Number.NaN, -60), 0)
})

test('a level above full scale is clamped to 1', () => {
  assert.equal(dbToLevel(6, -60), 1)
})

test('a sample has no effect until the delay has elapsed', () => {
  // The meter runs ahead of the speakers; reacting early reads as the visual leading the music.
  const f = new LevelFollower({ delayMs: 300 })
  for (let t = 0; t <= 280; t += 50) f.push(0, t)
  assert.equal(run(f, 0, 280), 0)
})

test('a held level is reached once the delay and smoothing have elapsed', () => {
  const f = new LevelFollower({ delayMs: 300, tauMs: 150 })
  let v = 0
  for (let t = 0; t <= 2000; t += 33) {
    if (t % 50 < 33) f.push(-30, t) // ~20Hz feed, interleaved with frames
    v = f.valueAt(t)
  }
  assert.ok(Math.abs(v - 0.5) < 0.02, `expected ~0.5, got ${v}`)
})

test('the value eases toward a step instead of jumping', () => {
  const f = new LevelFollower({ delayMs: 0, tauMs: 150 })
  f.push(0, 0)
  f.valueAt(0)
  const first = f.valueAt(33)
  assert.ok(first > 0 && first < 0.5, `one frame should move partway, got ${first}`)
})

test('the value decays to 0 when samples stop arriving', () => {
  // Pause/stop simply ends the feed; holding the last value would freeze the swell.
  const f = new LevelFollower({ delayMs: 300, tauMs: 150, staleMs: 250 })
  for (let t = 0; t <= 1000; t += 50) f.push(0, t)
  const loud = run(f, 0, 1300)
  assert.ok(loud > 0.9, `expected loud before the gap, got ${loud}`)
  const after = run(f, 1300, 3000)
  assert.ok(after < 0.02, `expected decay to ~0, got ${after}`)
})

test('a long gap between frames does not overshoot', () => {
  const f = new LevelFollower({ delayMs: 0, tauMs: 150 })
  f.push(-30, 0)
  f.valueAt(0)
  const v = f.valueAt(10_000)
  assert.ok(v <= 0.5 + 1e-9, `must not overshoot the target, got ${v}`)
})
