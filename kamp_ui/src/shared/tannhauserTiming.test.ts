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
  LIGHTS,
  LIGHT_DIFFUSE,
  LIGHT_DRIFT,
  MASK_H,
  MIN_FALLOFF,
  SLIDE_STRIPS,
  SliderField,
  StrokeTracker,
  dropPhase,
  lightField,
  maskWidth,
  type Slider,
  type Trail
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
  assert.ok(maskWidth(100) <= 1024)
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

test('a stamp with a floor leaves some fog at its centre, and never fogs a clearer texel', () => {
  const m = new FogMask(64, 48)
  m.stamp(32, 24, 6, { floor: 64 })
  assert.equal(at(m, 32, 24), 64)
  m.stamp(10, 10, 4) // fully clear
  m.stamp(10, 10, 4, { floor: 64 })
  assert.equal(at(m, 10, 10), 0)
})

test('a soft stamp is a cone: it starts refogging right away from the centre', () => {
  const hard = new FogMask(64, 48)
  const soft = new FogMask(64, 48)
  hard.stamp(32, 24, 6)
  soft.stamp(32, 24, 6, { soft: true })
  assert.equal(at(hard, 33, 24), 0, 'a hard stamp has a flat clear core')
  assert.ok(at(soft, 33, 24) > 0, 'a soft stamp has no flat core')
  assert.equal(at(soft, 32, 24), 0)
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

// --- sliding drops ----------------------------------------------------------------------

const ASPECT = 16 / 10

/** Run a field for `seconds` with a still cursor and the art hidden; collect per-tick snapshots. */
function runField(
  f: SliderField,
  seconds: number,
  opts: { dt?: number; idle?: number; artShown?: number; t0?: number } = {}
): { trails: Trail[][]; snaps: Slider[][] } {
  const dt = opts.dt ?? 1 / 20
  const trails: Trail[][] = []
  const snaps: Slider[][] = []
  let t = opts.t0 ?? 0
  for (let i = 0; i < seconds / dt; i++) {
    t += dt
    trails.push(f.tick(dt, t, opts.idle ?? 0, opts.artShown ?? 0, ASPECT))
    snaps.push(f.sliders.map((s) => ({ ...s })))
  }
  return { trails, snaps }
}

test('the trickle is steady: a few drops are usually on the move, never too many', () => {
  const f = new SliderField(seeded(11))
  const { snaps } = runField(f, 120)
  let total = 0
  for (const s of snaps) {
    const running = s.filter((d) => !d.heavy && !d.resting && !d.merging)
    assert.ok(running.length <= SliderField.MAX)
    assert.ok(s.length <= SliderField.SLOTS)
    total += running.length
  }
  const mean = total / snaps.length
  assert.ok(mean >= 2 && mean <= SliderField.MAX, `mean running ${mean}`)
})

test('resting drops are always scattered over the glass', () => {
  const f = new SliderField(seeded(31))
  const { snaps } = runField(f, 60)
  const later = snaps.slice(snaps.length / 2)
  const mean = later.reduce((n, s) => n + s.filter((d) => d.resting).length, 0) / later.length
  assert.ok(mean >= SliderField.REST_MAX * 0.5, `mean resting ${mean}`)
})

test('a resting drop sits still and swells, then breaks loose and runs once heavy', () => {
  const f = new SliderField(seeded(32))
  f.pauseSpawning()
  const d = f.spawnRestAt(0.5, 0.3, 0.008)
  runField(f, 5)
  let s = f.sliders.find((x) => x.id === d.id)!
  assert.equal(s.y, 0.3, 'a resting drop moved')
  assert.ok(s.resting)
  assert.ok(s.r > 0.008, 'a resting drop never swelled')
  runField(f, 120)
  s = f.sliders.find((x) => x.id === d.id)!
  assert.ok(!s || !s.resting, 'never broke loose')
  if (s) assert.ok(s.y > 0.3, 'broke loose but never ran')
})

/** Run until `id` starts merging into another drop; return whether it did. */
function mergedWithin(f: SliderField, id: number, seconds: number): boolean {
  for (let i = 0; i < seconds * 20; i++) {
    f.tick(1 / 20, i / 20, 0, 0, ASPECT)
    const s = f.sliders.find((x) => x.id === id)
    if (!s) return false
    if (s.merging) return true
  }
  return false
}

test('a running drop that reaches a resting drop absorbs it and grows', () => {
  const f = new SliderField(seeded(33))
  f.pauseSpawning()
  const runner = f.spawnAt(0.5, 0.2, 0.012)
  const sitter = f.spawnRestAt(0.5, 0.45, 0.009)
  assert.ok(mergedWithin(f, sitter.id, 60), 'the run passed through the resting drop')
  runField(f, 1)
  const s = f.sliders.find((x) => x.id === runner.id)!
  assert.ok(s.r >= Math.hypot(0.012, 0.009) - 0.0005, `r=${s.r}`)
})

test('a running drop is drawn sideways into a drop just beside its path', () => {
  const f = new SliderField(seeded(34))
  f.pauseSpawning()
  f.spawnAt(0.5, 0.2, 0.012)
  // 0.025 pane heights to the side: beyond the merge distance, inside the reach.
  const near = f.spawnRestAt(0.5 + 0.025 / ASPECT, 0.4, 0.009)
  assert.ok(mergedWithin(f, near.id, 60), 'the run never reached the drop beside it')
})

test('a running drop leaves a drop well clear of its path alone', () => {
  const f = new SliderField(seeded(34))
  f.pauseSpawning()
  const runner = f.spawnAt(0.5, 0.2, 0.012)
  const far = f.spawnRestAt(0.5 + 0.12 / ASPECT, 0.4, 0.009)
  for (let i = 0; i < 60 * 20 && f.sliders.some((x) => x.id === runner.id); i++) {
    f.tick(1 / 20, i / 20, 0, 0, ASPECT)
    const s = f.sliders.find((x) => x.id === far.id)
    assert.ok(s && !s.merging, 'a distant drop was pulled in')
  }
})

test('two runs on neighbouring paths combine', () => {
  const f = new SliderField(seeded(35))
  f.pauseSpawning()
  f.spawnAt(0.5, 0.05, 0.016)
  // 0.05 apart: wider than the merge distance plus both meanders, so only the
  // sideways pull can bring them together.
  const lower = f.spawnAt(0.5 + 0.05 / ASPECT, 0.2, 0.008)
  assert.ok(mergedWithin(f, lower.id, 60), 'the two runs never met')
})

test('left to itself, the glass shows runs meeting other drops every few seconds', () => {
  const f = new SliderField(seeded(37))
  const { snaps } = runField(f, 120)
  const merged = new Set<number>()
  for (const s of snaps) for (const d of s) if (d.merging) merged.add(d.id)
  // Nothing else merges here: there is no cursor, so no wipes.
  assert.ok(merged.size >= 15, `only ${merged.size} merges in two minutes`)
})

test('a wipe takes the drops under it, which shrink away rather than pop', () => {
  const f = new SliderField(seeded(36))
  f.pauseSpawning()
  const hit = f.spawnRestAt(0.5, 0.5, 0.01)
  const miss = f.spawnRestAt(0.9, 0.5, 0.01)
  runField(f, 1)
  f.wipe({ ax: 0.4, ay: 0.5, bx: 0.6, by: 0.5 }, 0.05, ASPECT)
  let prev = f.sliders.find((x) => x.id === hit.id)!.r
  for (let i = 0; i < 40; i++) {
    f.tick(1 / 20, i / 20, 0, 0, ASPECT)
    const s = f.sliders.find((x) => x.id === hit.id)
    if (!s) break
    assert.ok(s.merging)
    assert.ok(s.r <= prev + 1e-12)
    prev = s.r
  }
  assert.equal(
    f.sliders.find((x) => x.id === hit.id),
    undefined,
    'the wiped drop stayed'
  )
  assert.ok(
    f.sliders.find((x) => x.id === miss.id && !x.merging),
    'a drop outside the wipe went'
  )
})

test('a new drop forms in place before it breaks loose', () => {
  const f = new SliderField(seeded(12))
  const d = f.spawnAt(0.5, 0.3, 0.012)
  assert.equal(d.alpha, 0)
  const { snaps } = runField(f, SliderField.FORM_S * 0.9)
  const last = snaps[snaps.length - 1].find((s) => s.id === d.id)!
  assert.ok(last.alpha > 0.5)
  assert.equal(last.y, 0.3)
})

test('a drop never jumps, and only ever moves down, whatever the frame timing', () => {
  const f = new SliderField(seeded(13))
  const rng = seeded(99)
  const prev = new Map<number, Slider>()
  let t = 0
  for (let i = 0; i < 4000; i++) {
    const dt = [0, 1 / 60, 1 / 20, 0.1, 1][Math.floor(rng() * 5)]
    t += dt
    f.tick(dt, t, 0, 0, ASPECT)
    const step = Math.min(dt, SliderField.MAX_DT)
    for (const s of f.sliders) {
      const p = prev.get(s.id)
      if (p && !s.merging) {
        assert.ok(s.y >= p.y - 1e-12, 'moved up')
        assert.ok(s.y - p.y <= SliderField.V_CAP * step + 1e-9, `jumped ${s.y - p.y}`)
        assert.ok(Math.abs(s.x - p.x) <= SliderField.V_CAP * step + 1e-9)
        assert.ok(Math.abs(s.alpha - p.alpha) <= SliderField.MAX_ALPHA_STEP + 1e-9)
      }
      prev.set(s.id, { ...s })
    }
  }
})

test('drops move in stick-slip runs, not at a constant speed', () => {
  const f = new SliderField(seeded(14))
  const d = f.spawnAt(0.3, 0.05, 0.014)
  let still = 0
  let moving = 0
  let y = d.y
  for (let i = 0; i < 20 * 20; i++) {
    f.tick(1 / 20, i / 20, 0, 0, ASPECT)
    const s = f.sliders.find((x) => x.id === d.id)
    if (!s || s.merging) break
    if (i / 20 > SliderField.FORM_S) {
      if (s.y - y < 1e-6) still++
      else moving++
    }
    y = s.y
  }
  assert.ok(still > 5, `never stuck (${still})`)
  assert.ok(moving > 5, `never slid (${moving})`)
})

test('a running drop gathers the beads in its path and grows, up to a cap', () => {
  const f = new SliderField(seeded(15))
  const d = f.spawnAt(0.5, 0.0, 0.01)
  runField(f, 20)
  const s = f.sliders.find((x) => x.id === d.id)
  // It may have left the pane or merged; if still here it has grown.
  if (s && !s.merging) assert.ok(s.r > 0.01)
  for (const x of f.sliders) assert.ok(x.r <= SliderField.R_MAX + 1e-9)
})

test('two drops that meet merge, and the survivor keeps their combined area', () => {
  const f = new SliderField(seeded(16))
  f.pauseSpawning()
  const a = f.spawnAt(0.5, 0.5, 0.012)
  const b = f.spawnAt(0.5 + 0.008 / ASPECT, 0.5, 0.009)
  const { snaps } = runField(f, SliderField.FORM_S * 0.9) // still forming, so no growth from travel
  const end = snaps[snaps.length - 1]
  assert.equal(end.length, 1, 'both drops survived')
  const survivor = end[0]
  assert.equal(survivor.id, a.id, 'the larger drop survives')
  const expected = Math.hypot(0.012, 0.009)
  assert.ok(Math.abs(survivor.r - expected) < 0.0015, `r=${survivor.r} want ${expected}`)
  // The smaller one shrank away; it never vanished at full size.
  const bs = snaps.map((s) => s.find((x) => x.id === b.id)).filter((x): x is Slider => !!x)
  const lastB = bs[bs.length - 1]
  assert.ok(lastB.r * lastB.alpha < 0.002, 'the absorbed drop popped out')
  for (let i = 1; i < bs.length; i++) assert.ok(bs[i].r <= bs[i - 1].r + 1e-12)
})

test('the survivor of a merge grows smoothly, not in one step', () => {
  const f = new SliderField(seeded(17))
  f.pauseSpawning()
  const a = f.spawnAt(0.5, 0.5, 0.012)
  f.spawnAt(0.5, 0.505, 0.01)
  let r = a.r
  for (let i = 0; i < 40; i++) {
    f.tick(1 / 20, i / 20, 0, 0, ASPECT)
    const s = f.sliders.find((x) => x.id === a.id)!
    assert.ok(Math.abs(s.r - r) <= SliderField.MAX_R_STEP + 1e-9)
    r = s.r
  }
})

test('each trail is contiguous with its drop and carries its radius', () => {
  const f = new SliderField(seeded(18))
  const { trails, snaps } = runField(f, 60)
  const lastEnd = new Map<number, number>()
  let n = 0
  for (let i = 0; i < trails.length; i++) {
    for (const tr of trails[i]) {
      n++
      assert.ok(tr.by > tr.ay, 'a trail only runs downward')
      const prevBy = lastEnd.get(tr.id)
      if (prevBy !== undefined) assert.ok(Math.abs(tr.ay - prevBy) < 1e-9, 'gap in a trail')
      lastEnd.set(tr.id, tr.by)
      const s = snaps[i].find((x) => x.id === tr.id)
      if (s) assert.equal(tr.by, s.y)
      assert.ok(tr.r > 0)
    }
  }
  assert.ok(n > 0, 'no trails at all')
})

test('a drop that runs off the bottom is removed', () => {
  const f = new SliderField(seeded(19))
  f.pauseSpawning()
  const d = f.spawnAt(0.5, 0.995, 0.02)
  runField(f, 30)
  assert.equal(
    f.sliders.find((x) => x.id === d.id),
    undefined
  )
})

test('a running drop never fades out: it leaves only off the bottom of the pane', () => {
  const f = new SliderField(seeded(22))
  f.pauseSpawning()
  const d = f.spawnAt(0.5, 0.1, 0.012)
  let last: Slider | undefined
  for (let i = 0; i < 20 * 300; i++) {
    f.tick(1 / 20, i / 20, 0, 0, ASPECT)
    const s = f.sliders.find((x) => x.id === d.id)
    if (!s) break
    if (i / 20 > SliderField.FORM_S) assert.equal(s.alpha, 1, `faded at y=${s.y}`)
    last = { ...s }
  }
  assert.equal(
    f.sliders.find((x) => x.id === d.id),
    undefined,
    'still running after 5 minutes'
  )
  assert.ok(last && last.y - last.r > 0.98, `vanished mid-pane at y=${last?.y}`)
})

test('a trail starts narrow where the drop broke loose and widens behind it', () => {
  const f = new SliderField(seeded(23))
  f.pauseSpawning()
  const d = f.spawnAt(0.5, 0.05, 0.014)
  const { trails, snaps } = runField(f, 30)
  const mine = trails.flatMap((ts, i) =>
    ts.filter((t) => t.id === d.id).map((t) => ({ t, s: snaps[i].find((x) => x.id === d.id) }))
  )
  assert.ok(mine.length > 10, 'too few trail segments')
  const first = mine[0].t
  const later = mine[mine.length - 1].t
  assert.ok(first.r < later.r * 0.6, `no taper: ${first.r} vs ${later.r}`)
  for (const { t, s } of mine) if (s) assert.ok(t.r <= s.r * SliderField.TRAIL_R + 1e-12)
})

/**
 * Width, in pane heights, of the clearly cleared band (at least half the
 * possible clearing) that a full-width trail of a drop of radius r leaves in
 * the mask the shader actually samples.
 */
function trailClearWidth(r: number): number {
  const m = new FogMask(maskWidth(1), MASK_H)
  const cx = m.w / 2
  const tr = r * SliderField.TRAIL_R * MASK_H
  m.stroke(cx, MASK_H * 0.2, cx, MASK_H * 0.8, tr, {
    floor: SliderField.TRAIL_FLOOR,
    soft: true
  })
  const half = (FOG_FULL + SliderField.TRAIL_FLOOR) / 2
  const y = Math.round(MASK_H / 2)
  let n = 0
  for (let x = 0; x < m.w; x++) if (at(m, x, y) <= half) n++
  return n / MASK_H
}

test('a trail clears a band in proportion to its drop, not a fixed width', () => {
  const small = trailClearWidth(SliderField.R[0])
  const large = trailClearWidth(SliderField.R_MAX)
  const ratio = SliderField.R_MAX / SliderField.R[0]
  assert.ok(
    large / small > ratio * 0.6,
    `small ${small.toFixed(4)} vs large ${large.toFixed(4)}, want about ${ratio.toFixed(1)}x`
  )
})

test('a trail clears about the drop own width', () => {
  for (const r of [SliderField.R[1], 0.02, SliderField.R_MAX]) {
    const w = trailClearWidth(r)
    // A drop is 2r across; its trail clears most of that, never much more.
    assert.ok(
      w >= 1.0 * r && w <= 2.2 * r,
      `r=${r}: cleared ${w.toFixed(4)} (${(w / r).toFixed(2)}r)`
    )
  }
})

test('with the art shown, drops start in the side strips', () => {
  for (let seed = 1; seed < 20; seed++) {
    const f = new SliderField(seeded(seed))
    runField(f, 20, { artShown: 1, idle: 60 })
    for (const s of f.sliders) {
      assert.ok(
        SLIDE_STRIPS.some(([lo, hi]) => s.x0 >= lo && s.x0 <= hi),
        `x0=${s.x0} is behind the art`
      )
    }
  }
})

test('the heavy drop is rare and waits for a still cursor', () => {
  const f = new SliderField(seeded(20))
  assert.ok(f.nextHeavyAt >= SliderField.HEAVY_GAP_FLOOR_S)
  f.forceHeavy()
  runField(f, 2, { idle: 1 })
  assert.equal(f.sliders.filter((s) => s.heavy).length, 0)
  runField(f, 0.2, { idle: 60 })
  assert.equal(f.sliders.filter((s) => s.heavy).length, 1)
})

test('the heavy drop has its own slot, so it can start when the trickle is full', () => {
  const f = new SliderField(seeded(21))
  f.pauseSpawning()
  for (let i = 0; i < SliderField.MAX; i++) f.spawnAt(0.1 + i * 0.12, 0.1, 0.01)
  f.forceHeavy()
  runField(f, 0.2, { idle: 60 })
  assert.equal(f.sliders.filter((s) => s.heavy).length, 1)
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

// --- lights ---------------------------------------------------------------------------

const xs = (buf: Float32Array): number[] =>
  Array.from({ length: LIGHTS }, (_, i) => buf[i * 4] / ASPECT)

test('lightField: with the art shown, every light sits in a side strip', () => {
  const buf = lightField([1.1, 2.3], 1, ASPECT, new Float32Array(LIGHTS * 4))
  const slack = LIGHT_DRIFT / ASPECT
  for (const x of xs(buf)) {
    const inStrip = SLIDE_STRIPS.some(([a, b]) => x >= a - slack && x <= b + slack)
    assert.ok(inStrip, `light at x=${x} is behind the art`)
  }
})

test('lightField: with the art hidden, the lights spread across the pane', () => {
  const buf = lightField([1.1, 2.3], 0, ASPECT, new Float32Array(LIGHTS * 4))
  assert.ok(
    xs(buf).some((x) => x > 0.3 && x < 0.7),
    'some light should cross the middle'
  )
})

test('lightField: stays on the pane vertically', () => {
  for (const d of [0, 1.7, 3.9, 6.2]) {
    const buf = lightField([d, d * 1.3], 0.5, ASPECT, new Float32Array(LIGHTS * 4))
    for (let i = 0; i < LIGHTS; i++) {
      assert.ok(buf[i * 4 + 1] >= 0.1 - LIGHT_DRIFT && buf[i * 4 + 1] <= 0.9 + LIGHT_DRIFT)
    }
  }
})

test('lightField: the diffuse falloff is a wider copy of the sharp one', () => {
  const buf = lightField([0, 0], 0, ASPECT, new Float32Array(LIGHTS * 4))
  for (let i = 0; i < LIGHTS; i++) {
    const invS = buf[i * 4 + 2]
    const invD = buf[i * 4 + 3]
    assert.ok(invS > 0 && invD > 0)
    assert.ok(Math.abs(invS / invD - LIGHT_DIFFUSE * LIGHT_DIFFUSE) < 1e-3)
  }
})

test('lightField: fills the buffer it is given, so the render loop allocates nothing', () => {
  const buf = new Float32Array(LIGHTS * 4)
  assert.equal(lightField([0, 0], 0, ASPECT, buf), buf)
})
