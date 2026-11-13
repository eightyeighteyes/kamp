import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  VISIBLE_SHARE,
  advanceField,
  makeField,
  visibleOrbCount,
  visibleShare,
  type Orb
} from './bokehField.ts'
import { seededRng, type PaneRect } from './particleUtil.ts'

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
  const counts = new Array(8).fill(0)
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

// The KAMP-779 report: at times the pane looked blank. Over an hour of seeded
// drift, with the art covering the centre and the vignette eating the edges, the
// field must never drop to zero visible orbs, and almost never to one.
for (const art of [ART, null]) {
  test(`the field is never blank (art ${art ? 'shown' : 'hidden'})`, () => {
    for (const seed of [1, 2, 3, 4]) {
      const share = census(seed, HOUR, art)
      assert.equal(share[0], 0, `seed ${seed}: blank ${(share[0] * 100).toFixed(1)}% of the time`)
      assert.ok(share[1] < 0.02, `seed ${seed}: one orb ${(share[1] * 100).toFixed(1)}% of the time`)
    }
  })
}
