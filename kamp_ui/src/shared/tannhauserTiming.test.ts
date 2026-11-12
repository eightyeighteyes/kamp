// Run with `npm test` (node --test; Node >= 23.6 strips TypeScript natively).
// KAMP-772: the pure model behind the Tannhäuser background. The fog mask is
// real bytes (the exact buffer uploaded to the GPU), so these tests check the
// pixels themselves, not a mock of a drawing API.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  BreathEnvelope,
  DROP_PERIODS_S,
  FOG_FULL,
  FogMask,
  MASK_H,
  MIN_FALLOFF,
  SLIDE_STRIPS,
  SlideEvents,
  StrokeTracker,
  dropPhase,
  maskWidth
} from './tannhauserTiming.ts'

function seeded(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const at = (m: FogMask, x: number, y: number): number => m.data[y * m.w + x]
const minOf = (m: FogMask): number => m.data.reduce((a, b) => Math.min(a, b), 255)

// --- mask size ----------------------------------------------------------------------

test('the mask is a fixed height, with the width following the aspect', () => {
  assert.equal(maskWidth(1), MASK_H)
  assert.equal(maskWidth(16 / 9), Math.round(MASK_H * (16 / 9)))
})

test('the mask width stays bounded at extreme aspects', () => {
  assert.ok(maskWidth(0.01) >= 16)
  assert.ok(maskWidth(100) <= 512)
  assert.ok(maskWidth(Number.NaN) >= 16)
})

// --- fog mask -----------------------------------------------------------------------

test('a new mask is fully fogged and needs its first upload', () => {
  const m = new FogMask(40, 30)
  assert.equal(m.data.length, 40 * 30)
  assert.equal(minOf(m), FOG_FULL)
  assert.equal(m.dirty, true)
})

test('an idle, fully fogged mask is never dirty, so nothing is uploaded', () => {
  const m = new FogMask(40, 30)
  m.markUploaded()
  for (let i = 0; i < 100; i++) m.refill(1 / 20)
  assert.equal(m.dirty, false)
})

test('a stamp clears its centre completely and leaves distant texels fogged', () => {
  const m = new FogMask(64, 48)
  m.markUploaded()
  m.stamp(32, 24, 6)
  assert.equal(at(m, 32, 24), 0)
  assert.equal(at(m, 0, 0), FOG_FULL)
  assert.equal(m.dirty, true)
})

test('a stamp has a soft edge at least MIN_FALLOFF texels wide', () => {
  const m = new FogMask(64, 48)
  m.stamp(32, 24, 1) // tiny radius still gets a soft falloff
  // Walk right from the centre: values must rise through several distinct steps.
  const row = Array.from({ length: MIN_FALLOFF + 2 }, (_, i) => at(m, 32 + i, 24))
  const partial = row.filter((v) => v > 0 && v < FOG_FULL)
  assert.ok(partial.length >= MIN_FALLOFF - 1, `edge too hard: ${row.join(',')}`)
  for (let i = 1; i < row.length; i++) assert.ok(row[i] >= row[i - 1], 'falloff is monotonic')
})

test('stamps min-blend: a second stamp never adds fog back', () => {
  const m = new FogMask(64, 48)
  m.stamp(30, 24, 6)
  const before = Uint8Array.from(m.data)
  m.stamp(36, 24, 6)
  for (let i = 0; i < m.data.length; i++) assert.ok(m.data[i] <= before[i])
})

test('a stamp outside the mask is clipped, not wrapped', () => {
  const m = new FogMask(20, 20)
  m.stamp(-3, 10, 4)
  // Nothing on the far right edge, which a row-wrap would have touched.
  for (let y = 0; y < 20; y++) assert.equal(at(m, 19, y), FOG_FULL)
})

test('refill is linear and lands exactly on full, with no 8-bit stall', () => {
  const m = new FogMask(16, 16)
  m.stamp(8, 8, 3)
  const fps = 20
  const frames = Math.ceil(FogMask.REFILL_S * fps) + 2
  for (let i = 0; i < frames; i++) m.refill(1 / fps)
  assert.equal(minOf(m), FOG_FULL)
})

test('refill from clear takes about REFILL_S, not much sooner', () => {
  const m = new FogMask(8, 8)
  m.clear()
  for (let i = 0; i < FogMask.REFILL_S * 20 * 0.9; i++) m.refill(1 / 20)
  assert.ok(minOf(m) < FOG_FULL, 'refogged too fast')
  assert.ok(minOf(m) > FOG_FULL * 0.8)
})

test('refill accumulates sub-level steps instead of dropping them', () => {
  const m = new FogMask(4, 4)
  m.clear()
  // At 240 Hz each step is ~0.1 of a level; a per-frame floor() would never move.
  for (let i = 0; i < 240; i++) m.refill(1 / 240)
  assert.ok(minOf(m) > 0)
})

test('refill clamps an oversized dt', () => {
  const m = new FogMask(4, 4)
  m.clear()
  m.refill(30)
  assert.ok(minOf(m) < 10, 'a stalled renderer must not refog in one frame')
})

test('fill restores full fog and marks dirty', () => {
  const m = new FogMask(16, 16)
  m.stamp(8, 8, 4)
  m.markUploaded()
  m.fill()
  assert.equal(minOf(m), FOG_FULL)
  assert.equal(m.dirty, true)
})

test('a resize keeps a wipe in the same relative place, with no reset', () => {
  const m = new FogMask(40, 20)
  m.stamp(10, 10, 4)
  m.resize(80, 20)
  assert.equal(m.w, 80)
  assert.equal(m.data.length, 80 * 20)
  assert.ok(at(m, 20, 10) < 64, 'the wipe moved or vanished')
  assert.equal(at(m, 70, 10), FOG_FULL)
  assert.equal(m.dirty, true)
})

test('a resize to the same size is a no-op', () => {
  const m = new FogMask(40, 20)
  m.markUploaded()
  const data = m.data
  m.resize(40, 20)
  assert.equal(m.data, data)
  assert.equal(m.dirty, false)
})

test('a stroke wipes a continuous path, not a dotted one', () => {
  const m = new FogMask(100, 40)
  m.stroke(10, 20, 90, 20, 3)
  for (let x = 10; x <= 90; x++) assert.ok(at(m, x, 20) < 32, `gap at x=${x}`)
})

test('a very long stroke is capped in stamps but still reaches its end', () => {
  const m = new FogMask(512, 96)
  const n = m.stroke(0, 48, 511, 48, 2)
  assert.ok(n <= FogMask.MAX_STAMPS)
  assert.ok(at(m, 511, 48) < 32)
})

// --- stroke tracking ------------------------------------------------------------------

test('the first sample of an epoch only anchors, it never draws', () => {
  const s = new StrokeTracker()
  assert.equal(s.next({ x: 0.2, y: 0.1 }, 1), null)
})

test('movement within an epoch draws from the previous sample', () => {
  const s = new StrokeTracker()
  s.next({ x: 0, y: 0 }, 1)
  assert.deepEqual(s.next({ x: 0.5, y: -0.5 }, 1), { ax: 0, ay: 0, bx: 0.5, by: -0.5 })
})

test('a still cursor draws nothing', () => {
  const s = new StrokeTracker()
  s.next({ x: 0.3, y: 0.3 }, 1)
  assert.equal(s.next({ x: 0.3, y: 0.3 }, 1), null)
})

test('a new epoch (re-entry) never joins the exit point to the entry point', () => {
  const s = new StrokeTracker()
  s.next({ x: -0.9, y: 0 }, 1)
  s.next({ x: -1, y: 0 }, 1) // exits on the left
  // Re-enters on the right before the next frame.
  assert.equal(s.next({ x: 1, y: 0 }, 2), null)
  assert.deepEqual(s.next({ x: 0.9, y: 0 }, 2), { ax: 1, ay: 0, bx: 0.9, by: 0 })
})

test('reset drops the anchor', () => {
  const s = new StrokeTracker()
  s.next({ x: 0, y: 0 }, 1)
  s.reset()
  assert.equal(s.next({ x: 0.5, y: 0 }, 1), null)
})

// --- breath (track change) -------------------------------------------------------------

/** Drive a breath and record its uniforms each frame. */
function run(
  b: BreathEnvelope,
  seconds: number,
  dt = 1 / 30
): { commits: number; ps: number[]; bs: number[] } {
  let commits = 0
  const ps: number[] = []
  const bs: number[] = []
  for (let t = 0; t < seconds; t += dt) {
    if (b.tick(dt)) commits++
    ps.push(b.progress)
    bs.push(b.amount)
  }
  return { commits, ps, bs }
}

test('an idle breath shows nothing', () => {
  const b = new BreathEnvelope()
  assert.equal(b.active, false)
  assert.equal(b.amount, 0)
})

test('a breath grows outward monotonically and commits exactly once', () => {
  const b = new BreathEnvelope()
  b.trigger()
  const { commits, ps } = run(b, BreathEnvelope.GROW_S + BreathEnvelope.FADE_S + 1)
  assert.equal(commits, 1)
  for (let i = 1; i < ps.length; i++) assert.ok(ps[i] >= ps[i - 1] || ps[i] === 0)
  assert.equal(b.active, false)
})

test('the breath commits only once its front has covered the pane', () => {
  const b = new BreathEnvelope()
  b.trigger()
  for (let t = 0; t < 5; t += 1 / 30) {
    if (b.tick(1 / 30)) {
      assert.equal(b.progress, 1)
      assert.equal(b.amount, 1)
      return
    }
  }
  assert.fail('never committed')
})

test('a skip mid-growth continues the bloom, never restarts it', () => {
  const b = new BreathEnvelope()
  b.trigger()
  run(b, BreathEnvelope.GROW_S * 0.5)
  const mid = b.progress
  assert.ok(mid > 0 && mid < 1)
  b.trigger()
  b.tick(1 / 30)
  assert.ok(b.progress >= mid)
})

test('a skip while fading rises from the current amount and commits again', () => {
  const b = new BreathEnvelope()
  b.trigger()
  run(b, BreathEnvelope.GROW_S + BreathEnvelope.FADE_S * 0.5)
  const fading = b.amount
  assert.ok(fading > 0 && fading < 1)
  b.trigger()
  b.tick(1 / 30)
  assert.ok(b.amount >= fading, 'amount dropped on re-trigger')
  assert.ok(b.amount - fading < 0.2, 'amount jumped on re-trigger')
  assert.equal(b.progress, 1, 'the front must not shrink back to the centre')
  const { commits } = run(b, BreathEnvelope.RISE_S + 0.2)
  assert.equal(commits, 1)
})

test('what the breath shows never jumps, whatever the frame timing', () => {
  // The front's reach is progress and its strength amount. A fresh trigger sets
  // amount to 1 at progress 0, which shows nothing, so the visible quantity is
  // their product; progress itself only drops (to 0) once amount is already 0.
  const b = new BreathEnvelope()
  const rng = seeded(7)
  let shown = 0
  let p = 0
  for (let i = 0; i < 3000; i++) {
    if (rng() < 0.01) b.trigger()
    const dt = [0, 1 / 60, 1 / 30, 0.1, 1][Math.floor(rng() * 5)]
    b.tick(dt)
    const now = b.progress * b.amount
    assert.ok(Math.abs(now - shown) <= BreathEnvelope.MAX_STEP + 1e-9, `jump ${shown}->${now}`)
    if (b.progress !== 0) assert.ok(b.progress >= p - 1e-12, 'the front shrank')
    shown = now
    p = b.progress
  }
})

test('cancel drops a breath in flight', () => {
  const b = new BreathEnvelope()
  b.trigger()
  b.tick(0.2)
  b.cancel()
  assert.equal(b.active, false)
  assert.equal(b.amount, 0)
})

// --- rare slide -----------------------------------------------------------------------

function due(
  s: SlideEvents,
  opts: { idle?: number; breathing?: boolean; artShown?: number } = {}
): void {
  s.forceNext()
  s.tick(1 / 30, 1000, opts.idle ?? 60, opts.breathing ?? false, opts.artShown ?? 1)
}

test('a slide never starts before its gap is due', () => {
  const s = new SlideEvents(seeded(1))
  s.tick(1 / 30, 0, 60, false, 1)
  assert.equal(s.active, null)
  assert.ok(s.nextAt >= SlideEvents.GAP_FLOOR_S)
})

test('a due slide waits for a still cursor and for any breath to finish', () => {
  const s = new SlideEvents(seeded(2))
  due(s, { idle: 1 })
  assert.equal(s.active, null)
  due(s, { breathing: true })
  assert.equal(s.active, null)
  due(s)
  assert.ok(s.active)
})

test('with the art shown, a slide lands in a side strip', () => {
  for (let seed = 1; seed < 40; seed++) {
    const s = new SlideEvents(seeded(seed))
    due(s, { artShown: 1 })
    const x = s.active!.x
    assert.ok(
      SLIDE_STRIPS.some(([lo, hi]) => x >= lo && x <= hi),
      `x=${x} is behind the art`
    )
  }
})

test('a slide only ever moves down, and stops', () => {
  const s = new SlideEvents(seeded(3))
  due(s)
  const e = s.active!
  let y = SlideEvents.pos(e, 0).y
  for (let age = 0; age <= e.life; age += 0.05) {
    const p = SlideEvents.pos(e, age)
    assert.equal(p.x, e.x)
    assert.ok(p.y >= y - 1e-12)
    y = p.y
  }
  assert.ok(Math.abs(SlideEvents.pos(e, e.life).y - (e.y + e.dist)) < 1e-9)
})

test('the trail follows exactly the path the drop head is drawn on', () => {
  const s = new SlideEvents(seeded(4))
  due(s)
  const e = s.active!
  const segs: { ay: number; by: number }[] = []
  for (let i = 0; i < 200 && s.active; i++) {
    const seg = s.tick(1 / 30, 1000, 60, false, 1)
    if (seg) segs.push(seg)
  }
  assert.ok(segs.length > 0, 'no trail')
  // Contiguous: each segment starts where the last one ended.
  for (let i = 1; i < segs.length; i++) assert.equal(segs[i].ay, segs[i - 1].by)
  assert.ok(Math.abs(segs[segs.length - 1].by - (e.y + e.dist)) < 1e-9)
})

test('a slide head fades in and out with zero value at both ends', () => {
  const s = new SlideEvents(seeded(5))
  due(s)
  const e = s.active!
  assert.equal(SlideEvents.alpha(e, 0), 0)
  assert.ok(SlideEvents.alpha(e, e.life) < 1e-9)
  assert.ok(SlideEvents.alpha(e, e.life * 0.4) > 0.5)
})

test('after a slide ends, the next is scheduled a full gap later', () => {
  const s = new SlideEvents(seeded(6))
  due(s)
  for (let i = 0; i < 400 && s.active; i++) s.tick(1 / 30, 1000 + i / 30, 60, false, 1)
  assert.equal(s.active, null)
  assert.ok(s.nextAt >= 1000 + SlideEvents.GAP_FLOOR_S)
})

test('cancel drops a slide in flight', () => {
  const s = new SlideEvents(seeded(8))
  due(s)
  s.cancel(50)
  assert.equal(s.active, null)
  assert.ok(s.nextAt >= 50 + SlideEvents.GAP_FLOOR_S)
})

// --- drop clock -----------------------------------------------------------------------

test('drop phases are 0..1 and periodic in their own periods', () => {
  for (const t of [0, 3.3, 100, 36000.7]) {
    const [a, b] = dropPhase(t)
    assert.ok(a >= 0 && a < 1 && b >= 0 && b < 1)
    const [a2] = dropPhase(t + DROP_PERIODS_S[0])
    const [, b2] = dropPhase(t + DROP_PERIODS_S[1])
    assert.ok(Math.abs(a - a2) < 1e-6 || Math.abs(Math.abs(a - a2) - 1) < 1e-6)
    assert.ok(Math.abs(b - b2) < 1e-6 || Math.abs(Math.abs(b - b2) - 1) < 1e-6)
  }
})

test('drop phases advance smoothly even ten hours in', () => {
  const t = 36000
  const [a0, b0] = dropPhase(t)
  const [a1, b1] = dropPhase(t + 1 / 30)
  const step = (x: number, y: number): number => Math.min(Math.abs(y - x), 1 - Math.abs(y - x))
  assert.ok(step(a0, a1) < 0.01)
  assert.ok(step(b0, b1) < 0.01)
})
