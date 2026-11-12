import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  CastaliaPlate,
  GRAINS,
  MODES,
  NEIGHBOURS,
  RING_FALLBACK_S,
  SHIMMER_GAP_FLOOR_S,
  SHIMMER_GAP_MAX_S,
  SHIMMER_PEAK,
  SHIMMER_S,
  VMAX,
  albumMode,
  chladni,
  chladniGrad,
  fnv1a,
  harmonicSet,
  settledPositions,
  type PlateOptions
} from './castaliaModel.ts'
import { seededRng } from './particleUtil.ts'

const DT = 1 / 30
const ASPECT = 1.6
const NEAR = 0.05 // |f| below this is "on a line"

function plate(seed = 1, opts: PlateOptions = {}): CastaliaPlate {
  const p = new CastaliaPlate(seededRng(seed), opts)
  p.setBounds(ASPECT)
  p.setIdentity('album:1', '10', false)
  p.prewarm()
  return p
}

function run(p: CastaliaPlate, seconds: number, each?: () => void): void {
  for (let t = 0; t < seconds; t += DT) {
    p.tick(DT)
    each?.()
  }
}

/** Fraction of grains on a nodal line of the plate's current field. */
function onLines(p: CastaliaPlate): number {
  let n = 0
  for (let i = 0; i < GRAINS; i++) if (Math.abs(p.field(p.u[i], p.v[i])) < NEAR) n++
  return n / GRAINS
}

/** Scatter every grain uniformly over the plate, at rest. */
function scatter(p: CastaliaPlate, seed = 9): void {
  const r = seededRng(seed)
  for (let i = 0; i < GRAINS; i++) {
    p.u[i] = r()
    p.v[i] = r()
    p.vx[i] = p.vy[i] = 0
  }
}

function meanSpeed(p: CastaliaPlate): number {
  let s = 0
  for (let i = 0; i < GRAINS; i++) s += Math.hypot(p.vx[i], p.vy[i])
  return s / GRAINS
}

// --- the mode table ----------------------------------------------------------------

test('modes are distinct, integer and m < n, so none is degenerate', () => {
  const seen = new Set<string>()
  for (const { m, n, sign } of MODES) {
    assert.ok(Number.isInteger(m) && Number.isInteger(n) && m >= 1 && m < n)
    const key = `${m},${n},${sign}`
    assert.ok(!seen.has(key), key)
    seen.add(key)
  }
})

test('every mode has at least two neighbours, none itself, and neighbourhood is symmetric', () => {
  NEIGHBOURS.forEach((nb, i) => {
    assert.ok(nb.length >= 2, `mode ${i} has ${nb.length} neighbours`)
    for (const j of nb) {
      assert.notEqual(j, i)
      assert.ok(NEIGHBOURS[j].includes(i), `${i} -> ${j} is not mutual`)
    }
  })
})

test('every mode has visible nodal lines: neither empty nor a wash of noise', () => {
  const N = 120
  MODES.forEach((mode, i) => {
    let near = 0
    for (let a = 0; a < N; a++) {
      for (let b = 0; b < N; b++) {
        if (Math.abs(chladni(mode, (a + 0.5) / N, (b + 0.5) / N)) < NEAR) near++
      }
    }
    const frac = near / (N * N)
    assert.ok(frac > 0.03 && frac < 0.25, `mode ${i} line coverage ${frac.toFixed(3)}`)
  })
})

test('the analytic gradient matches a finite difference', () => {
  const g = new Float64Array(3)
  const h = 1e-6
  const r = seededRng(4)
  for (const mode of MODES) {
    const u = r()
    const v = r()
    chladniGrad(mode, u, v, g)
    assert.ok(Math.abs(g[0] - chladni(mode, u, v)) < 1e-12)
    const du = (chladni(mode, u + h, v) - chladni(mode, u - h, v)) / (2 * h)
    const dv = (chladni(mode, u, v + h) - chladni(mode, u, v - h)) / (2 * h)
    assert.ok(Math.abs(g[1] - du) < 1e-4)
    assert.ok(Math.abs(g[2] - dv) < 1e-4)
  }
})

// --- identity ------------------------------------------------------------------------

test('an album key always picks the same figure, and keys spread over the table', () => {
  assert.equal(albumMode('album:42'), albumMode('album:42'))
  const hit = new Set<number>()
  for (let k = 0; k < 300; k++) hit.add(albumMode(`album:${k}`))
  assert.ok(hit.size >= MODES.length * 0.75, `only ${hit.size} of ${MODES.length} modes used`)
})

test('a track shows a harmonic of its album: the base or a neighbour', () => {
  const p = plate()
  const set = harmonicSet(albumMode('album:1'))
  for (let t = 0; t < 20; t++) {
    p.setIdentity('album:1', String(t), false)
    assert.ok(set.includes(p.mode))
  }
})

test('a skip within the album always changes the figure', () => {
  const p = plate()
  for (let t = 11; t < 40; t++) {
    const before = p.mode
    p.setIdentity('album:1', String(t), false)
    assert.notEqual(p.mode, before, `track ${t}`)
  }
})

test('the same track from a fresh start shows the same figure and the same grains', () => {
  const a = plate(1)
  const b = plate(2)
  assert.equal(a.mode, b.mode)
  assert.deepEqual([...a.u.slice(0, 20)], [...b.u.slice(0, 20)])
})

test('while stopped, a new identity re-places the grains on the new figure at once', () => {
  const p = plate()
  let other = 'album:2'
  for (let k = 3; albumMode(other) === albumMode('album:1'); k++) other = `album:${k}`
  p.setIdentity(other, '1', false)
  assert.ok(onLines(p) > 0.6, `only ${onLines(p)} on the new figure`)
})

test('identity while running with no track change (restore) morphs quietly, with no ring', () => {
  const p = plate()
  const set = harmonicSet(albumMode('album:7'))
  p.setIdentity('album:7', '1', true)
  run(p, DT)
  assert.equal(p.mode, set[fnv1a('1') % set.length])
  assert.equal(p.ringLevel, 0)
})

// --- settling ------------------------------------------------------------------------

test('scattered grains settle onto the nodal lines', () => {
  const p = plate()
  scatter(p)
  const before = onLines(p)
  run(p, 20)
  assert.ok(onLines(p) > 0.75, `only ${onLines(p)} settled (from ${before})`)
})

test('positive control: without the pull, scattered grains do not settle', () => {
  const p = plate(1, { force: 0 })
  scatter(p)
  run(p, 20)
  assert.ok(onLines(p) < 0.35, `${onLines(p)} settled with no pull`)
})

test('a grain on an antinode, where the pull is zero, is bounced off it and settles', () => {
  // (1,4,+): f(0,0) = 1 and the gradient there is exactly zero.
  const idx = MODES.findIndex((m) => m.m === 1 && m.n === 4 && m.sign === 1)
  const still = plate(1, { jitter: 0 })
  const live = plate(1)
  for (const p of [still, live]) {
    p.ringTo(idx)
    p.ringLevel = 0
    for (let i = 0; i < GRAINS; i++) {
      p.u[i] = p.v[i] = 0
      p.vx[i] = p.vy[i] = 0
    }
  }
  run(still, 15)
  run(live, 15)
  assert.equal(still.u[0], 0, 'positive control: with no bounce the grain should sit forever')
  assert.ok(onLines(live) > 0.6, `only ${onLines(live)} escaped the antinode`)
})

test('the prewarm opens on the settled figure', () => {
  const p = plate()
  assert.ok(onLines(p) > 0.6)
  run(p, 10)
  assert.ok(onLines(p) > 0.6, 'the settled figure dissolved on its own')
})

test('settledPositions is deterministic and fills every slot inside the plate', () => {
  const a = new Float64Array(200)
  const b = new Float64Array(200)
  const c = new Float64Array(200)
  const d = new Float64Array(200)
  settledPositions(MODES[3], seededRng(5), a, b)
  settledPositions(MODES[3], seededRng(5), c, d)
  assert.deepEqual(a, c)
  assert.deepEqual(b, d)
  for (let i = 0; i < 200; i++) assert.ok(a[i] >= 0 && a[i] <= 1 && b[i] >= 0 && b[i] <= 1)
})

// --- the ring ----------------------------------------------------------------------

test('a same-album track change rings at once and the grains resettle', () => {
  const p = plate()
  run(p, 2)
  const quiet = meanSpeed(p)
  p.setIdentity('album:1', '11', true)
  p.trackChanged(true)
  assert.equal(p.ringLevel, 1)
  run(p, DT)
  assert.ok(meanSpeed(p) > quiet * 3)
  run(p, 15)
  assert.ok(onLines(p) > 0.7, `only ${onLines(p)} resettled`)
})

test('a new album waits for its palette, then rings onto the new figure', () => {
  const p = plate()
  const old = p.mode
  p.setIdentity('album:2', '1', true)
  p.trackChanged(true)
  assert.ok(p.ringIsPending)
  run(p, RING_FALLBACK_S / 2)
  assert.equal(p.ringLevel, 0)
  assert.equal(p.mode, old, 'the figure changed before the ring')
  p.setPalette(true)
  assert.equal(p.ringLevel, 1)
  assert.ok(harmonicSet(albumMode('album:2')).includes(p.mode))
})

test('a new album whose palette never comes rings after the fallback', () => {
  const p = plate()
  p.setIdentity('album:2', '1', true)
  p.trackChanged(true)
  run(p, RING_FALLBACK_S + 2 * DT)
  assert.ok(!p.ringIsPending)
  assert.ok(p.ringLevel > 0.5)
})

test('a palette with nothing pending (mount, theme) never rings', () => {
  const p = plate()
  p.setPalette(true)
  assert.equal(p.ringLevel, 0)
})

test('a track change while stopped never rings', () => {
  const p = plate()
  p.setIdentity('album:1', '11', false)
  p.trackChanged(false)
  run(p, 1)
  assert.equal(p.ringLevel, 0)
})

test('resume drops a pending ring so it never fires late', () => {
  const p = plate()
  p.setIdentity('album:2', '1', true)
  p.trackChanged(true)
  p.resume()
  run(p, RING_FALLBACK_S * 2)
  assert.equal(p.ringLevel, 0)
})

test('rapid skips never push a grain off the plate or past the speed cap, and nothing jumps', () => {
  const p = plate()
  const pu = Float64Array.from(p.u)
  const pv = Float64Array.from(p.v)
  let worst = 0
  for (let k = 0; k < 12; k++) {
    p.setIdentity(`album:${k % 3}`, String(k), true)
    p.trackChanged(true)
    if (k % 2) p.setPalette(true)
    run(p, 0.25, () => {
      for (let i = 0; i < GRAINS; i++) {
        assert.ok(p.u[i] >= 0 && p.u[i] <= 1 && p.v[i] >= 0 && p.v[i] <= 1)
        assert.ok(Math.hypot(p.vx[i], p.vy[i]) <= VMAX + 1e-9)
        const step = Math.hypot((p.u[i] - pu[i]) * ASPECT, p.v[i] - pv[i])
        worst = Math.max(worst, step)
      }
      pu.set(p.u)
      pv.set(p.v)
    })
  }
  assert.ok(worst <= VMAX * DT + 1e-9, `a grain jumped ${worst}`)
})

test('a ring during a ring lifts the envelope; it never drops it', () => {
  const p = plate()
  p.setIdentity('album:1', '11', true)
  p.trackChanged(true)
  run(p, 0.3)
  const mid = p.ringLevel
  p.setIdentity('album:1', '12', true)
  p.trackChanged(true)
  assert.ok(p.ringLevel >= mid)
  assert.equal(p.ringLevel, 1)
})

test('rapid skipping still leaves grains spread over the plate, never bare', () => {
  const p = plate()
  for (let k = 0; k < 10; k++) {
    p.setIdentity('album:1', String(20 + k), true)
    p.trackChanged(true)
    run(p, 0.3)
  }
  // Count occupied cells of a coarse grid: grains must not have piled into a corner.
  const cells = new Set<number>()
  for (let i = 0; i < GRAINS; i++) cells.add(Math.floor(p.u[i] * 6) * 6 + Math.floor(p.v[i] * 6))
  assert.ok(cells.size >= 30, `grains occupy only ${cells.size} of 36 cells`)
})

// --- the shimmer -------------------------------------------------------------------

test('the shimmer is rare: its gap respects the floor and the cap', () => {
  for (let s = 1; s < 30; s++) {
    const p = plate(s)
    assert.ok(p.nextShimmerAt >= SHIMMER_GAP_FLOOR_S && p.nextShimmerAt <= SHIMMER_GAP_MAX_S)
  }
})

test('the shimmer rises and relaxes smoothly, never past its peak, and lands exactly on 0', () => {
  const p = plate()
  p.forceShimmer()
  let prev = 0
  let peak = 0
  run(p, SHIMMER_S + 1, () => {
    assert.ok(Math.abs(p.shimmer - prev) <= SHIMMER_PEAK * 0.06, 'the shimmer jumped')
    peak = Math.max(peak, p.shimmer)
    prev = p.shimmer
  })
  assert.ok(peak > SHIMMER_PEAK * 0.9 && peak <= SHIMMER_PEAK)
  assert.equal(p.shimmer, 0)
  assert.ok(onLines(p) > 0.6, 'the figure did not relax back')
})

test('a due shimmer waits for a still cursor and a quiet plate', () => {
  const p = plate()
  p.nextShimmerAt = 0
  p.pointerMove(0.2, 0.5)
  run(p, 1)
  assert.equal(p.shimmer, 0, 'shimmered under a moving cursor')
  p.pointerLeave()
  p.setIdentity('album:1', '11', true)
  p.trackChanged(true)
  run(p, 1)
  assert.equal(p.shimmer, 0, 'shimmered during a ring')
  run(p, 8)
  assert.ok(p.shimmer > 0 || p.nextShimmerAt > p.elapsed, 'the shimmer never came')
})

// --- the cursor --------------------------------------------------------------------

test('a moving cursor jostles nearby grains and leaves distant ones alone', () => {
  const p = plate()
  run(p, 3)
  const cx = 0.3
  const cy = 0.5
  p.pointerMove(cx, cy)
  const near: number[] = []
  const far: number[] = []
  for (let i = 0; i < GRAINS; i++) {
    const d = Math.hypot(p.u[i] * ASPECT - cx, p.v[i] - cy)
    if (d < 0.04) near.push(i)
    else if (d > 0.3) far.push(i)
  }
  assert.ok(near.length > 0 && far.length > 0, 'positive control: no grains to compare')
  const u0 = Float64Array.from(p.u)
  const v0 = Float64Array.from(p.v)
  // Mean displacement in pane heights: what the eye sees, not a speed.
  const moved = (idx: number[]): number =>
    idx.reduce((s, i) => s + Math.hypot((p.u[i] - u0[i]) * ASPECT, p.v[i] - v0[i]), 0) /
    idx.length
  // A quick back-and-forth rub, about 1 pane height / s, for half a second.
  for (let k = 0; k < 15; k++) {
    p.pointerMove(cx + (k % 2) * 0.03, cy)
    p.tick(DT)
  }
  const n = moved(near)
  const f = moved(far)
  // 0.006 pane heights is ~5 px on an 800 px pane: visible on a 1 px grain.
  assert.ok(n > 0.006, `near grains barely moved: ${n}`)
  assert.ok(f < n / 4, `distant grains moved ${f} against ${n} near the cursor`)
  // ...and they settle back onto the figure.
  p.pointerLeave()
  run(p, 8)
  assert.ok(onLines(p) > 0.6)
})

test('leaving on one side and re-entering on the other is not a sweep', () => {
  const p = plate()
  p.pointerMove(0.05, 0.5)
  p.tick(DT)
  p.pointerLeave()
  p.tick(DT)
  const quiet = meanSpeed(p)
  p.pointerMove(ASPECT - 0.05, 0.5)
  p.tick(DT)
  assert.ok(meanSpeed(p) < quiet * 2 + 0.01)
})

// --- recolor -------------------------------------------------------------------------

test('while stopped, a new palette recolors every grain at once (KAMP-779)', () => {
  const p = plate()
  const gen = p.setPalette(false)
  for (let i = 0; i < GRAINS; i++) {
    assert.equal(p.gen[i], gen)
    assert.equal(p.alpha(i), p.baseAlpha[i])
  }
})

test('while running, every grain reaches the new palette, only at the bottom of a dip', () => {
  const p = plate()
  const gen = p.setPalette(true)
  const prev = Uint32Array.from(p.gen)
  let snaps = 0
  run(p, 3, () => {
    for (let i = 0; i < GRAINS; i++) {
      if (p.gen[i] !== prev[i] && p.alpha(i) > p.baseAlpha[i] * 0.05) snaps++
    }
    prev.set(p.gen)
  })
  assert.equal(snaps, 0)
  for (let i = 0; i < GRAINS; i++) assert.equal(p.gen[i], gen)
  assert.equal(p.oldestGenerationInUse(), gen)
})

test('grain alpha stays dim, within the requirement', () => {
  const p = plate()
  for (let i = 0; i < GRAINS; i++) assert.ok(p.alpha(i) >= 0.3 && p.alpha(i) <= 0.5)
})
