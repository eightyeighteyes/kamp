import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  FIRST_RACK,
  HIDDEN_RERACK_S,
  MAX_SHARP,
  PULL_S,
  RACK_GAP,
  RackFocus,
  SOFT_DIM,
  focusAlphas
} from './bokehFocus.ts'
import { seededRng } from './particleUtil.ts'

// Eight orbs, the last three are the accents (bokehField's ORB_PLAN order).
const N = 8
const ACCENTS = [5, 6, 7]
const DT = 1 / 60
const always = (): boolean => true

function rackOf(seed: number): RackFocus {
  return new RackFocus(N, ACCENTS, seededRng(seed))
}

/** The orbs a rack is pulling toward focus right now. */
function targets(r: RackFocus): number[] {
  return [...r.targets].sort()
}

/** Tick until the target set changes (or `limit` seconds pass); returns seconds. */
function untilRack(r: RackFocus, visible: (i: number) => boolean, limit = 120): number {
  const before = targets(r).join()
  let t = 0
  while (t < limit) {
    r.tick(DT, visible)
    t += DT
    if (targets(r).join() !== before) return t
  }
  return Infinity
}

test('nothing is in focus at first, and the first rack comes soon', () => {
  const r = rackOf(1)
  assert.deepEqual(r.focus, new Array(N).fill(0))
  const t = untilRack(r, always)
  assert.ok(t <= FIRST_RACK[1] + DT, `first rack after ${t.toFixed(1)} s`)
})

test('a pull lands exactly on 1 and on 0 (KAMP-772)', () => {
  const r = rackOf(2)
  untilRack(r, always)
  const picked = targets(r)
  for (let t = 0; t < PULL_S + 0.1; t += DT) r.tick(DT, always)
  for (let i = 0; i < N; i++) {
    if (picked.includes(i)) assert.equal(r.focus[i], 1, `orb ${i} stopped at ${r.focus[i]}`)
    else assert.equal(r.focus[i], 0, `orb ${i} stopped at ${r.focus[i]}`)
  }
  // And back down to exactly 0 once the next rack drops them.
  untilRack(r, always)
  for (let t = 0; t < PULL_S + 0.1; t += DT) r.tick(DT, always)
  for (const i of picked) {
    if (!r.targets.has(i)) assert.equal(r.focus[i], 0, `orb ${i} stuck at ${r.focus[i]}`)
  }
})

test('only accents ever come into focus', () => {
  const r = rackOf(3)
  for (let t = 0; t < 3600; t += DT) {
    r.tick(DT, always)
    for (let i = 0; i < N; i++) if (!ACCENTS.includes(i)) assert.equal(r.focus[i], 0)
  }
})

test('a rack picks 1 or 2 accents at random, and every accent gets its turn', () => {
  const r = rackOf(4)
  const sizes = new Set<number>()
  const picked = new Set<number>()
  for (let n = 0; n < 60; n++) {
    untilRack(r, always)
    sizes.add(r.targets.size)
    for (const i of r.targets) picked.add(i)
  }
  assert.deepEqual([...sizes].sort(), [1, 2])
  assert.deepEqual([...picked].sort(), ACCENTS)
})

test('focus moves on: each rack changes which accents are sharp', () => {
  const r = rackOf(5)
  untilRack(r, always)
  for (let n = 0; n < 40; n++) {
    // Every rack must change the set, so the set changes within one rack gap.
    // (Waiting for "any change" would let a repeated pick slip through to the
    // next rack.)
    const t = untilRack(r, always)
    assert.ok(t <= RACK_GAP[1] + DT, `the same accents stayed sharp for ${t.toFixed(1)} s`)
  }
})

test('a pull eases in and out: no snap as it starts or lands', () => {
  const r = rackOf(13)
  untilRack(r, always) // this frame starts the pull; focus hasn't moved yet
  const [i] = targets(r)
  let prev = r.focus[i]
  r.tick(DT, always)
  assert.ok(r.focus[i] - prev < 1e-3, `first frame moved ${(r.focus[i] - prev).toFixed(5)}`)
  // Run to the frame before it lands, then check the last step is as gentle.
  while (r.focus[i] < 1) {
    prev = r.focus[i]
    r.tick(DT, always)
  }
  assert.ok(1 - prev < 1e-3, `last frame moved ${(1 - prev).toFixed(5)}`)
})

test('a rack only picks accents that are visible when it picks', () => {
  // Orb 6 sits behind the art for the whole run.
  const visible = (i: number): boolean => i !== 6
  const r = rackOf(6)
  for (let n = 0; n < 40; n++) {
    untilRack(r, visible)
    assert.ok(!r.targets.has(6), 'picked a hidden orb')
    assert.ok(r.targets.size > 0)
  }
})

test('a target that hides behind the art loses focus within HIDDEN_RERACK_S', () => {
  const r = rackOf(7)
  untilRack(r, always)
  const [hid] = targets(r)
  const visible = (i: number): boolean => i !== hid
  let t = 0
  while (r.targets.has(hid) && t < 60) {
    r.tick(DT, visible)
    t += DT
  }
  assert.ok(t <= HIDDEN_RERACK_S + DT, `still targeted after ${t.toFixed(1)} s hidden`)
})

test('with no accent visible, nothing is targeted and every orb defocuses', () => {
  const r = rackOf(8)
  untilRack(r, always)
  const none = (): boolean => false
  for (let t = 0; t < HIDDEN_RERACK_S + PULL_S + 1; t += DT) r.tick(DT, none)
  assert.equal(r.targets.size, 0)
  assert.deepEqual(r.focus, new Array(N).fill(0))
  // And focus returns once an accent is back in view.
  assert.ok(untilRack(r, always) < 10)
})

test('a re-rack mid-pull continues from where focus is, never jumps (KAMP-774)', () => {
  const r = rackOf(9)
  untilRack(r, always)
  for (let t = 0; t < PULL_S / 2; t += DT) r.tick(DT, always)
  const mid = [...r.focus]
  assert.ok(
    mid.some((f) => f > 0.2 && f < 0.8),
    'expected a pull in progress'
  )
  r.rack(always) // the dev "pull now", or a hidden-target re-rack, mid-pull
  // The rack itself moves nothing; a single frame moves focus only a little.
  assert.deepEqual(r.focus, mid)
  r.tick(DT, always)
  for (let i = 0; i < N; i++) {
    assert.ok(Math.abs(r.focus[i] - mid[i]) < 0.02, `orb ${i} jumped ${mid[i]} -> ${r.focus[i]}`)
  }
})

test('focus never steps abruptly and never sharpens more than MAX_SHARP orbs', () => {
  // An hour with accents wandering in and out of view, re-seeded each minute.
  const rng = seededRng(10)
  const hidden = new Set<number>()
  const visible = (i: number): boolean => !hidden.has(i)
  const r = rackOf(11)
  let prev = [...r.focus]
  let worst = 0
  for (let t = 0; t < 3600; t += DT) {
    if (rng() < DT / 7) {
      const i = ACCENTS[Math.floor(rng() * ACCENTS.length)]
      if (hidden.has(i)) hidden.delete(i)
      else hidden.add(i)
    }
    r.tick(DT, visible)
    const sharp = r.focus.filter((f) => f > 0.5).length
    assert.ok(sharp <= MAX_SHARP, `${sharp} orbs sharp at ${t.toFixed(1)} s`)
    for (let i = 0; i < N; i++) worst = Math.max(worst, Math.abs(r.focus[i] - prev[i]))
    prev = [...r.focus]
  }
  // A smootherstep over PULL_S peaks at 1.875/PULL_S per second.
  assert.ok(worst <= (1.875 / PULL_S) * DT + 1e-9, `worst step ${worst.toFixed(4)}`)
})

test('the same seed racks the same way (reproducible __kampGL screenshots)', () => {
  const a = rackOf(12)
  const b = rackOf(12)
  for (let t = 0; t < 300; t += DT) {
    a.tick(DT, always)
    b.tick(DT, always)
  }
  assert.deepEqual(a.focus, b.focus)
})

// --- soft under sharp ----------------------------------------------------------

// Two layers composited with 'screen': the result's coverage is 1 − (1−a)(1−b).
const screen = (a: number, b: number): number => 1 - (1 - a) * (1 - b)

test('soft under sharp: the centre never darkens while the halo dims (KAMP-768/774)', () => {
  // Sweep the alphas an orb is actually drawn at: accents are 0.5–0.56 base, up
  // to ~0.7 with breath and bloom; the sharp interior is a gain over that.
  for (let as = 0.05; as <= 0.95; as += 0.05) {
    for (let ak = 0.05; ak <= 1; ak += 0.05) {
      let prevCentre = -Infinity
      let prevHalo = Infinity
      for (let f = 0; f <= 1 + 1e-9; f += 0.01) {
        const { soft, sharp } = focusAlphas(f, as, ak)
        const centre = screen(as * soft, ak * sharp)
        const halo = as * soft // beyond the sharp disc only the soft layer shows
        assert.ok(centre >= prevCentre - 1e-12, `centre dipped at f=${f} (as=${as}, ak=${ak})`)
        assert.ok(halo <= prevHalo + 1e-12, `halo rose at f=${f}`)
        prevCentre = centre
        prevHalo = halo
      }
    }
  }
})

test('soft under sharp: out of focus is the soft layer alone, at full alpha', () => {
  assert.deepEqual(focusAlphas(0, 0.5, 0.8), { soft: 1, sharp: 0 })
})

test('soft under sharp: in focus, the halo dims by SOFT_DIM when the sharp layer can cover it', () => {
  // Bright sharp interior over a modest soft centre: the full dim is allowed.
  const { soft, sharp } = focusAlphas(1, 0.3, 1)
  assert.equal(sharp, 1)
  assert.ok(Math.abs(soft - (1 - SOFT_DIM)) < 1e-12)
  // A soft centre already near opaque can't be dimmed without the centre dipping.
  assert.ok(focusAlphas(1, 0.95, 0.5).soft > 1 - SOFT_DIM)
})
