// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-765: the pure timing behind the Borealis background. Like Halation's, most
// of these pin rates of change: nothing may jump or strobe.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  FLICKER_STRIPS,
  FLOW_PERIODS_S,
  FlickerEvents,
  LeanFollower,
  SurgeEnvelope,
  flowPhase
} from './borealisTiming.ts'

function seeded(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** A frame-time sequence mixing zero, normal, capped and oversized steps. */
function jitteredDts(n: number, rng: () => number): number[] {
  const opts = [0, 1 / 60, 1 / 30, 0.05, 0.1, 0.4, 1.0]
  return Array.from({ length: n }, () => opts[Math.floor(rng() * opts.length)])
}

const TAU = Math.PI * 2
/** Smallest angular distance, so a 2π wrap reads as continuous. */
const angDist = (a: number, b: number): number => {
  const d = Math.abs(a - b) % TAU
  return Math.min(d, TAU - d)
}

// --- flow ------------------------------------------------------------------------

test('flow phases are periodic in each loop period', () => {
  for (const t of [0, 13.7, 500, 9999.25]) {
    const a = flowPhase(t)
    const b = flowPhase(t + FLOW_PERIODS_S[0])
    const c = flowPhase(t + FLOW_PERIODS_S[1])
    assert.ok(angDist(a[0], b[0]) < 1e-9)
    assert.ok(angDist(a[1], c[1]) < 1e-9)
  }
})

test('flow phases stay within 0..2π and are continuous across the wrap', () => {
  const step = 1 / 30
  let prev = flowPhase(0)
  for (let t = step; t < FLOW_PERIODS_S[1] * 2; t += step) {
    const cur = flowPhase(t)
    for (const i of [0, 1]) {
      assert.ok(cur[i] >= 0 && cur[i] < TAU)
      // One frame advances at most 2π·step/period.
      assert.ok(angDist(cur[i], prev[i]) <= (TAU * step) / FLOW_PERIODS_S[i] + 1e-9)
    }
    prev = cur
  }
})

test('flow phases are still accurate after ten hours (float64 on the CPU)', () => {
  const t = 36000.123
  const step = 1 / 30
  const a = flowPhase(t)
  const b = flowPhase(t + step)
  for (const i of [0, 1]) {
    const expected = (TAU * step) / FLOW_PERIODS_S[i]
    assert.ok(Math.abs(angDist(a[i], b[i]) - expected) < 1e-6)
  }
})

test('the two loops are incommensurate enough that the combined repeat is hours', () => {
  const [p0, p1] = FLOW_PERIODS_S
  const gcd = (x: number, y: number): number => (y === 0 ? x : gcd(y, x % y))
  assert.ok((p0 * p1) / gcd(p0, p1) >= 3600)
})

// --- track-change surge ----------------------------------------------------------

test('a surge rises, peaks and falls back to nothing', () => {
  const s = new SurgeEnvelope()
  assert.equal(s.active, false)
  s.trigger(0)
  let peak = 0
  for (let i = 0; i < 400; i++) {
    s.tick(1 / 60)
    peak = Math.max(peak, s.bands[0].value)
  }
  assert.ok(peak > 0.99)
  assert.equal(s.active, false)
  assert.equal(s.bands[0].value, 0)
})

test('a surge rises gradually: no step faster than MAX_RATE, whatever dt', () => {
  const rng = seeded(7)
  for (let run = 0; run < 20; run++) {
    const s = new SurgeEnvelope()
    let prev = [0, 0]
    for (const dt of jitteredDts(200, rng)) {
      if (rng() < 0.05) s.trigger(rng() < 0.5 ? 0 : 1)
      s.tick(dt)
      const cur = s.bands.map((b) => b.value)
      for (const i of [0, 1]) {
        const step = Math.abs(cur[i] - prev[i])
        assert.ok(step <= SurgeEnvelope.MAX_RATE * SurgeEnvelope.MAX_DT + 1e-9, `step ${step}`)
      }
      prev = cur
    }
  }
})

test('a visible band never jumps position, even under rapid skips', () => {
  const rng = seeded(11)
  for (let run = 0; run < 20; run++) {
    const s = new SurgeEnvelope()
    let prev = s.bands.map((b) => ({ ...b }))
    for (const dt of jitteredDts(300, rng)) {
      if (rng() < 0.08) s.trigger(rng() < 0.5 ? 0 : 1)
      s.tick(dt)
      s.bands.forEach((b, i) => {
        const p = prev[i]
        // Only a band that was invisible may be re-launched from the start edge.
        if (p.value > 0.001) {
          const moved = Math.abs(b.position - p.position)
          assert.ok(moved <= SurgeEnvelope.MAX_DT / SurgeEnvelope.SWEEP_S + 1e-9, `moved ${moved}`)
          assert.equal(b.dir, p.dir)
        }
      })
      prev = s.bands.map((b) => ({ ...b }))
    }
  }
})

test('a skip while a band is early on brightens that band instead of starting another', () => {
  const s = new SurgeEnvelope()
  s.trigger(0)
  for (let i = 0; i < 30; i++) s.tick(1 / 60)
  const pos = s.bands[0].position
  s.trigger(1)
  s.tick(1 / 60)
  assert.ok(s.bands[0].position > pos, 'the first band keeps travelling')
  assert.equal(s.bands[0].dir, 0, 'and keeps its direction')
  assert.equal(s.bands[1].value, 0, 'no second band')
})

test('a skip once the band is past halfway rolls a second band in from zero', () => {
  const s = new SurgeEnvelope()
  s.trigger(0)
  while (s.bands[0].position < 0.6) s.tick(1 / 60)
  s.trigger(1)
  assert.equal(s.bands[1].value, 0, 'starts invisible, so it cannot pop')
  assert.equal(s.bands[1].position, 0)
  for (let i = 0; i < 60; i++) s.tick(1 / 60)
  assert.ok(s.bands[1].value > 0.5)
  assert.ok(s.bands[0].value > 0, 'the first band is still finishing, not dropped')
})

test('cancel drops every band in flight', () => {
  const s = new SurgeEnvelope()
  s.trigger(0)
  for (let i = 0; i < 30; i++) s.tick(1 / 60)
  s.cancel()
  assert.equal(s.active, false)
  for (const b of s.bands) assert.equal(b.value, 0)
})

// --- cursor lean -----------------------------------------------------------------

test('the lean attacks smoothly while the cursor moves over the pane', () => {
  const l = new LeanFollower()
  let prev = 0
  for (let i = 0; i < 60; i++) {
    l.tick(1 / 30, 1, 0, { x: 0.3, y: -0.2 })
    assert.ok(l.amount >= prev)
    assert.ok(l.amount - prev <= (1 / 30 / LeanFollower.ATTACK_S) * 1.0001)
    prev = l.amount
  }
  assert.ok(l.amount > 0.9)
})

test('the lean eases back once the cursor rests over the pane', () => {
  const l = new LeanFollower()
  for (let i = 0; i < 60; i++) l.tick(1 / 30, 1, 0, { x: 0.3, y: 0 })
  const leaning = l.amount
  // Still over the pane (pointerActive stays 1), but idle past REST_S.
  let idle = 0
  for (let i = 0; i < 150; i++) {
    idle += 1 / 30
    l.tick(1 / 30, 1, idle, { x: 0.3, y: 0 })
  }
  assert.ok(leaning > 0.9)
  assert.ok(l.amount < 0.05, `still leaning ${l.amount}`)
})

test('the lean re-attacks smoothly when the cursor moves again', () => {
  const l = new LeanFollower()
  for (let i = 0; i < 60; i++) l.tick(1 / 30, 1, 10, { x: 0, y: 0 })
  assert.ok(l.amount < 0.01)
  l.tick(1 / 30, 1, 0, { x: 0, y: 0 })
  assert.ok(l.amount > 0 && l.amount < 0.2, `jumped to ${l.amount}`)
})

test('on leave the anchor holds and only the amount decays', () => {
  const l = new LeanFollower()
  for (let i = 0; i < 90; i++) l.tick(1 / 30, 1, 0, { x: 0.6, y: -0.4 })
  const ax = l.x
  const ay = l.y
  // Leave: the base keeps pointerAt at the last real position and fades
  // pointerActive; idle restarts at the leave.
  let active = 1
  for (let i = 0; i < 90; i++) {
    active *= Math.exp(-1 / 30 / 0.6)
    l.tick(1 / 30, active, i / 30, { x: 0.6, y: -0.4 })
    assert.ok(Math.abs(l.x - ax) < 1e-9 && Math.abs(l.y - ay) < 1e-9)
  }
  assert.ok(l.amount < 0.1)
})

test('the anchor eases to a new cursor position rather than snapping', () => {
  const l = new LeanFollower()
  for (let i = 0; i < 90; i++) l.tick(1 / 30, 1, 0, { x: -0.5, y: 0 })
  l.tick(1 / 30, 1, 0, { x: 0.5, y: 0 })
  assert.ok(l.x > -0.5 && l.x < 0.0, `anchor at ${l.x}`)
})

test('cancel zeroes the lean', () => {
  const l = new LeanFollower()
  for (let i = 0; i < 60; i++) l.tick(1 / 30, 1, 0, { x: 0.3, y: 0 })
  l.cancel()
  assert.equal(l.amount, 0)
})

// --- rare edge flicker -----------------------------------------------------------

function runUntilFlicker(
  f: FlickerEvents,
  opts: { idle?: number; surging?: boolean } = {}
): number {
  let t = 0
  while (!f.active && t < 2000) {
    t += 0.1
    f.tick(0.1, t, opts.idle ?? 60, opts.surging ?? false, 0)
  }
  return t
}

test('flicker gaps stay within the floor and the cap', () => {
  const rng = seeded(3)
  for (let i = 0; i < 300; i++) {
    const f = new FlickerEvents(rng)
    assert.ok(f.nextAt >= FlickerEvents.GAP_FLOOR_S - 1e-9)
    assert.ok(f.nextAt <= FlickerEvents.GAP_MAX_S + 1e-9)
  }
})

test('flicker gaps vary: it is never on a schedule', () => {
  const rng = seeded(5)
  const gaps = Array.from({ length: 50 }, () => new FlickerEvents(rng).nextAt)
  const spread = Math.max(...gaps) - Math.min(...gaps)
  assert.ok(spread > 60, `spread only ${spread}s`)
})

test('a due flicker waits for the cursor to be idle', () => {
  const f = new FlickerEvents(seeded(1))
  f.forceNext()
  f.tick(0.1, 1, FlickerEvents.IDLE_S - 1, false, 0)
  assert.equal(f.active, null)
  f.tick(0.1, 1.1, FlickerEvents.IDLE_S + 1, false, 0)
  assert.notEqual(f.active, null)
})

test('a flicker never starts during a surge', () => {
  const f = new FlickerEvents(seeded(1))
  f.forceNext()
  for (let i = 0; i < 100; i++) f.tick(0.1, i * 0.1, 60, true, 0)
  assert.equal(f.active, null)
})

test('a flicker settles: zero brightness and zero slope at both ends', () => {
  const f = new FlickerEvents(seeded(9))
  f.forceNext()
  runUntilFlicker(f)
  const a = f.active!
  const at = (age: number): number => FlickerEvents.envelope(age, a.life)
  const h = 1e-4
  assert.equal(at(0), 0)
  assert.ok(Math.abs(at(a.life)) < 1e-9)
  assert.ok(Math.abs((at(h) - at(0)) / h) < 0.01)
  assert.ok(Math.abs((at(a.life) - at(a.life - h)) / h) < 0.01)
})

test('the flicker stays far below the photosensitivity limit', () => {
  // Count local brightness maxima: at most FLUTTER_HZ per second, so a 2-3 s
  // flicker shows a few soft pulses, never anything near 3 flashes a second.
  const life = 3
  const n = 3000
  let peaks = 0
  let prev = FlickerEvents.envelope(0, life)
  let rising = true
  for (let i = 1; i <= n; i++) {
    const v = FlickerEvents.envelope((i / n) * life, life)
    if (rising && v < prev) peaks++
    rising = v >= prev
    prev = v
  }
  assert.ok(FlickerEvents.FLUTTER_HZ <= 1.2)
  assert.ok(peaks <= Math.ceil(life * FlickerEvents.FLUTTER_HZ), `${peaks} peaks`)
  // And the flutter's dip is bounded: it modulates, it doesn't blink off.
  let maxStep = 0
  for (let i = 1; i <= n; i++) {
    const d = Math.abs(
      FlickerEvents.envelope((i / n) * life, life) -
        FlickerEvents.envelope(((i - 1) / n) * life, life)
    )
    maxStep = Math.max(maxStep, d / (life / n))
  }
  assert.ok(maxStep < 3, `slope ${maxStep}/s`)
})

test('with the art shown, a flicker lands in the side strips', () => {
  const rng = seeded(21)
  for (let i = 0; i < 200; i++) {
    const f = new FlickerEvents(rng)
    f.forceNext()
    f.tick(0.1, 1, 60, false, 1)
    const x = f.active!.x
    const inStrip = FLICKER_STRIPS.some(([lo, hi]) => x >= lo && x <= hi)
    assert.ok(inStrip, `x=${x}`)
  }
})

test('a flicker ends and schedules the next one', () => {
  const f = new FlickerEvents(seeded(4))
  f.forceNext()
  const start = runUntilFlicker(f)
  let t = start
  while (f.active) {
    t += 0.1
    f.tick(0.1, t, 60, false, 0)
  }
  assert.ok(f.nextAt >= t + FlickerEvents.GAP_FLOOR_S - 0.2)
})

test('cancel drops a flicker in flight', () => {
  const f = new FlickerEvents(seeded(2))
  f.forceNext()
  runUntilFlicker(f)
  f.cancel(5)
  assert.equal(f.active, null)
  assert.equal(f.value(), 0)
})
