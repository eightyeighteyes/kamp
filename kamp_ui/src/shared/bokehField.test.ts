import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  ORB_PLAN,
  SpriteBakeGate,
  TIERS,
  VISIBLE_SHARE,
  advanceField,
  makeField,
  visibleOrbCount,
  visibleShare,
  type Orb,
  type Tier
} from './bokehField.ts'
import { seededRng, type PaneRect } from './particleUtil.ts'
import { PaletteCrossfade } from './paletteCrossfade.ts'

// A wide Now Playing pane with the art centred at 45% of its height, roughly the
// shipped layout. Art hidden (KAMP-769) is the `null` case.
const W = 1600
const H = 1000
const ART: PaneRect = { x0: 0.5 - 0.225 * (H / W), x1: 0.5 + 0.225 * (H / W), y0: 0.275, y1: 0.725 }

const STEP = 0.1 // the engine's dt is clamped to 0.1 s; never take bigger steps
const HOUR = 3600

/** Share of one-second samples with each visible-orb count, over `seconds`. */
function census(seed: number, seconds: number, art: PaneRect | null): number[] {
  const orbs = makeField(seededRng(seed))
  const counts = new Array(ORB_PLAN.length + 1).fill(0)
  let elapsed = 0
  for (let s = 0; s < seconds; s++) {
    for (let i = 0; i < 1 / STEP; i++) {
      advanceField(orbs, STEP)
      elapsed += STEP
    }
    counts[visibleOrbCount(orbs, elapsed, W, H, art)]++
  }
  return counts.map((c) => c / seconds)
}

test('visibleShare: a centred orb is seen, one behind the art or off the pane is not', () => {
  // No art: a modest orb in the middle of the pane is wholly visible.
  assert.equal(visibleShare(W / 2, H / 2, 200, W, H, null), 1)
  // The same orb hidden behind the opaque art counts for nothing.
  assert.equal(visibleShare(W / 2, H / 2, 200, W, H, ART), 0)
  // Off the pane entirely.
  assert.equal(visibleShare(-500, H / 2, 200, W, H, null), 0)
  // Straddling the left edge: half is on the pane, but the vignette band (96 px
  // here) eats into it. The disc beyond x = 96 at r = 200 is 0.207 analytically.
  const edge = visibleShare(0, H / 2, 200, W, H, null)
  assert.ok(Math.abs(edge - 0.207) < 0.03, `edge share ${edge}`)
})

test('visibleOrbCount respects VISIBLE_SHARE', () => {
  const [o] = makeField(seededRng(1))
  const solo: Orb[] = [{ ...o, ax: 0, ay: 0, bx: 0.5, by: 0.5 }]
  assert.equal(visibleOrbCount(solo, 0, W, H, null), 1)
  assert.equal(visibleOrbCount(solo, 0, W, H, ART), o.sizePct * H * 0.5 > 0.3 * H ? 1 : 0)
  // Slide it off the right edge until less than the threshold remains.
  solo[0].bx = 1.2
  assert.ok(visibleShare(1.2 * W, H / 2, o.sizePct * H * 0.5, W, H, null) < VISIBLE_SHARE)
  assert.equal(visibleOrbCount(solo, 0, W, H, null), 0)
})

/** Run `seconds` at 30 fps, calling `each` with the field after every frame. */
function run(seed: number, seconds: number, each: (orbs: Orb[], dt: number) => void): void {
  const orbs = makeField(seededRng(seed))
  const dt = 1 / 30
  for (let f = 0; f < seconds * 30; f++) {
    advanceField(orbs, dt)
    each(orbs, dt)
  }
}

test('each orb base stays within its tier reach', () => {
  run(1, HOUR, (orbs) => {
    for (const o of orbs) {
      const half = 0.5 * TIERS[o.tier].reach + 1e-9
      assert.ok(
        Math.abs(o.bx - 0.5) <= half && Math.abs(o.by - 0.5) <= half,
        `${o.tier} out of reach`
      )
    }
  })
})

test('heroes keep their centre on the pane; mids and accents leave and come back', () => {
  // Heroes are the wash behind the art, so their base never leaves the pane. The
  // smaller orbs must actually roam off it, or the count never varies and the
  // field reads as a static arrangement.
  const offAt = { hero: 0, mid: 0, accent: 0 }
  const backAfterOff = { hero: false, mid: false, accent: false }
  const wasOff: boolean[] = []
  run(2, HOUR, (orbs) => {
    orbs.forEach((o, i) => {
      const off = o.bx < 0 || o.bx > 1 || o.by < 0 || o.by > 1
      if (off) offAt[o.tier]++
      if (wasOff[i] && !off) backAfterOff[o.tier] = true
      wasOff[i] = off
    })
  })
  assert.equal(offAt.hero, 0)
  assert.ok(offAt.mid > 0 && backAfterOff.mid, 'mids never left and came back')
  assert.ok(offAt.accent > offAt.mid && backAfterOff.accent, 'accents should roam furthest')
})

test('the drift is as calm as the old straight-line drift', () => {
  // The wrap model crossed a pane in 90–150 s (hero), 60–110 s (mid) and 45–70 s
  // (accent): mean speeds of about 0.008, 0.012 and 0.018 panes/s. Hold each tier
  // within a band of those, and keep the ordering (small orbs move most).
  const sum = { hero: 0, mid: 0, accent: 0 }
  const n = { hero: 0, mid: 0, accent: 0 }
  let prev: { x: number; y: number }[] = []
  run(3, HOUR, (orbs, dt) => {
    orbs.forEach((o, i) => {
      if (prev[i]) {
        sum[o.tier] += Math.hypot(o.bx - prev[i].x, o.by - prev[i].y) / dt
        n[o.tier]++
      }
    })
    prev = orbs.map((o) => ({ x: o.bx, y: o.by }))
  })
  const mean = (t: Tier): number => sum[t] / n[t]
  const band: Record<Tier, [number, number]> = {
    hero: [0.005, 0.013],
    mid: [0.008, 0.018],
    accent: [0.011, 0.024]
  }
  for (const t of ['hero', 'mid', 'accent'] as const) {
    assert.ok(mean(t) > band[t][0] && mean(t) < band[t][1], `${t} speed ${mean(t).toFixed(4)}`)
  }
  assert.ok(mean('hero') < mean('mid') && mean('mid') < mean('accent'))
})

test('no orb ever turns abruptly (the walk bound is soft)', () => {
  // DrunkWalk reflects hard at its bound. Through a linear map that is a sudden
  // turn mid-pane: the worst frame-to-frame velocity change measured 0.04–0.054
  // panes/s. Through the tanh map it is ~0.006–0.011, the walk's own noise.
  let worst = 0
  for (const seed of [1, 2, 3]) {
    let prev: { x: number; y: number; vx: number; vy: number }[] = []
    let frames = 0
    run(seed, HOUR, (orbs, dt) => {
      const next = orbs.map((o, i) => {
        const p = prev[i]
        const vx = p ? (o.bx - p.x) / dt : 0
        const vy = p ? (o.by - p.y) / dt : 0
        if (p && frames > 1) worst = Math.max(worst, Math.hypot(vx - p.vx, vy - p.vy))
        return { x: o.bx, y: o.by, vx, vy }
      })
      prev = next
      frames++
    })
  }
  assert.ok(worst < 0.02, `worst velocity change ${worst.toFixed(4)} panes/s per frame`)
})

test('a new field starts spread out, not gathered at the centre', () => {
  // DrunkWalk starts at 0; without the prewarm every orb would open on the art.
  let spread = 0
  let count = 0
  for (let seed = 1; seed <= 40; seed++) {
    for (const o of makeField(seededRng(seed))) {
      spread += Math.abs(o.bx - 0.5) / (0.5 * TIERS[o.tier].reach)
      count++
    }
  }
  assert.ok(spread / count > 0.25, `mean start offset ${(spread / count).toFixed(3)} of reach`)
})

test('the same seed rebuilds the same field (reproducible __kampGL screenshots)', () => {
  const a = makeField(seededRng(9))
  const b = makeField(seededRng(9))
  advanceField(a, 0.1)
  advanceField(b, 0.1)
  assert.deepEqual(
    a.map((o) => [o.bx, o.by, o.sizePct, o.blurMul]),
    b.map((o) => [o.bx, o.by, o.sizePct, o.blurMul])
  )
})

// KAMP-782: depth of field. Each orb carries its own blur, so the field reads as
// a range of softness rather than one sheet. Small orbs sit nearest the focal
// plane, big ones furthest from it.
test('each orb blurs within its tier range, accents least and heroes most', () => {
  const seen: Record<Tier, [number, number]> = {
    hero: [Infinity, -Infinity],
    mid: [Infinity, -Infinity],
    accent: [Infinity, -Infinity]
  }
  for (let seed = 1; seed <= 50; seed++) {
    for (const o of makeField(seededRng(seed))) {
      const [lo, hi] = TIERS[o.tier].blur
      assert.ok(o.blurMul >= lo && o.blurMul <= hi, `${o.tier} blur ${o.blurMul}`)
      seen[o.tier][0] = Math.min(seen[o.tier][0], o.blurMul)
      seen[o.tier][1] = Math.max(seen[o.tier][1], o.blurMul)
    }
  }
  assert.ok(seen.accent[1] < seen.mid[0], 'every accent is sharper than every mid')
  assert.ok(seen.mid[1] <= seen.hero[0], 'every mid is sharper than every hero')
  // A spread within each tier too, so no two orbs share one blur level.
  for (const t of ['hero', 'mid', 'accent'] as const) {
    assert.ok(seen[t][1] - seen[t][0] > 0.1, `${t} blur has no spread`)
  }
})

// The KAMP-779 report: at times the pane looked blank. Over an hour of seeded
// drift, with the art covering the centre and the vignette eating the edges, the
// field must never drop to zero visible orbs, and almost never to one.
for (const art of [ART, null]) {
  test(`the field is never blank (art ${art ? 'shown' : 'hidden'})`, () => {
    for (const seed of [1, 2, 3, 4]) {
      const share = census(seed, HOUR, art)
      assert.equal(share[0], 0, `seed ${seed}: blank ${(share[0] * 100).toFixed(1)}% of the time`)
      assert.ok(
        share[1] < 0.02,
        `seed ${seed}: one orb ${(share[1] * 100).toFixed(1)}% of the time`
      )
    }
  })
}

// --- sprite re-bake gate, against the real PaletteCrossfade -----------------

const RED = [{ r: 255, g: 0, b: 0 }]
const BLUE = [{ r: 0, g: 0, b: 255 }]

test('a settled field never re-bakes its sprites', () => {
  const fade = new PaletteCrossfade(RED, 1000)
  const gate = new SpriteBakeGate()
  assert.equal(gate.shouldBake('k', fade.current()), true) // first frame
  for (let f = 0; f < 300; f++) {
    fade.tick(33)
    assert.equal(gate.shouldBake('k', fade.current()), false, `re-baked on frame ${f}`)
  }
})

test('a fade re-bakes every drawn frame until it settles, then stops', () => {
  const fade = new PaletteCrossfade(RED, 1000)
  const gate = new SpriteBakeGate()
  gate.shouldBake('k', fade.current())
  fade.set(BLUE, true)
  let bakes = 0
  for (let f = 0; f < 60; f++) {
    fade.tick(33)
    if (gate.shouldBake('k', fade.current())) bakes++
  }
  // ~1000 ms / 33 ms of fade frames, plus the one that lands on the target.
  assert.ok(bakes >= 30 && bakes <= 32, `${bakes} bakes`)
  assert.equal(gate.shouldBake('k', fade.current()), false)
})

test('a palette set while stopped re-bakes once, in its final colours', () => {
  const fade = new PaletteCrossfade(RED, 1000)
  const gate = new SpriteBakeGate()
  gate.shouldBake('k', fade.current())
  fade.set(BLUE, false) // the loop is stopped: reduced motion, or the pane hidden
  assert.deepEqual(fade.current(), BLUE)
  assert.equal(gate.shouldBake('k', fade.current()), true)
  assert.equal(gate.shouldBake('k', fade.current()), false)
})

test('a geometry change or invalidate() re-bakes a settled field', () => {
  const fade = new PaletteCrossfade(RED, 1000)
  const gate = new SpriteBakeGate()
  gate.shouldBake('k', fade.current())
  assert.equal(gate.shouldBake('k2', fade.current()), true)
  gate.invalidate()
  assert.equal(gate.shouldBake('k2', fade.current()), true)
})
