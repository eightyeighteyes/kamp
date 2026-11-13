// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-775: the pure layout and timing logic behind the Chapel background.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  FIELD_COUNT,
  FieldWarmth,
  OFFSET_MAX,
  ProportionTween,
  SeamBloom,
  SeamWarmth,
  Warmth,
  breathedWeights,
  fieldColors,
  layoutFields,
  makeBreaths,
  randomOffsets,
  randomProportions,
  titleBandAlpha
} from './chapelLayout.ts'

// Deterministic RNG (mulberry32) so the random parts of the layout are reproducible.
function seeded(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const sum = (xs: number[]): number => xs.reduce((a, b) => a + b, 0)
const close = (a: number, b: number, eps = 1e-9): boolean => Math.abs(a - b) <= eps

function assertStacked(w: number, h: number, weights: number[]): void {
  const { fields, seams, feather } = layoutFields(w, h, weights)
  assert.equal(fields.length, weights.length)
  assert.equal(seams.length, weights.length - 1)
  for (let i = 0; i < fields.length; i++) {
    assert.ok(fields[i].h > 0, `field ${i} collapsed`)
    if (i > 0) {
      const gap = fields[i].y - (fields[i - 1].y + fields[i - 1].h)
      // Each edge feathers over `feather` centred on the edge, so a gap of at
      // least one feather keeps neighbouring fades from overlapping into mud.
      assert.ok(gap >= feather - 1e-9, `gap ${gap} narrower than feather ${feather}`)
      assert.ok(seams[i - 1] > fields[i - 1].y + fields[i - 1].h && seams[i - 1] < fields[i].y)
    }
  }
}

// --- layout -------------------------------------------------------------------------

test('fields stack top to bottom without overlapping', () => {
  assertStacked(1200, 800, [1, 1, 1])
  assertStacked(1200, 800, [0.6, 1.4, 1])
})

test('field heights sum to the same total whatever the proportions', () => {
  const a = layoutFields(1000, 700, [1, 1, 1])
  const b = layoutFields(1000, 700, [0.5, 2, 1.3])
  assert.ok(close(sum(a.fields.map((f) => f.h)), sum(b.fields.map((f) => f.h)), 1e-6))
})

test('breathing at its extremes keeps the total height and never overlaps', () => {
  const breaths = makeBreaths(seeded(1))
  const base = [0.7, 1.3, 1]
  const total = sum(layoutFields(1000, 700, base).fields.map((f) => f.h))
  for (let t = 0; t < 120; t += 0.37) {
    const w = breathedWeights(base, breaths, t)
    assertStacked(1000, 700, w)
    assert.ok(close(sum(layoutFields(1000, 700, w).fields.map((f) => f.h)), total, 1e-6))
  }
})

test('breathing periods fall in the 20-40 second band', () => {
  for (const b of makeBreaths(seeded(7))) {
    assert.ok(b.period >= 20 && b.period <= 40, `period ${b.period}`)
  }
})

test('a still frame (no breath) is the composition at rest', () => {
  const base = [0.8, 1.2, 1]
  assert.deepEqual(breathedWeights(base, null, 123), base)
})

test('without offsets the fields are centred and share one x-range', () => {
  const { fields } = layoutFields(1200, 800, [1, 1, 1])
  for (const f of fields) {
    assert.equal(f.x, fields[0].x)
    assert.equal(f.w, fields[0].w)
    assert.ok(close(f.x + f.w / 2, 600))
  }
})

// --- horizontal offsets (KAMP-781) -----------------------------------------------------

test('an offset moves its field by exactly that fraction of the width: the range is reachable', () => {
  const centred = layoutFields(1200, 800, [1, 1, 1]).fields
  const moved = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0]).fields
  assert.ok(close(moved[0].x - centred[0].x, OFFSET_MAX * 1200))
  assert.ok(close(moved[1].x - centred[1].x, -OFFSET_MAX * 1200))
  assert.ok(close(moved[2].x, centred[2].x))
  assert.equal(moved[0].w, centred[0].w)
})

test('at the extreme offsets no field or its feather leaves the pane, at any aspect', () => {
  for (const [w, h] of [
    [1200, 800],
    [2400, 600],
    [600, 1400],
    [4000, 120],
    [120, 4000]
  ]) {
    for (const off of [OFFSET_MAX, -OFFSET_MAX]) {
      const { fields, featherX } = layoutFields(w, h, [1, 1, 1], [off, off, off])
      for (const f of fields) {
        assert.ok(f.x - featherX / 2 >= -1e-9, `${w}x${h} off ${off}: left ${f.x - featherX / 2}`)
        assert.ok(f.x + f.w + featherX / 2 <= w + 1e-9, `${w}x${h} off ${off}: right`)
      }
    }
  }
})

test('an offset past the range is held at the bound', () => {
  const a = layoutFields(1200, 800, [1, 1, 1], [1, 0, 0]).fields[0]
  const b = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, 0, 0]).fields[0]
  assert.equal(a.x, b.x)
})

test('random offsets stay in range and always stagger the stack', () => {
  const rng = seeded(9)
  for (let i = 0; i < 200; i++) {
    const o = randomOffsets(rng)
    assert.equal(o.length, FIELD_COUNT)
    for (const x of o) assert.ok(Math.abs(x) <= OFFSET_MAX, `offset ${x}`)
    // Bold, not timid: the fields never line up.
    assert.ok(Math.max(...o) - Math.min(...o) >= OFFSET_MAX * 0.6, `spread of ${o}`)
  }
})

test('a seam spans both fields it divides, so its glow covers a stagger', () => {
  const { fields, seamX } = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0])
  assert.equal(seamX.length, 2)
  assert.ok(close(seamX[0].x0, Math.min(fields[0].x, fields[1].x)))
  assert.ok(close(seamX[0].x1, Math.max(fields[0].x + fields[0].w, fields[1].x + fields[1].w)))
})

test('degenerate panes give no fields rather than negative geometry', () => {
  assert.equal(layoutFields(0, 0, [1, 1, 1]).fields.length, 0)
  assert.equal(layoutFields(800, 0, [1, 1, 1]).fields.length, 0)
  assert.equal(layoutFields(800, 3, [1, 1, 1]).fields.length, 0)
  assertStacked(4000, 120, [1, 1, 1])
  assertStacked(120, 4000, [1, 1, 1])
})

// --- proportions on track change -------------------------------------------------------

test('random proportions stay within bounds so no field dominates or vanishes', () => {
  const rng = seeded(3)
  for (let i = 0; i < 200; i++) {
    const p = randomProportions(rng)
    assert.equal(p.length, FIELD_COUNT)
    const share = p.map((x) => x / sum(p))
    for (const s of share) assert.ok(s > 0.18 && s < 0.5, `share ${s}`)
  }
})

test('a retarget while stopped lands on the new proportions immediately', () => {
  // KAMP-779 extended to proportions: nothing ticks a stopped engine, so a
  // still frame would otherwise keep the previous track's layout.
  const p = new ProportionTween([1, 1, 1], 6000)
  p.set([2, 1, 1], false)
  assert.deepEqual(p.current(), [2, 1, 1])
})

test('a retarget while running eases toward the new proportions', () => {
  const p = new ProportionTween([1, 1, 1], 6000)
  p.set([2, 1, 1], true)
  assert.deepEqual(p.current(), [1, 1, 1])
  p.tick(3000)
  const mid = p.current()[0]
  assert.ok(mid > 1 && mid < 2, `mid ${mid}`)
  p.tick(3000)
  assert.deepEqual(p.current(), [2, 1, 1])
})

test('a retarget mid-tween continues from what is on screen, without a jump', () => {
  const p = new ProportionTween([1, 1, 1], 6000)
  p.set([2, 1, 1], true)
  p.tick(2000)
  const onScreen = p.current()
  p.set([1, 1, 2], true)
  assert.deepEqual(p.current(), onScreen)
})

// --- seam warmth (cursor) ------------------------------------------------------------

const SEAMS = [300, 600]

function warmFor(fps: number, seconds: number): number {
  const w = new SeamWarmth(2)
  w.aim(310, SEAMS, 100)
  const steps = Math.round(seconds * fps)
  for (let i = 0; i < steps; i++) w.tick(1 / fps)
  return w.values()[0]
}

test('the seam nearest the cursor warms and the others stay cool', () => {
  const w = new SeamWarmth(2)
  w.aim(310, SEAMS, 100)
  for (let i = 0; i < 60; i++) w.tick(0.1)
  assert.ok(w.values()[0] > 0.8)
  assert.equal(w.values()[1], 0)
})

test('a cursor far from every seam warms nothing', () => {
  const w = new SeamWarmth(2)
  w.aim(450, SEAMS, 100)
  for (let i = 0; i < 60; i++) w.tick(0.1)
  assert.deepEqual(w.values(), [0, 0])
})

test('warmth follows the same curve at 15 and 60 fps', () => {
  assert.ok(Math.abs(warmFor(15, 2) - warmFor(60, 2)) < 1e-6)
})

test('warmth reaches about 63% of its target after one attack time constant', () => {
  const w = new SeamWarmth(2)
  w.aim(300, SEAMS, 100) // dead on the seam: target 1
  w.tick(SeamWarmth.ATTACK_S)
  assert.ok(Math.abs(w.values()[0] - (1 - Math.exp(-1))) < 1e-9)
})

test('leaving the pane cools the seam gradually instead of snapping', () => {
  const w = new SeamWarmth(2)
  w.aim(300, SEAMS, 100)
  for (let i = 0; i < 100; i++) w.tick(0.1)
  const hot = w.values()[0]
  w.aim(null, SEAMS, 100)
  w.tick(1 / 15)
  const after = w.values()[0]
  assert.ok(after < hot && after > hot * 0.9, `snapped from ${hot} to ${after}`)
  w.tick(SeamWarmth.RELEASE_S)
  assert.ok(Math.abs(w.values()[0] - hot * Math.exp(-1 - 1 / 15 / SeamWarmth.RELEASE_S)) < 1e-6)
})

test('cooling is slower than warming', () => {
  assert.ok(SeamWarmth.RELEASE_S > SeamWarmth.ATTACK_S)
})

test('clear() drops all warmth, for a resume that must not replay a stale glow', () => {
  const w = new SeamWarmth(2)
  w.aim(300, SEAMS, 100)
  for (let i = 0; i < 30; i++) w.tick(0.1)
  w.clear()
  assert.deepEqual(w.values(), [0, 0])
  w.tick(1)
  assert.deepEqual(w.values(), [0, 0])
})

// --- field warmth (cursor, KAMP-781) --------------------------------------------------

function heldOn(x: number, y: number, titleTop: number | null = null, seconds = 10): number[] {
  const { fields } = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0])
  const w = new FieldWarmth(FIELD_COUNT)
  for (let i = 0; i < seconds * 15; i++) {
    w.aim(x, y, fields, titleTop)
    w.tick(1 / 15)
  }
  return w.values()
}

test('the field under the cursor warms, and only that one', () => {
  const { fields } = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0])
  const f = fields[1]
  const v = heldOn(f.x + f.w / 2, f.y + f.h / 2)
  assert.ok(v[1] > 0.9, `field ${v[1]}`)
  assert.equal(v[0], 0)
  assert.equal(v[2], 0)
})

test('a cursor in the gap between fields warms no field', () => {
  const { fields, seams } = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0])
  assert.deepEqual(heldOn(fields[0].x + fields[0].w / 2, seams[0]), [0, 0, 0])
})

test('a cursor in the side margin warms no field', () => {
  assert.deepEqual(heldOn(5, 400), [0, 0, 0])
})

test('the hit test follows an offset field, not the centred stack', () => {
  const { fields } = layoutFields(1200, 800, [1, 1, 1], [OFFSET_MAX, -OFFSET_MAX, 0])
  // Field 0 is pushed right: just past its left edge is outside it.
  const f = fields[0]
  assert.deepEqual(heldOn(f.x - 5, f.y + f.h / 2), [0, 0, 0])
  assert.ok(heldOn(f.x + f.w - 5, f.y + f.h / 2)[0] > 0.9)
})

test('no field warms behind the title band, to keep the title legible', () => {
  const { fields } = layoutFields(1200, 800, [1, 1, 1])
  const f = fields[2]
  const y = f.y + f.h / 2
  assert.deepEqual(heldOn(f.x + f.w / 2, y, y - 10), [0, 0, 0])
  assert.ok(heldOn(f.x + f.w / 2, y, y + 10)[2] > 0.9, 'above the band it still warms')
})

test('a field warms on the shared attack and cools on the shared release', () => {
  const { fields } = layoutFields(1200, 800, [1, 1, 1])
  const f = fields[0]
  const w = new FieldWarmth(FIELD_COUNT)
  w.aim(f.x + 10, f.y + 10, fields, null)
  w.tick(Warmth.ATTACK_S)
  assert.ok(Math.abs(w.values()[0] - (1 - Math.exp(-1))) < 1e-9)
  for (let i = 0; i < 100; i++) w.tick(0.1)
  const hot = w.values()[0]
  // Leaving: the same field cools; nothing near the pane's centre picks it up.
  w.aim(null, null, fields, null)
  w.tick(Warmth.RELEASE_S)
  assert.ok(Math.abs(w.values()[0] - hot * Math.exp(-1)) < 1e-9)
  assert.equal(w.values()[1], 0)
})

// --- rare seam bloom -------------------------------------------------------------------

test('the gap before a bloom is bounded and deterministic for a given RNG', () => {
  const a = new SeamBloom(seeded(11))
  const b = new SeamBloom(seeded(11))
  assert.equal(a.nextAt, b.nextAt)
  for (let s = 0; s < 200; s++) {
    const g = new SeamBloom(seeded(s)).nextAt
    assert.ok(g >= SeamBloom.GAP_FLOOR_S && g <= SeamBloom.GAP_MAX_S, `gap ${g}`)
  }
})

test('a due bloom waits for the pointer to be idle', () => {
  const bloom = new SeamBloom(seeded(5))
  const due = bloom.nextAt
  bloom.tick(0, due + 1, 0, 2) // pointer just moved
  assert.equal(bloom.active, null)
  bloom.tick(0, due + 1, SeamBloom.IDLE_S, 2)
  assert.notEqual(bloom.active, null)
  assert.ok(bloom.active!.seam >= 0 && bloom.active!.seam < 2)
})

test('a bloom rises and recedes, then schedules the next one', () => {
  const bloom = new SeamBloom(seeded(5))
  let t = bloom.nextAt
  bloom.tick(0, t, 999, 2)
  const life = bloom.active!.life
  assert.equal(bloom.envelope(), 0)
  bloom.tick(life / 2, (t += life / 2), 999, 2)
  assert.ok(bloom.envelope() > 0.5)
  bloom.tick(life, (t += life), 999, 2)
  assert.equal(bloom.active, null)
  assert.ok(bloom.nextAt >= t + SeamBloom.GAP_FLOOR_S)
})

test('cancel() drops an in-flight bloom so it never replays late', () => {
  const bloom = new SeamBloom(seeded(5))
  bloom.tick(0, bloom.nextAt, 999, 2)
  assert.notEqual(bloom.active, null)
  bloom.cancel(bloom.nextAt)
  assert.equal(bloom.active, null)
})

test('no seams means no bloom', () => {
  const bloom = new SeamBloom(seeded(5))
  bloom.tick(0, bloom.nextAt + 1, 999, 0)
  assert.equal(bloom.active, null)
})

// --- colors --------------------------------------------------------------------------

const DARK = { r: 30, g: 30, b: 90 }
const MID = { r: 120, g: 60, b: 40 }
const LIGHT = { r: 230, g: 200, b: 120 }

test('field colors are the dominant swatches ordered lightest at the top', () => {
  assert.deepEqual(fieldColors([DARK, LIGHT, MID, { r: 255, g: 255, b: 255 }]), [LIGHT, MID, DARK])
})

test('a short palette repeats so there is always a color per field', () => {
  const c = fieldColors([MID])
  assert.equal(c.length, FIELD_COUNT)
  for (const x of c) assert.deepEqual(x, MID)
})

test('an empty palette yields no field colors (the engine uses the accent)', () => {
  assert.deepEqual(fieldColors([]), [])
})

// --- legibility -------------------------------------------------------------------------

test('under the title, a field shows at most a quarter of its color over --bg', () => {
  assert.ok(titleBandAlpha() <= 0.25, `effective alpha ${titleBandAlpha()}`)
})
