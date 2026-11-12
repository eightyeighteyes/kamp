import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  CLOSE_GAP_FLOOR_S,
  CLOSE_GAP_MAX_S,
  GUST_FALLBACK_S,
  KIND_CLOSE,
  KIND_DEAD,
  KIND_FLAKE,
  LAYER_NEAR,
  MAX_GENERATIONS,
  NEAR_MAX,
  POOL_SIZE,
  RESERVED_SLOTS,
  SnowField,
  stillFrame
} from './wintermuteModel.ts'
import { seededRng, type PaneRect } from './particleUtil.ts'

const DT = 1 / 30
const ASPECT = 1.6
// A centred square cover, as the Now Playing pane lays it out.
const ART: PaneRect = { x0: 0.25, y0: 0.15, x1: 0.75, y1: 0.85 }

function field(seed = 1, art: PaneRect | null = null): SnowField {
  const f = new SnowField(seededRng(seed))
  f.setBounds(ASPECT, art)
  f.prewarm()
  return f
}

function run(f: SnowField, seconds: number, each?: () => void): void {
  for (let t = 0; t < seconds; t += DT) {
    f.tick(DT)
    each?.()
  }
}

// Takes the field only so call sites read naturally; the predicate closes over it.
function count(_f: SnowField, pred: (i: number) => boolean): number {
  let n = 0
  for (let i = 0; i < POOL_SIZE; i++) if (pred(i)) n++
  return n
}

const flake = (f: SnowField) => (i: number) => f.kind[i] === KIND_FLAKE

/** Flakes on screen and not hidden behind the art. */
function visible(f: SnowField, art: PaneRect | null): number {
  return count(f, (i) => {
    if (f.kind[i] !== KIND_FLAKE) return false
    const nx = f.x[i] / ASPECT
    const ny = f.y[i]
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return false
    return !(art && nx > art.x0 && nx < art.x1 && ny > art.y0 && ny < art.y1)
  })
}

/** Start a gust the way the engine does: a track change, then its palette. */
function gust(f: SnowField): void {
  f.trackChanged(true)
  f.setPalette(true)
}

// --- the floor -----------------------------------------------------------------------

test('prewarm fills the pane to its target, spread from top to bottom', () => {
  const f = field()
  assert.equal(count(f, flake(f)), f.targetCount())
  const thirds = [0, 0, 0]
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] === KIND_FLAKE) thirds[Math.min(2, Math.max(0, Math.floor(f.y[i] * 3)))]++
  }
  for (const n of thirds) assert.ok(n > f.targetCount() / 5, `thirds ${thirds}`)
})

test('the count holds at its target and the pane never thins out', () => {
  const f = field(2, ART)
  const target = f.targetCount()
  let minVisible = Infinity
  run(f, 90, () => {
    minVisible = Math.min(minVisible, visible(f, ART))
  })
  assert.equal(count(f, flake(f)), target)
  assert.ok(minVisible >= target * 0.35, `min visible ${minVisible} of ${target}`)
})

test('the target scales with the visible area, and a pane of art keeps a floor', () => {
  const f = new SnowField(seededRng(3))
  f.setBounds(ASPECT, null)
  const hidden = f.targetCount()
  f.setBounds(ASPECT, ART)
  const shown = f.targetCount()
  assert.ok(shown < hidden, `${shown} < ${hidden}`)
  f.setBounds(ASPECT, { x0: 0, y0: 0, x1: 1, y1: 1 })
  assert.ok(f.targetCount() > 0)
  assert.ok(f.targetCount() <= POOL_SIZE - RESERVED_SLOTS)
})

test('a raised target fills in gradually, with a fade-in, not in one frame', () => {
  const f = field(4, ART)
  const before = count(f, flake(f))
  f.setBounds(ASPECT, null)
  const target = f.targetCount()
  f.tick(DT)
  const afterOne = count(f, flake(f))
  assert.ok(afterOne - before <= 3, `jumped ${before} -> ${afterOne}`)
  run(f, 8)
  assert.equal(count(f, flake(f)), target)
  // Something born in the pane starts transparent.
  const i = f.place(0.5, 0.5, 0)
  assert.equal(f.alpha(i), 0)
})

test('a lowered target thins out only as flakes leave the pane', () => {
  const f = field(5)
  const before = count(f, flake(f))
  f.setBounds(ASPECT, ART)
  f.tick(DT)
  assert.ok(before - count(f, flake(f)) <= 3)
  run(f, 60)
  assert.equal(count(f, flake(f)), f.targetCount())
})

test('the near layer is capped, so large blurred flakes stay few', () => {
  // Dense enough that the near layer's share alone would exceed the cap.
  const f = new SnowField(seededRng(6), { density: 400 })
  f.setBounds(ASPECT, null)
  f.prewarm()
  run(f, 30)
  assert.ok(count(f, (i) => f.kind[i] === KIND_FLAKE && f.layer[i] === LAYER_NEAR) <= NEAR_MAX)
})

test('flakes in one layer fall at different speeds, so they never travel in clumps', () => {
  const f = field(7)
  const speeds: number[] = []
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] === KIND_FLAKE && f.layer[i] === 0) speeds.push(f.fall[i])
  }
  const lo = Math.min(...speeds)
  const hi = Math.max(...speeds)
  assert.ok(hi / lo > 1.3, `spread ${lo}..${hi}`)
})

test('flakes never take the reserved slots, so the close flake always has room', () => {
  const f = new SnowField(seededRng(8), { density: 10_000 })
  f.setBounds(ASPECT, null)
  f.prewarm()
  run(f, 2)
  assert.ok(count(f, flake(f)) <= POOL_SIZE - RESERVED_SLOTS)
  f.forceClose()
  assert.ok(f.closeIndex >= 0)
})

test('a long frame gap is clamped, so nothing jumps', () => {
  const f = field(9)
  const i = f.place(0.5, 0.5, 2)
  const y0 = f.y[i]
  f.tick(5)
  assert.ok(f.y[i] - y0 < 0.05, `moved ${f.y[i] - y0}`)
})

// --- the gust ------------------------------------------------------------------------

test('a gust waits for the new palette, then blows', () => {
  const f = field(10)
  const calm = f.windX()
  f.trackChanged(true)
  run(f, 0.5)
  assert.equal(f.windX(), calm)
  f.setPalette(true)
  run(f, 1.5)
  assert.ok(Math.abs(f.windX() - calm) > 0.1, `wind ${f.windX()}`)
})

test('a same-album track change (no new palette) still gusts after the fallback', () => {
  const f = field(11)
  const calm = f.windX()
  f.trackChanged(true)
  run(f, GUST_FALLBACK_S + 1.5)
  assert.ok(Math.abs(f.windX() - calm) > 0.1)
})

test('a palette with no pending track change does not gust (mount, theme switch)', () => {
  const f = field(12)
  const calm = f.windX()
  f.setPalette(true)
  run(f, 3)
  assert.equal(f.windX(), calm)
})

test('a track change while stopped never gusts', () => {
  const f = field(13)
  const calm = f.windX()
  f.trackChanged(false)
  f.setPalette(false)
  run(f, 3)
  assert.equal(f.windX(), calm)
})

test('resume drops a pending gust so it never fires late', () => {
  const f = field(14)
  const calm = f.windX()
  f.trackChanged(true)
  f.resume()
  run(f, 3)
  assert.equal(f.windX(), calm)
})

test('the wind never jumps: rapid skips and a resume mid-gust all ease', () => {
  const f = field(15)
  let prev = f.windX()
  let maxStep = 0
  const watch = (): void => {
    maxStep = Math.max(maxStep, Math.abs(f.windX() - prev))
    prev = f.windX()
  }
  for (let k = 0; k < 6; k++) {
    gust(f)
    run(f, 0.4, watch)
  }
  f.resume()
  run(f, 6, watch)
  // Peak wind is reached over most of a second, so one frame moves it a little.
  assert.ok(maxStep < 0.03, `step ${maxStep}`)
  assert.ok(f.windX() === 0, 'settles back to the calm drift')
})

test('the gust blows in from one side and refills that side as it empties', () => {
  const f = field(16, ART)
  gust(f)
  run(f, 0.5)
  const sign = Math.sign(f.windX())
  assert.notEqual(sign, 0)
  // The upwind side strip: the one the gust blows flakes out of.
  const upwind = (i: number): boolean => {
    if (f.kind[i] !== KIND_FLAKE) return false
    const nx = f.x[i] / ASPECT
    return f.y[i] > 0 && f.y[i] < 1 && (sign > 0 ? nx > 0 && nx < ART.x0 : nx > ART.x1 && nx < 1)
  }
  const before = count(f, upwind)
  let min = Infinity
  run(f, 4, () => {
    min = Math.min(min, count(f, upwind))
  })
  assert.ok(min >= before * 0.5, `upwind strip fell from ${before} to ${min}`)
  const fromUpwind = sign > 0 ? f.inflow.left : f.inflow.right
  const fromDownwind = sign > 0 ? f.inflow.right : f.inflow.left
  assert.ok(fromUpwind > 0)
  assert.equal(fromDownwind, 0)
})

test('the new colors come in on the gust, from the upwind side', () => {
  const f = field(17)
  gust(f)
  const gen = f.generation
  run(f, 0.5)
  const sign = Math.sign(f.windX())
  run(f, 2)
  let n = 0
  let sumX = 0
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] === KIND_FLAKE && f.gen[i] === gen && f.x[i] >= 0 && f.x[i] <= ASPECT) {
      n++
      sumX += f.x[i]
    }
  }
  assert.ok(n > 5, `only ${n} new-color flakes`)
  const meanX = sumX / n / ASPECT
  if (sign > 0) assert.ok(meanX < 0.5, `mean x ${meanX}`)
  else assert.ok(meanX > 0.5, `mean x ${meanX}`)
})

// --- palette generations -------------------------------------------------------------

test('while stopped, a new palette rebinds every flake at once (KAMP-779)', () => {
  const f = field(18)
  const gen = f.setPalette(false)
  for (let i = 0; i < POOL_SIZE; i++) {
    if (f.kind[i] !== KIND_DEAD) {
      assert.equal(f.gen[i], gen)
      assert.equal(f.dip(i), 1)
    }
  }
})

test('rapid skipping recolors old flakes only at the bottom of a dip, never a snap', () => {
  const f = field(19)
  const gens = new Uint32Array(POOL_SIZE)
  const kinds = new Uint8Array(POOL_SIZE)
  gens.set(f.gen)
  kinds.set(f.kind)
  let snaps = 0
  let dips = 0
  const watch = (): void => {
    for (let i = 0; i < POOL_SIZE; i++) {
      // A respawn may take a new color: it enters from outside the pane.
      if (f.kind[i] === kinds[i] && f.gen[i] !== gens[i] && !f.respawnedThisTick(i)) {
        if (f.dip(i) > 0.05) snaps++
        else dips++
      }
    }
    gens.set(f.gen)
    kinds.set(f.kind)
  }
  for (let k = 0; k < 8; k++) {
    f.setPalette(true)
    run(f, 0.25, watch)
  }
  run(f, 5, watch)
  assert.equal(snaps, 0)
  assert.ok(dips > 0, 'the cap forced some recolors')
  assert.ok(f.oldestGenerationInUse() > f.generation - MAX_GENERATIONS)
})

// --- the cursor ----------------------------------------------------------------------

/** Sweep the cursor horizontally across the pane at `speed` pane heights / s. */
function sweep(f: SnowField, y: number, speed: number, from = 0.2, to = 1.4): void {
  for (let x = from; x <= to; x += speed * DT) {
    f.pointerMove(x, y)
    f.tick(DT)
  }
}

test('a quick sweep sets nearby flakes swirling, and they settle back to falling', () => {
  const f = new SnowField(seededRng(20), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.52, 1)
  sweep(f, 0.5, 2)
  assert.ok(f.swirl[i] > 0.02, `swirl ${f.swirl[i]}`)
  f.pointerLeave()
  run(f, 6)
  assert.ok(f.swirl[i] < 0.002)
  assert.equal(f.swirlSense[i], 0, 'released, so a later sweep can capture it afresh')
})

test('a slow cursor stirs nothing', () => {
  const f = new SnowField(seededRng(21), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.52, 1)
  sweep(f, 0.5, 0.1, 0.7, 0.9)
  assert.equal(f.swirl[i], 0)
})

test('flakes out of reach of the cursor are not stirred', () => {
  const f = new SnowField(seededRng(22), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.95, 1)
  sweep(f, 0.2, 2)
  assert.equal(f.swirl[i], 0)
})

test('a swirl keeps its direction once captured, even when the cursor turns back', () => {
  const f = new SnowField(seededRng(23), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.55, 1)
  let sense = 0
  const watch = (): void => {
    if (f.swirlSense[i] === 0) return
    if (sense === 0) sense = f.swirlSense[i]
    assert.equal(f.swirlSense[i], sense)
  }
  // Out and back along the same line: re-reading the side each frame would flip it.
  for (let x = 0.6; x <= 0.9; x += 2 * DT) {
    f.pointerMove(x, 0.5)
    f.tick(DT)
    watch()
  }
  for (let x = 0.9; x >= 0.6; x -= 2 * DT) {
    f.pointerMove(x, 0.5)
    f.tick(DT)
    watch()
  }
  assert.notEqual(sense, 0)
})

test('flakes on opposite sides of the path swirl in opposite directions (a wake)', () => {
  const f = new SnowField(seededRng(24), { density: 0 })
  f.setBounds(ASPECT, null)
  const above = f.place(0.8, 0.45, 1)
  const below = f.place(0.8, 0.55, 1)
  sweep(f, 0.5, 2, 0.5, 1.1)
  assert.notEqual(f.swirlSense[above], 0)
  assert.equal(f.swirlSense[above], -f.swirlSense[below])
})

test('leaving on one side and re-entering on the other is not a sweep', () => {
  const f = new SnowField(seededRng(25), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.5, 1)
  f.pointerMove(0.02, 0.5)
  f.tick(DT)
  f.pointerLeave()
  f.tick(DT)
  f.pointerMove(0.85, 0.5)
  f.tick(DT)
  assert.equal(f.swirl[i], 0)
})

test('a resume does not sweep from a stale cursor position', () => {
  const f = new SnowField(seededRng(26), { density: 0 })
  f.setBounds(ASPECT, null)
  const i = f.place(0.8, 0.5, 1)
  f.pointerMove(0.05, 0.5)
  f.tick(DT)
  f.resume()
  f.pointerMove(0.82, 0.5)
  f.tick(DT)
  assert.equal(f.swirl[i], 0)
})

// --- the close flake -----------------------------------------------------------------

test('the close flake is rare: its gap respects the floor and the cap', () => {
  for (let seed = 1; seed <= 200; seed++) {
    const f = new SnowField(seededRng(seed))
    assert.ok(f.nextCloseAt >= CLOSE_GAP_FLOOR_S && f.nextCloseAt <= CLOSE_GAP_MAX_S)
  }
})

test('the close flake waits for its gap and for a still cursor', () => {
  const f = field(27)
  const due = f.nextCloseAt
  run(f, due - 1)
  assert.equal(f.closeIndex, -1)
  // Keep the cursor moving past the due time.
  for (let t = 0; t < 3; t += DT) {
    f.pointerMove(0.1 + (t % 1) * 0.01, 0.1)
    f.tick(DT)
  }
  assert.equal(f.closeIndex, -1)
  f.pointerLeave()
  run(f, 7)
  assert.ok(f.closeIndex >= 0)
  assert.equal(f.kind[f.closeIndex], KIND_CLOSE)
})

test('only one close flake at a time', () => {
  const f = field(28)
  f.forceClose()
  const i = f.closeIndex
  f.forceClose()
  assert.equal(f.closeIndex, i)
  assert.equal(
    count(f, (k) => f.kind[k] === KIND_CLOSE),
    1
  )
})

test('the close flake comes into focus beside the art, never behind it', () => {
  for (let seed = 1; seed <= 60; seed++) {
    const f = field(seed, ART)
    f.forceClose()
    const i = f.closeIndex
    assert.ok(i >= 0, `seed ${seed}`)
    let peak = false
    for (let t = 0; t < 30 && f.kind[i] === KIND_CLOSE; t += DT) {
      f.tick(DT)
      if (f.focus(i) === 1) {
        peak = true
        const r = f.closeRadius(i)
        const nx = f.x[i] / ASPECT
        const rx = r / ASPECT
        const overlaps =
          nx + rx > ART.x0 && nx - rx < ART.x1 && f.y[i] + r > ART.y0 && f.y[i] - r < ART.y1
        assert.ok(!overlaps, `seed ${seed}: in focus at ${nx},${f.y[i]}`)
        assert.ok(nx > 0 && nx < 1 && f.y[i] > 0 && f.y[i] < 1, 'on screen')
      }
    }
    assert.ok(peak, `seed ${seed}: focus reached exactly 1`)
  }
})

test('focus starts and ends at exactly zero, and the flake falls out of view', () => {
  const f = field(29)
  f.forceClose()
  const i = f.closeIndex
  assert.equal(f.focus(i), 0)
  let last = 0
  let maxStep = 0
  for (let t = 0; t < 40 && f.kind[i] === KIND_CLOSE; t += DT) {
    f.tick(DT)
    if (f.kind[i] !== KIND_CLOSE) break
    maxStep = Math.max(maxStep, Math.abs(f.focus(i) - last))
    last = f.focus(i)
  }
  assert.notEqual(f.kind[i], KIND_CLOSE, 'it left')
  assert.equal(last, 0)
  assert.ok(maxStep < 0.05, `focus stepped ${maxStep}`)
  assert.equal(f.closeIndex, -1)
})

// --- the still frame -----------------------------------------------------------------

const TITLE: PaneRect = { x0: 0.3, y0: 0.88, x1: 0.7, y1: 0.97 }

test('the still frame is deterministic', () => {
  assert.deepEqual(stillFrame(ASPECT, ART, TITLE), stillFrame(ASPECT, ART, TITLE))
})

test('the still frame stays out from behind the art and the title, inside the pane', () => {
  const s = stillFrame(ASPECT, ART, TITLE)
  assert.ok(s.length > 20)
  for (const p of s) {
    const nx = p.x / ASPECT
    assert.ok(nx >= 0 && nx <= 1 && p.y >= 0 && p.y <= 1)
    for (const r of [ART, TITLE]) {
      assert.ok(!(nx > r.x0 && nx < r.x1 && p.y > r.y0 && p.y < r.y1), `${nx},${p.y}`)
    }
  }
})

test('with the art hidden the still frame spreads across the pane', () => {
  const s = stillFrame(ASPECT, null, null)
  assert.ok(s.some((p) => p.x / ASPECT > 0.4 && p.x / ASPECT < 0.6 && p.y > 0.3 && p.y < 0.7))
})
