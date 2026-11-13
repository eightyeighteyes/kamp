import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DUST_GAP_MAX_S,
  GAP_S,
  GAP_W,
  GROOVES,
  MATRIX_REST,
  NOMINAL,
  NUDGE_MAX,
  RUNOUT_RAMP_S,
  RUNOUT_S,
  RunoutPlatter,
  runoutTarget
} from './runoutModel.ts'
import { fnv1a, seededRng } from './particleUtil.ts'
import type { RunoutPlayback } from './runoutPlayback.ts'

const DT = 1 / 30

const play = (o: Partial<RunoutPlayback> = {}): RunoutPlayback => ({
  playing: true,
  position: 10,
  duration: 200,
  endOfSide: false,
  disc: 1,
  ...o
})

function run(p: RunoutPlatter, seconds: number): void {
  for (let t = 0; t < seconds; t += DT) p.tick(DT)
}

/** A platter that has been mounted, shown playing, and is running. */
function running(o: Partial<RunoutPlayback> = {}): RunoutPlatter {
  const p = new RunoutPlatter(seededRng(1))
  p.setIdentity('album:1', '1', false)
  p.setPlayback(play(o), false)
  p.resume()
  return p
}

// --- platter speed --------------------------------------------------------------

test('mounting while playing lands at speed: no spin-up on mount', () => {
  const p = new RunoutPlatter(seededRng(1))
  p.setPlayback(play(), false)
  assert.equal(p.speed, NOMINAL)
})

test('mounting while paused lands stopped', () => {
  const p = new RunoutPlatter(seededRng(1))
  p.setPlayback(play({ playing: false }), false)
  assert.equal(p.speed, 0)
})

test('the first snapshot lands even if the loop is already running', () => {
  const p = new RunoutPlatter(seededRng(1))
  p.resume()
  p.setPlayback(play(), true)
  assert.equal(p.speed, NOMINAL)
})

test('resuming after the pane was hidden lands at the current speed', () => {
  const p = running({ playing: false })
  // Hidden: playback changes arrive while stopped.
  p.setPlayback(play({ playing: true }), false)
  p.resume()
  assert.equal(p.speed, NOMINAL)
})

test('a resume never replays a spin-up that started before the stop', () => {
  const p = running({ playing: false })
  p.setPlayback(play({ playing: true }), true) // live edge: spin-up begins
  p.tick(DT)
  assert.ok(p.speed < NOMINAL)
  p.resume()
  assert.equal(p.speed, NOMINAL)
})

test('play while running spins up gradually, then lands exactly at speed', () => {
  const p = running({ playing: false })
  p.setPlayback(play({ playing: true }), true)
  p.tick(DT)
  assert.ok(p.speed > 0 && p.speed < NOMINAL / 2, `speed ${p.speed}`)
  run(p, 5)
  assert.equal(p.speed, NOMINAL)
})

test('pause while running sags gradually, then stops at exactly zero', () => {
  const p = running()
  p.setPlayback(play({ playing: false }), true)
  p.tick(DT)
  assert.ok(p.speed > NOMINAL / 2 && p.speed < NOMINAL, `speed ${p.speed}`)
  run(p, 8)
  assert.equal(p.speed, 0)
})

test('the sag is slower than the spin-up', () => {
  const up = running({ playing: false })
  up.setPlayback(play({ playing: true }), true)
  run(up, 0.5)
  const down = running()
  down.setPlayback(play({ playing: false }), true)
  run(down, 0.5)
  // Fraction of the way to the target after the same time, with a margin: equal
  // time constants give equal fractions, which float noise could order either way.
  const upDone = up.speed / NOMINAL
  const downDone = 1 - down.speed / NOMINAL
  assert.ok(upDone > downDone + 0.1, `up ${upDone} down ${downDone}`)
})

test('the angle advances with the speed and stays wrapped in [0, 2π)', () => {
  const p = running()
  const a0 = p.angle
  p.tick(DT)
  assert.ok(Math.abs(p.angle - a0 - NOMINAL * DT) < 1e-9)
  run(p, (4 * Math.PI) / NOMINAL)
  assert.ok(p.angle >= 0 && p.angle < 2 * Math.PI)
})

test('a stopped platter does not turn', () => {
  const p = running({ playing: false })
  const a0 = p.angle
  run(p, 2)
  assert.equal(p.angle, a0)
})

// --- cursor nudge ----------------------------------------------------------------

test('moving the cursor right speeds the platter up, then it eases back', () => {
  const p = running()
  p.pointerMove(0.2)
  p.pointerMove(0.5)
  assert.ok(p.speed > NOMINAL)
  run(p, 6)
  assert.equal(p.speed, NOMINAL)
})

test('moving the cursor left drags the platter back', () => {
  const p = running()
  p.pointerMove(0.6)
  p.pointerMove(0.3)
  assert.ok(p.speed < NOMINAL)
})

test('a cursor that moves only vertically (same x) does not nudge', () => {
  const p = running()
  p.pointerMove(0.5)
  p.pointerMove(0.5)
  assert.equal(p.speed, NOMINAL)
})

test('the first move after the cursor enters does not nudge', () => {
  const p = running()
  p.pointerMove(0.1)
  p.pointerLeave()
  p.pointerMove(0.9) // re-entry far away: not a swipe
  assert.equal(p.speed, NOMINAL)
})

test('the nudge is capped', () => {
  const p = running()
  p.pointerMove(0)
  for (let i = 0; i < 50; i++) p.pointerMove(i % 2 === 0 ? 2 : 0)
  for (let i = 0; i < 50; i++) p.pointerMove(i)
  assert.ok(p.speed <= NOMINAL + NUDGE_MAX + 1e-12)
})

test('a nudge on a stopped platter dies away to exactly zero', () => {
  const p = running({ playing: false })
  p.pointerMove(0.2)
  p.pointerMove(0.5)
  assert.ok(p.speed > 0)
  run(p, 8)
  assert.equal(p.speed, 0)
})

// --- gap band ----------------------------------------------------------------------

test('a track change while running sends a band across the grooves', () => {
  const p = running()
  assert.equal(p.gapX, null)
  p.trackChanged(true)
  assert.ok(p.gapX != null && p.gapX < 0, 'enters from outside the grooves')
  run(p, GAP_S / 2)
  assert.ok(p.gapX! > 0 && p.gapX! < GROOVES)
  run(p, GAP_S / 2 + 0.5)
  assert.equal(p.gapX, null)
})

test('a track change while stopped sends no band', () => {
  const p = running()
  p.trackChanged(false)
  assert.equal(p.gapX, null)
})

test('a second track change during a band never moves the band back', () => {
  const p = running()
  p.trackChanged(true)
  run(p, GAP_S / 2)
  const x = p.gapX!
  p.trackChanged(true)
  assert.ok(p.gapX! >= x)
})

test('a second track change during a band sends another once it passes', () => {
  const p = running()
  p.trackChanged(true)
  run(p, GAP_S / 2)
  p.trackChanged(true)
  run(p, GAP_S / 2 + 0.2)
  assert.ok(p.gapX != null && p.gapX < GROOVES / 2, 'a fresh band is under way')
})

test('a band travels without the platter turning', () => {
  const p = running({ playing: false })
  p.trackChanged(true)
  run(p, GAP_S / 2)
  assert.ok(p.gapX! > 0)
})

// --- album change: groove modulation -----------------------------------------------

test('an album change while running is revealed behind the band, never snapped', () => {
  const p = running()
  const old = fnv1a('album:1')
  const next = fnv1a('album:2')
  p.setIdentity('album:2', '2', true)
  p.trackChanged(true)
  // The whole record still shows the old album as the band enters.
  assert.equal(p.seedAt(0), old)
  assert.equal(p.seedAt(GROOVES - 1), old)
  run(p, GAP_S / 2)
  const x = p.gapX!
  assert.equal(p.seedAt(x - 1), next, 'behind the band: the new album')
  assert.equal(p.seedAt(x + 1), old, 'ahead of the band: the old album')
  run(p, GAP_S)
  assert.equal(p.seedAt(0), next)
  assert.equal(p.seedAt(GROOVES - 1), next)
})

test('a skip within the album keeps the grooves', () => {
  const p = running()
  p.setIdentity('album:1', '2', true)
  p.trackChanged(true)
  run(p, GAP_S / 2)
  assert.equal(p.seedAt(0), fnv1a('album:1'))
})

test('an album change while stopped lands at once', () => {
  const p = running()
  p.setIdentity('album:2', '2', false)
  assert.equal(p.seedAt(0), fnv1a('album:2'))
})

test('an album change with no band (a restore) lands on the next tick', () => {
  const p = running()
  p.setIdentity('album:2', '2', true)
  p.tick(DT)
  assert.equal(p.seedAt(0), fnv1a('album:2'))
})

test('the identity may arrive after the track change, as the store can order it', () => {
  const p = running()
  p.trackChanged(true)
  p.setIdentity('album:2', '2', true)
  // The band carries it: revealed behind the band, not snapped once it has passed.
  run(p, GAP_S / 2)
  assert.equal(p.seedAt(p.gapX! - 1), fnv1a('album:2'))
  run(p, GAP_S / 2 + 0.5)
  assert.equal(p.seedAt(0), fnv1a('album:2'))
})

// --- runout -------------------------------------------------------------------------

test('runoutTarget: nothing until the last RUNOUT_S of a side', () => {
  assert.equal(runoutTarget(play({ endOfSide: true, position: 200 - RUNOUT_S - 1 })), 0)
  assert.equal(runoutTarget(play({ endOfSide: false, position: 199 })), 0)
})

test('runoutTarget ramps over RUNOUT_RAMP_S, then holds locked', () => {
  const mid = runoutTarget(play({ endOfSide: true, position: 200 - RUNOUT_S + RUNOUT_RAMP_S / 2 }))
  assert.ok(mid > 0 && mid < 1)
  assert.equal(runoutTarget(play({ endOfSide: true, position: 200 - RUNOUT_S + RUNOUT_RAMP_S })), 1)
  assert.equal(runoutTarget(play({ endOfSide: true, position: 199 })), 1)
})

test('runoutTarget is zero while the duration is unknown', () => {
  assert.equal(runoutTarget(play({ endOfSide: true, duration: 0, position: 3 })), 0)
})

test('the runout lands at once while stopped', () => {
  const p = new RunoutPlatter(seededRng(1))
  p.setPlayback(play({ endOfSide: true, position: 195 }), false)
  assert.equal(p.runout, 1)
})

test('the runout eases in while running, with no step from a snapshot', () => {
  const p = running({ endOfSide: true, position: 200 - RUNOUT_S - 1 })
  assert.equal(p.runout, 0)
  p.setPlayback(play({ endOfSide: true, position: 199 }), true) // a jump forward
  p.tick(DT)
  assert.ok(p.runout > 0 && p.runout < 0.1, `runout ${p.runout}`)
  run(p, 10)
  assert.equal(p.runout, 1)
})

test('position is interpolated between snapshots while playing', () => {
  // Just before the window: playing on alone must carry it in.
  const p = running({ endOfSide: true, position: 200 - RUNOUT_S - 0.5 })
  run(p, RUNOUT_RAMP_S + 3)
  assert.ok(p.runout > 0.5)
})

test('position does not advance while paused', () => {
  const p = running({ playing: false, endOfSide: true, position: 200 - RUNOUT_S - 0.5 })
  run(p, RUNOUT_RAMP_S + 3)
  assert.equal(p.runout, 0)
})

test('seeking back out of the runout unwinds it smoothly', () => {
  const p = running({ endOfSide: true, position: 199 })
  assert.equal(p.runout, 1)
  p.setPlayback(play({ endOfSide: true, position: 20 }), true)
  p.tick(DT)
  assert.ok(p.runout < 1 && p.runout > 0.9)
  run(p, 10)
  assert.equal(p.runout, 0)
})

test('the matrix number rides the platter into view as the runout begins', () => {
  const p = running({ endOfSide: true, position: 200 - RUNOUT_S - 0.1 })
  p.setView(-2.4, -0.7)
  run(p, 0.5) // the window opens
  const a0 = p.matrixAngle
  assert.ok(a0 < (-2.4 + -0.7) / 2, 'starts left of centre')
  run(p, 2)
  assert.ok(p.matrixAngle > a0, 'moves with the platter')
})

test('a still frame rests the matrix number in the strip left of the art', () => {
  const p = new RunoutPlatter(seededRng(1))
  p.setView(-2.4, -0.7)
  p.setPlayback(play({ endOfSide: true, position: 199 }), false)
  assert.ok(Math.abs(p.matrixAngle - (-2.4 + MATRIX_REST * 1.7)) < 1e-9)
})

// --- dust -----------------------------------------------------------------------------

test('dust arrives within the cap while the record turns', () => {
  const p = running()
  p.setView(-2.4, -0.7)
  let seen = false
  for (let t = 0; t < DUST_GAP_MAX_S + 1 && !seen; t += DT) {
    p.tick(DT)
    seen = p.dust != null
  }
  assert.ok(seen)
})

test('dust enters from the left and rides the groove to the right', () => {
  const p = running()
  p.setView(-2.4, -0.7)
  p.forceDust()
  const d = p.dust!
  assert.ok(d.angle < -2.4)
  const a0 = d.angle
  run(p, 1)
  assert.ok(p.dust!.angle > a0)
  assert.ok(d.groove > 0 && d.groove < GROOVES)
})

test('dust leaves once it is past the view', () => {
  const p = running()
  p.setView(-2.4, -0.7)
  p.forceDust()
  let gone = false
  for (let t = 0; t < (1.7 + 1) / NOMINAL && !gone; t += DT) {
    p.tick(DT)
    gone = p.dust == null
  }
  assert.ok(gone)
})

test('no dust arrives on a stopped record', () => {
  const p = running({ playing: false })
  p.setView(-2.4, -0.7)
  run(p, DUST_GAP_MAX_S + 5)
  assert.equal(p.dust, null)
})

// --- idle -------------------------------------------------------------------------------

test('a paused, settled platter is idle; playing is not', () => {
  assert.equal(running({ playing: false }).idle, true)
  assert.equal(running().idle, false)
})

test('a band or a nudge wakes an idle platter', () => {
  const a = running({ playing: false })
  a.trackChanged(true)
  assert.equal(a.idle, false)
  const b = running({ playing: false })
  b.pointerMove(0.1)
  b.pointerMove(0.4)
  assert.equal(b.idle, false)
})

test('a sagging platter is not idle until it stops', () => {
  const p = running()
  p.setPlayback(play({ playing: false }), true)
  p.tick(DT)
  assert.equal(p.idle, false)
  run(p, 8)
  assert.equal(p.idle, true)
})

test('band width is a few grooves', () => {
  assert.ok(GAP_W >= 1 && GAP_W < GROOVES / 4)
})
