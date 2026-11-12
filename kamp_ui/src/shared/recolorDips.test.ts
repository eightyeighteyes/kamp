import { test } from 'node:test'
import assert from 'node:assert/strict'
import { RecolorDips, type RecolorDipOptions } from './recolorDips.ts'
import { seededRng } from './particleUtil.ts'

const OPTS: RecolorDipOptions = { maxGenerations: 2, spreadS: 1, dipS: 0.8, swapAtDip: 0.05 }
const N = 50
const DT = 1 / 60
const all = (): boolean => true

function dips(): RecolorDips {
  const d = new RecolorDips(N, seededRng(3), OPTS)
  for (let i = 0; i < N; i++) d.assign(i)
  return d
}

/** Step every particle from `t0` for `seconds`, calling `each` after each frame. */
function run(d: RecolorDips, t0: number, seconds: number, each?: (t: number) => void): number {
  let t = t0
  for (; t < t0 + seconds; t += DT) {
    for (let i = 0; i < N; i++) d.step(i, t)
    each?.(t)
  }
  return t
}

test('stopped: every live particle moves to the new generation at once, with no dip', () => {
  const d = dips()
  const live = (i: number): boolean => i % 2 === 0
  const gen = d.setPalette(false, 0, live)
  for (let i = 0; i < N; i++) {
    assert.equal(d.gen[i], live(i) ? gen : 0)
    assert.equal(d.dip(i, 0.4), 1)
  }
})

test('running within the cap: no particle dips', () => {
  const d = dips()
  d.setPalette(true, 0, all) // generation 1; floor is 0 with a cap of 2
  run(d, 0, 3, (t) => {
    for (let i = 0; i < N; i++) assert.equal(d.dip(i, t), 1)
  })
  assert.equal(d.oldestInUse(all), 0)
})

test('running past the cap: every particle reaches the newest generation, only at a dip bottom', () => {
  const d = dips()
  d.setPalette(true, 0, all)
  const gen = d.setPalette(true, 0, all) // generation 2; generation 0 is past the cap
  const prev = Uint32Array.from(d.gen)
  let snaps = 0
  run(d, 0, OPTS.spreadS + OPTS.dipS + 0.1, (t) => {
    for (let i = 0; i < N; i++) {
      if (d.gen[i] !== prev[i] && d.dip(i, t) > OPTS.swapAtDip) snaps++
    }
    prev.set(d.gen)
  })
  assert.equal(snaps, 0)
  for (let i = 0; i < N; i++) assert.equal(d.gen[i], gen)
  assert.equal(d.oldestInUse(all), gen)
})

test('dips are staggered across the spread', () => {
  const d = dips()
  d.setPalette(true, 0, all)
  d.setPalette(true, 0, all)
  const firstDip = new Float64Array(N).fill(-1)
  run(d, 0, OPTS.spreadS + OPTS.dipS, (t) => {
    for (let i = 0; i < N; i++) if (firstDip[i] < 0 && d.dip(i, t) < 1) firstDip[i] = t
  })
  const starts = [...firstDip].sort((a, b) => a - b)
  assert.ok(starts[0] >= 0)
  assert.ok(starts[N - 1] - starts[0] > OPTS.spreadS * 0.5, 'dips all started together')
})

test('a palette arriving after the bottom of a dip dips again rather than snapping', () => {
  const d = dips()
  d.setPalette(true, 0, all)
  d.setPalette(true, 0, all)
  // Some particles are past the bottom of their dip (recolored, still rising) when
  // the next palette lands; they are retargeted and must dip again.
  let t = run(d, 0, OPTS.spreadS * 0.5 + OPTS.dipS * 0.6)
  const retargeted: number[] = []
  for (let i = 0; i < N; i++) if (d.dip(i, t) < 1 && d.gen[i] === 2) retargeted.push(i)
  assert.ok(retargeted.length > 0, 'positive control: nobody was mid-rise')
  const gen = d.setPalette(true, t, all)
  const prev = Uint32Array.from(d.gen)
  let snaps = 0
  t = run(d, t, OPTS.spreadS + OPTS.dipS * 3, (now) => {
    for (let i = 0; i < N; i++) {
      if (d.gen[i] !== prev[i] && d.dip(i, now) > OPTS.swapAtDip) snaps++
    }
    prev.set(d.gen)
  })
  assert.equal(snaps, 0)
  for (const i of retargeted) assert.equal(d.gen[i], gen)
  // Everyone else is within the cap, which is all the cap promises.
  for (let i = 0; i < N; i++) assert.ok(d.gen[i] > gen - OPTS.maxGenerations)
})

test('assign gives a newborn the current generation and cancels its dip', () => {
  const d = dips()
  d.setPalette(true, 0, all)
  const gen = d.setPalette(true, 0, all)
  run(d, 0, 0.5)
  d.assign(7)
  assert.equal(d.gen[7], gen)
  assert.equal(d.dip(7, 0.6), 1)
})
