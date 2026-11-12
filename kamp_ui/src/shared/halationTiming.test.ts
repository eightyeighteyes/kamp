// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-774: the pure timing behind the Halation background. The main risk is
// strobing, so most of these pin rates of change rather than exact values.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  BurnEnvelope,
  RareEvents,
  SEED_FADE_S,
  SEED_PERIOD_S,
  SEED_WRAP,
  grainMix,
  seedAt,
  seedBoundary
} from './halationTiming.ts'

function seeded(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const variance = (xs: number[]): number => {
  const m = xs.reduce((a, b) => a + b, 0) / xs.length
  return xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length
}

// --- grain seed schedule --------------------------------------------------------

test('grain re-seeds every 3-4 s, never per frame', () => {
  const [lo, hi] = SEED_PERIOD_S
  for (let k = 0; k < 500; k++) {
    const period = seedBoundary(k + 1) - seedBoundary(k)
    assert.ok(period >= lo - 1e-9 && period <= hi + 1e-9, `epoch ${k} lasts ${period}s`)
  }
})

test('the re-seed is a 0.5 s crossfade with no jump in the grain', () => {
  // Sample the grain value a single pixel would show (fixed hashes per seed) and
  // check it never steps by more than the crossfade's slope allows.
  const pixel = (seed: number): number => ((Math.sin(seed * 12.9898) * 43758.5453) % 1 + 1) % 1
  const dt = 1 / 60
  let prev: number | null = null
  for (let t = 0; t < 60; t += dt) {
    const s = seedAt(t)
    assert.ok(s.mix >= 0 && s.mix <= 1)
    const v = grainMix(pixel(s.seedA), pixel(s.seedB), s.mix)
    if (prev != null) {
      // smoothstep's max slope is 1.5 / fade; the normalisation can amplify by
      // at most sqrt(2), and the value range of a normalised mix is under 2.
      const bound = (1.5 / SEED_FADE_S) * dt * Math.SQRT2 * 2 + 1e-9
      assert.ok(Math.abs(v - prev) <= bound, `grain stepped ${Math.abs(v - prev)} at t=${t}`)
    }
    prev = v
  }
})

test('outside a crossfade both seeds are the same, so A/B swap invisibly', () => {
  for (let k = 1; k < 50; k++) {
    const mid = seedBoundary(k) + SEED_FADE_S + 0.5
    const s = seedAt(mid)
    assert.equal(s.mix, 0)
    assert.equal(s.seedA, s.seedB)
    // At the instant the fade ends, the old pair at mix=1 shows the new seed.
    const end = seedAt(seedBoundary(k) + SEED_FADE_S - 1e-6)
    assert.ok(end.mix > 0.999)
    assert.equal(end.seedB, s.seedA)
  }
})

test('seeds stay small so the GLSL hash keeps its precision', () => {
  for (let t = 0; t < 2000; t += 0.37) {
    const s = seedAt(t)
    assert.ok(s.seedA >= 0 && s.seedA < SEED_WRAP)
    assert.ok(s.seedB >= 0 && s.seedB < SEED_WRAP)
  }
  // Hours in, still small (float32 uTime is not involved).
  const late = seedAt(6 * 3600 + 1.25)
  assert.ok(late.seedA < SEED_WRAP && late.seedB < SEED_WRAP)
})

test('grain contrast stays constant through the crossfade', () => {
  const rng = seeded(7)
  const a = Array.from({ length: 20000 }, rng)
  const b = Array.from({ length: 20000 }, rng)
  const base = variance(a)
  for (const m of [0, 0.25, 0.5, 0.75, 1]) {
    const v = variance(a.map((x, i) => grainMix(x, b[i], m)))
    assert.ok(Math.abs(v / base - 1) < 0.05, `variance ratio ${v / base} at mix ${m}`)
  }
  // A plain linear mix would sag to ~0.5 at the midpoint: the bug this prevents.
  const linear = variance(a.map((x, i) => x * 0.5 + b[i] * 0.5))
  assert.ok(linear / base < 0.6)
})

// --- track-change burn --------------------------------------------------------------

test('the burn starts with zero slope and peaks around 2 s', () => {
  const burn = new BurnEnvelope()
  burn.trigger(0)
  burn.tick(1 / 60)
  assert.ok(burn.value < 0.002, `first frame jumped to ${burn.value}`)
  let peakAt = 0
  let peak = 0
  for (let t = 1 / 60; t < 8; t += 1 / 60) {
    burn.tick(1 / 60)
    if (burn.value > peak) {
      peak = burn.value
      peakAt = t
    }
  }
  assert.ok(peakAt > 1.6 && peakAt < 2.5, `peak at ${peakAt}s`)
  assert.ok(peak <= BurnEnvelope.PEAK + 1e-9)
  assert.equal(burn.value, 0, 'the burn fades away completely')
  assert.equal(burn.active, false)
})

test('ten rapid track skips never flash or exceed the cap', () => {
  const burn = new BurnEnvelope()
  const dt = 1 / 60
  let prev = 0
  let frame = 0
  for (let skip = 0; skip < 10; skip++) {
    burn.trigger(skip % 3)
    for (let i = 0; i < 18; i++, frame++) {
      burn.tick(dt)
      assert.ok(burn.value <= BurnEnvelope.PEAK + 1e-9)
      assert.ok(
        Math.abs(burn.value - prev) <= BurnEnvelope.MAX_RATE * dt + 1e-9,
        `frame ${frame} stepped ${burn.value - prev}`
      )
      prev = burn.value
    }
  }
})

test('a retrigger mid-burn keeps the edge it is already burning from', () => {
  const burn = new BurnEnvelope()
  burn.trigger(1)
  for (let i = 0; i < 60; i++) burn.tick(1 / 60)
  burn.trigger(2)
  assert.equal(burn.edge, 1, 'moving the gradient to another edge would read as a flash')
  for (let i = 0; i < 600; i++) burn.tick(1 / 60)
  burn.trigger(2)
  assert.equal(burn.edge, 2, 'a fresh burn may pick a new edge')
})

test('a huge frame gap (fps 1, a stall) is clamped, not jumped', () => {
  const burn = new BurnEnvelope()
  burn.trigger(0)
  burn.tick(1)
  assert.ok(burn.value <= BurnEnvelope.MAX_RATE * BurnEnvelope.MAX_DT + 1e-9)
})

test('cancel drops a burn in flight (resume after a stop)', () => {
  const burn = new BurnEnvelope()
  burn.trigger(0)
  for (let i = 0; i < 90; i++) burn.tick(1 / 60)
  burn.cancel()
  assert.equal(burn.value, 0)
  assert.equal(burn.active, false)
  burn.tick(1 / 60)
  assert.equal(burn.value, 0)
})

// --- rare hair / jolt ---------------------------------------------------------------

function runEvents(
  ev: RareEvents,
  seconds: number,
  opts: { idle?: (t: number) => number; burning?: (t: number) => boolean } = {}
): { kinds: string[]; startTimes: number[] } {
  const dt = 1 / 20
  const kinds: string[] = []
  const startTimes: number[] = []
  let was = false
  let elapsed = 0
  for (let t = 0; t < seconds; t += dt) {
    elapsed += dt
    ev.tick(dt, elapsed, opts.idle?.(elapsed) ?? 1e9, opts.burning?.(elapsed) ?? false)
    const now = ev.active != null
    if (now && !was) {
      kinds.push(ev.active!.kind)
      startTimes.push(elapsed)
    }
    was = now
  }
  return { kinds, startTimes }
}

test('hairs and jolts are rare, spaced at least the gap floor apart', () => {
  const ev = new RareEvents(seeded(3))
  const { kinds, startTimes } = runEvents(ev, 3600)
  assert.ok(kinds.length >= 8 && kinds.length <= 60, `${kinds.length} events in an hour`)
  assert.ok(kinds.includes('hair') && kinds.includes('jolt'))
  for (let i = 1; i < startTimes.length; i++) {
    assert.ok(startTimes[i] - startTimes[i - 1] >= RareEvents.GAP_FLOOR_S)
  }
})

test('rare events wait for the cursor to be idle', () => {
  const ev = new RareEvents(seeded(5))
  const { kinds } = runEvents(ev, 1200, { idle: () => 0 })
  assert.equal(kinds.length, 0)
})

test('no rare event starts during a burn', () => {
  const ev = new RareEvents(seeded(9))
  const { kinds } = runEvents(ev, 1200, { burning: () => true })
  assert.equal(kinds.length, 0)
})

test('hair and jolt envelopes start and end at zero', () => {
  for (const seed of [1, 2, 3, 4, 5, 6]) {
    const ev = new RareEvents(seeded(seed))
    ev.forceNext()
    ev.tick(0.05, 1000, 1e9, false)
    const a = ev.active!
    assert.ok(a)
    assert.ok(Math.abs(ev.hairAlpha()) < 0.01 && Math.abs(ev.joltOffset()) < 0.05)
    while (ev.active) ev.tick(0.05, 1000, 1e9, false)
    assert.equal(ev.hairAlpha(), 0)
    assert.equal(ev.joltOffset(), 0)
  }
})

test('cancel drops an event in flight and reschedules', () => {
  const ev = new RareEvents(seeded(11))
  ev.forceNext()
  ev.tick(0.05, 500, 1e9, false)
  assert.ok(ev.active)
  ev.cancel(500)
  assert.equal(ev.active, null)
  assert.ok(ev.nextAt >= 500 + RareEvents.GAP_FLOOR_S)
})
