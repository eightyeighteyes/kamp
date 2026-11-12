// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-767: the particle model behind the Sparks background.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  BURST_COUNT,
  BURST_FALLBACK_S,
  KIND_AMBIENT,
  KIND_BURST,
  KIND_POP,
  MAX_GENERATIONS,
  POOL_SIZE,
  RESERVED_SLOTS,
  SHIMMER_HZ,
  SIZE_BUCKETS,
  SparkField,
  stillFrame,
  type PaneRect
} from './sparksModel.ts'

function seeded(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const DT = 1 / 30
const run = (f: SparkField, seconds: number, dt = DT): void => {
  for (let t = 0; t < seconds; t += dt) f.tick(dt)
}
const countKind = (f: SparkField, kind: number): number => {
  let n = 0
  for (let i = 0; i < POOL_SIZE; i++) if (f.kind[i] === kind) n++
  return n
}
const live = (f: SparkField): number => {
  let n = 0
  for (let i = 0; i < POOL_SIZE; i++) if (f.kind[i] !== 0) n++
  return n
}

// A typical 16:10 pane with the art centred, and the same pane with the art hidden.
const ASPECT = 1.6
const ART: PaneRect = { x0: 0.25, x1: 0.75, y0: 0.08, y1: 0.88 }

// --- steady state and the on-screen floor ----------------------------------------

test('ambient count settles near its target and never empties the pane', () => {
  const f = new SparkField(seeded(1))
  f.setBounds(ASPECT, null)
  run(f, 20) // warm-up
  const target = f.targetCount()
  let min = Infinity
  let max = 0
  for (let s = 0; s < 60; s++) {
    run(f, 1)
    const n = countKind(f, KIND_AMBIENT)
    min = Math.min(min, n)
    max = Math.max(max, n)
  }
  assert.ok(min >= target * 0.7, `min ${min} vs target ${target}`)
  assert.ok(max <= target * 1.3, `max ${max} vs target ${target}`)
})

test('the target scales with the visible area: art shown is sparser than art hidden', () => {
  const f = new SparkField(seeded(2))
  f.setBounds(ASPECT, null)
  const hidden = f.targetCount()
  f.setBounds(ASPECT, ART)
  const shown = f.targetCount()
  assert.ok(shown < hidden)
  assert.ok(shown > 0)
})

test('a pane that is nearly all art keeps a floor and does not run away', () => {
  const f = new SparkField(seeded(3))
  // Narrow pane: the art spans the full width and nearly all the height.
  f.setBounds(0.8, { x0: 0, x1: 1, y0: 0.02, y1: 0.98 })
  const target = f.targetCount()
  assert.ok(target >= 1)
  let spawned = 0
  let prev = live(f)
  for (let s = 0; s < 600; s++) {
    f.tick(DT)
    const n = live(f)
    if (n > prev) spawned += n - prev
    prev = n
  }
  // 20 s: the steady-state rate is target / mean life, capped at twice that.
  const ceiling = (2 * target * 20) / f.meanLife() + 5
  assert.ok(spawned <= ceiling, `spawned ${spawned} > ${ceiling}`)
  assert.ok(live(f) <= POOL_SIZE - RESERVED_SLOTS)
})

test('ambient spawning favours the side strips while the art shows', () => {
  const f = new SparkField(seeded(4))
  f.setBounds(ASPECT, ART)
  run(f, 10)
  let strips = 0
  let behind = 0
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] !== KIND_AMBIENT) continue
    const nx = f.spawnX[i] / ASPECT
    if (nx > ART.x0 && nx < ART.x1) behind++
    else strips++
  }
  assert.ok(strips > behind * 3, `strips ${strips} behind ${behind}`)
})

test('ambient sparks never take the reserved slots', () => {
  const f = new SparkField(seeded(5), { density: 10_000 })
  f.setBounds(ASPECT, null)
  run(f, 20)
  assert.ok(countKind(f, KIND_AMBIENT) <= POOL_SIZE - RESERVED_SLOTS)
  // The burst still gets its sparks.
  f.trackChanged(true)
  f.setPalette(true)
  run(f, 1)
  assert.ok(countKind(f, KIND_BURST) >= Math.min(BURST_COUNT, RESERVED_SLOTS))
})

// --- motion --------------------------------------------------------------------------

test('a placed spark rises and fades in, and the fade lands exactly on 1', () => {
  const f = new SparkField(seeded(6), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.9)
  const y0 = f.y[i]
  assert.equal(f.alpha(i), 0)
  run(f, 1.2)
  assert.ok(f.y[i] < y0, 'rises (y-down)')
  assert.equal(f.fadeIn(i), 1)
})

test('shimmer stays slow: no spark pulses faster than 0.8 Hz', () => {
  assert.ok(SHIMMER_HZ[1] <= 0.8)
  const f = new SparkField(seeded(7))
  f.setBounds(ASPECT, null)
  run(f, 10)
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] !== 0) assert.ok(f.shimmerHz[i] <= SHIMMER_HZ[1])
  }
})

test('a long frame gap is clamped, so nothing jumps', () => {
  const f = new SparkField(seeded(8), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.9)
  const y0 = f.y[i]
  f.tick(5) // a stalled renderer
  assert.ok(y0 - f.y[i] < 0.05, `moved ${y0 - f.y[i]}`)
})

test('sparks outside the pane are retired', () => {
  const f = new SparkField(seeded(9), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.02)
  run(f, 3)
  assert.equal(f.kind[i], 0)
})

// --- gust ----------------------------------------------------------------------------

test('a cursor sweep gusts nearby sparks its way, then they curl back upward', () => {
  const moved = new SparkField(seeded(10), { density: 0 })
  const still = new SparkField(seeded(10), { density: 0 })
  for (const f of [moved, still]) f.setBounds(ASPECT, null)
  const a = moved.place(0.8, 0.6)
  const b = still.place(0.8, 0.6)
  moved.pointerMove(0.7, 0.6)
  for (let k = 0; k < 6; k++) {
    moved.pointerMove(0.7 + 0.02 * k, 0.6) // sweeping right
    moved.tick(DT)
    still.tick(DT)
  }
  const dx = moved.x[a] - still.x[b]
  assert.ok(dx > 0.005, `gust pushed ${dx}`)
  // Let the gust decay: lateral push fades, and the spark keeps rising.
  const yMid = moved.y[a]
  moved.pointerLeave()
  run(moved, 3)
  run(still, 3)
  assert.ok(Math.abs(moved.gustX[a]) < 0.01)
  assert.ok(moved.y[a] < yMid)
})

test('sparks out of reach of the cursor are not gusted', () => {
  const f = new SparkField(seeded(11), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(1.4, 0.6)
  f.pointerMove(0.1, 0.6)
  for (let k = 0; k < 6; k++) {
    f.pointerMove(0.1 + 0.02 * k, 0.6)
    f.tick(DT)
  }
  assert.equal(f.gustX[i], 0)
})

test('leaving on one side and re-entering on the other is not a gust', () => {
  const f = new SparkField(seeded(12), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(1.4, 0.6)
  f.pointerMove(0.1, 0.6)
  f.tick(DT)
  f.pointerLeave()
  f.pointerMove(1.42, 0.6) // re-entry right next to the spark, between two frames
  f.tick(DT)
  f.tick(DT)
  assert.equal(f.gustX[i], 0)
  assert.equal(f.gustY[i], 0)
})

test('a resume does not gust from a stale cursor position', () => {
  const f = new SparkField(seeded(13), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(1.4, 0.6)
  f.pointerMove(0.1, 0.6)
  f.tick(DT)
  f.resume()
  f.pointerMove(1.42, 0.6)
  f.tick(DT)
  assert.equal(f.gustX[i], 0)
})

// --- track-change burst --------------------------------------------------------------

test('a burst waits for the new palette and rises on its generation', () => {
  const f = new SparkField(seeded(20), { density: 0 })
  f.setBounds(ASPECT, null)
  const oldGen = f.generation
  f.trackChanged(true)
  run(f, 0.2)
  assert.equal(countKind(f, KIND_BURST), 0, 'held until the palette lands')
  const gen = f.setPalette(true)
  assert.ok(gen > oldGen)
  run(f, 1)
  const n = countKind(f, KIND_BURST)
  assert.ok(n >= BURST_COUNT * 0.9, `burst ${n}`)
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] === KIND_BURST) {
      assert.equal(f.gen[i], gen)
      assert.ok(f.spawnY[i] >= 0.95, 'from the bottom of the pane')
    }
  }
})

test('a same-album track change (no new palette) still bursts after the fallback', () => {
  const f = new SparkField(seeded(21), { density: 0 })
  f.setBounds(ASPECT, null)
  f.trackChanged(true)
  run(f, BURST_FALLBACK_S + 0.8)
  assert.ok(countKind(f, KIND_BURST) > 0)
})

test('a palette with no pending track change bursts nothing (mount, theme switch)', () => {
  const f = new SparkField(seeded(22), { density: 0 })
  f.setBounds(ASPECT, null)
  f.setPalette(true)
  run(f, 2)
  assert.equal(countKind(f, KIND_BURST), 0)
})

test('a track change while stopped never bursts', () => {
  const f = new SparkField(seeded(23), { density: 0 })
  f.setBounds(ASPECT, null)
  f.trackChanged(false)
  run(f, 2)
  assert.equal(countKind(f, KIND_BURST), 0)
})

test('resume drops a pending burst so it never fires late', () => {
  const f = new SparkField(seeded(24), { density: 0 })
  f.setBounds(ASPECT, null)
  f.trackChanged(true)
  f.resume()
  f.setPalette(true)
  run(f, 2)
  assert.equal(countKind(f, KIND_BURST), 0)
})

test('a fast double skip queues one burst, not two', () => {
  const f = new SparkField(seeded(25), { density: 0 })
  f.setBounds(ASPECT, null)
  f.trackChanged(true)
  f.setPalette(true)
  f.trackChanged(true)
  f.setPalette(true)
  run(f, 2)
  assert.ok(countKind(f, KIND_BURST) <= BURST_COUNT)
})

// --- palette generations -------------------------------------------------------------

test('while stopped, a new palette rebinds every spark (the still frame lands on it)', () => {
  const f = new SparkField(seeded(30))
  f.setBounds(ASPECT, null)
  run(f, 5)
  const gen = f.setPalette(false)
  for (let i = 0; i < POOL_SIZE; i++) if (f.kind[i] !== 0) assert.equal(f.gen[i], gen)
  assert.equal(f.oldestGeneration(), gen)
})

test('rapid skipping keeps at most MAX_GENERATIONS sheets alive', () => {
  const f = new SparkField(seeded(31))
  f.setBounds(ASPECT, null)
  for (let k = 0; k < 10; k++) {
    run(f, 0.3)
    f.setPalette(true)
  }
  run(f, DT)
  const oldest = f.oldestGeneration()
  assert.ok(f.generation - oldest < MAX_GENERATIONS)
  for (let i = 0; i < POOL_SIZE; i++) if (f.kind[i] !== 0) assert.ok(f.gen[i] >= oldest)
})

// --- the rare pop ----------------------------------------------------------------------

test('the pop waits for its gap and for the cursor to be idle, on the animation clock', () => {
  const f = new SparkField(seeded(40), { density: 0 })
  f.setBounds(ASPECT, null)
  const due = f.nextPopAt
  assert.ok(due >= 45 && due <= 240, `gap ${due}`)
  // Keep the cursor busy past the due time: no pop.
  for (let t = 0; t < due + 2; t += DT) {
    f.pointerMove(0.2, 0.2)
    f.tick(DT)
  }
  assert.equal(countKind(f, KIND_POP), 0)
  f.pointerLeave()
  run(f, 8)
  assert.equal(countKind(f, KIND_POP), 1)
})

test('the pop rises well above where ambient sparks die', () => {
  const f = new SparkField(seeded(41), { density: 0 })
  f.setBounds(ASPECT, null)
  f.forcePop()
  let pop = -1
  for (let i = 0; i < POOL_SIZE; i++) if (f.kind[i] === KIND_POP) pop = i
  assert.ok(pop >= 0)
  let top = f.y[pop]
  for (let t = 0; t < 12 && f.kind[pop] === KIND_POP; t += DT) {
    f.tick(DT)
    top = Math.min(top, f.y[pop])
  }
  assert.ok(f.spawnY[pop] - top > 0.6, `travelled ${f.spawnY[pop] - top}`)
  assert.ok(f.maxAmbientRise() < 0.6 * (f.spawnY[pop] - top) + 0.3)
})

test('only one pop at a time', () => {
  const f = new SparkField(seeded(42), { density: 0 })
  f.setBounds(ASPECT, null)
  f.forcePop()
  f.forcePop()
  assert.equal(countKind(f, KIND_POP), 1)
})

// --- still frame -----------------------------------------------------------------------

test('the still frame is deterministic', () => {
  assert.deepEqual(stillFrame(ASPECT, ART), stillFrame(ASPECT, ART))
  assert.deepEqual(stillFrame(ASPECT, null), stillFrame(ASPECT, null))
})

test('the still frame stays out from behind the art and inside the pane', () => {
  const frame = stillFrame(ASPECT, ART)
  assert.ok(frame.length >= 20)
  for (const s of frame) {
    const nx = s.x / ASPECT
    assert.ok(nx >= 0 && nx <= 1 && s.y >= 0 && s.y <= 1)
    assert.ok(!(nx > ART.x0 && nx < ART.x1 && s.y > ART.y0 && s.y < ART.y1), 'behind the art')
    assert.ok(s.alpha > 0 && s.alpha <= 1)
    assert.ok(s.size >= 0 && s.size < SIZE_BUCKETS)
  }
})

test('with the art hidden the still frame spreads across the pane', () => {
  const frame = stillFrame(ASPECT, null)
  const xs = frame.map((s) => s.x / ASPECT)
  assert.ok(Math.min(...xs) < 0.25 && Math.max(...xs) > 0.75)
  assert.ok(
    xs.some((x) => x > 0.35 && x < 0.65),
    'the centre carries sparks too'
  )
})
