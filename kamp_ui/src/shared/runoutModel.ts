// KAMP-771: the model behind the Runout Now Playing background: a macro view of
// a spinning record's grooves, lit by a lamp held close.
//
// Framework-free so `node --test` covers it under strip-only TypeScript: no
// parameter properties, no enums, and sibling imports take a `.ts` specifier
// (KAMP-765). The engine (nowplaying/runout/runoutEngine.ts) only draws.
//
// Geometry belongs to the engine. The model speaks in two units it can own:
//   * screen angle, radians about the record's centre (the engine puts that
//     centre below the pane, so the view shows the top of the record and a
//     positive speed carries the grooves left to right);
//   * groove index, 0 at the outermost visible groove to GROOVES at the
//     innermost, fractional between grooves.
// Things riding the record (dust, the matrix number) are kept in screen angle and
// advanced by the platter speed, so nothing has to unwrap the platter's angle.

import { clampDt, poissonGap, smoothstep } from './rareSchedule.ts'
import { fnv1a } from './particleUtil.ts'
import type { RunoutPlayback } from './runoutPlayback.ts'

// --- tunables ------------------------------------------------------------------------
export const GROOVES = 40
const DT_MAX = 0.1 // s; a longer frame is clamped so nothing jumps
// Platter speed at play, rad/s. Far below 33⅓ rpm on purpose: at the radii in
// view this is a slow sweep, which is what the ticket asks for.
export const NOMINAL = 0.05
// First-order approach to the target speed. The sag is the slower of the two,
// so a pause reads as a platter losing power rather than braking.
const SPINUP_TAU = 0.6 // s
const SAG_TAU = 1.4 // s
// Below this fraction of NOMINAL away from the target, land on it exactly
// (KAMP-772: an eased value must actually arrive, or "stopped" is never true).
const LAND_EPS = 0.01
// The cursor nudge: rad/s per pane height of sideways travel, its cap, and how
// fast it dies away.
const NUDGE_GAIN = 0.2
export const NUDGE_MAX = 4 * NOMINAL
const NUDGE_TAU = 0.8 // s
// The track-change gap band: its half-width in grooves, and the time it takes to
// cross from outside the outermost groove to past the innermost.
export const GAP_W = 2.5
export const GAP_S = 5
// The runout: the last RUNOUT_S of a side. The grooves widen into the lead-out
// over RUNOUT_RAMP_S and then hold, locked. RUNOUT_RATE caps how fast the
// envelope moves, so a seek or a 4 Hz snapshot never steps it.
export const RUNOUT_S = 20
export const RUNOUT_RAMP_S = 6
const RUNOUT_RATE = 1 / 3 // per s
// When the window opens, the matrix number starts this far (in seconds at
// NOMINAL) left of centre, so it reaches the middle about halfway through.
const MATRIX_LEAD_S = RUNOUT_S / 2
// Dust: the rare moment. Spacing, the slowest platter it rides, and how far
// outside the view it enters and leaves.
const DUST_GAP_FLOOR_S = 20
const DUST_GAP_MEAN_S = 40
export const DUST_GAP_MAX_S = 90
const DUST_MIN_SPEED = 0.25 * NOMINAL
const DUST_MARGIN = 0.15 // rad
const DUST_MAX_S = 60
const DUST_FADE_S = 1

const TAU = 2 * Math.PI

/** A dust speck riding a groove. */
export interface Dust {
  angle: number // screen angle, rad
  groove: number // groove index
  age: number // s
}

/** How far into the runout the side is: 0 before the window, 1 once locked. */
export function runoutTarget(p: RunoutPlayback): number {
  if (!p.endOfSide || p.duration <= 0) return 0
  const remaining = Math.max(0, p.duration - p.position)
  return smoothstep((RUNOUT_S - remaining) / RUNOUT_RAMP_S)
}

export class RunoutPlatter {
  /** Platter angle, rad, in [0, 2π). Only the groove modulation's phase uses it. */
  angle: number
  /** How far the grooves have widened into the lead-out, 0..1. */
  runout = 0
  /** Screen angle of the matrix number. */
  matrixAngle = 0
  dust: Dust | null = null

  private readonly rng: () => number
  private base = 0 // rad/s, the motor's part of the speed
  private nudge = 0 // rad/s, the cursor's part
  private landed = false
  private lastX: number | null = null

  private playing = false
  private position = 0
  private duration = 0
  private endOfSide = false
  private target = 0 // runoutTarget, kept current as position advances

  // Groove modulation per album. `seed` is what the record shows; during a band,
  // the grooves the band has passed show `bandSeed`. `pendingSeed` is an album
  // change still waiting for its band.
  private seed = 0
  private bandSeed = 0
  private pendingSeed: number | null = null
  private band: number | null = null // groove index of the band's centre
  private bandQueued = false

  private viewMin = -2.4
  private viewMax = -0.7
  private dustIn: number

  constructor(rng: () => number) {
    this.rng = rng
    this.angle = rng() * TAU
    this.dustIn = this.dustGap()
  }

  /** Current angular speed, rad/s. Negative while the cursor drags it back. */
  get speed(): number {
    return this.base + this.nudge
  }

  /** The band's centre as a groove index, or null when none is passing. */
  get gapX(): number | null {
    return this.band
  }

  /** Dust alpha, 0..1: fades in on arrival and out before its time runs out. */
  get dustAlpha(): number {
    const d = this.dust
    if (!d) return 0
    return (
      smoothstep(d.age / DUST_FADE_S) *
      (1 - smoothstep((d.age - (DUST_MAX_S - DUST_FADE_S)) / DUST_FADE_S))
    )
  }

  /** Nothing would change from one frame to the next, so the engine can skip draws. */
  get idle(): boolean {
    return (
      !this.playing &&
      this.base === 0 &&
      this.nudge === 0 &&
      this.band == null &&
      !this.bandQueued &&
      this.dust == null &&
      this.runout === this.target
    )
  }

  /** The groove modulation seed at a groove index. */
  seedAt(groove: number): number {
    return this.band != null && groove < this.band ? this.bandSeed : this.seed
  }

  /** The screen-angle range the engine shows. */
  setView(min: number, max: number): void {
    this.viewMin = min
    this.viewMax = max
  }

  private get viewCentre(): number {
    return (this.viewMin + this.viewMax) / 2
  }

  // --- inputs ------------------------------------------------------------------------

  /**
   * A playback snapshot. The first one, and any while stopped, lands: the
   * platter is at its speed and the runout at its value, with nothing replayed.
   * Only a change seen while running animates.
   */
  setPlayback(p: RunoutPlayback, running: boolean): void {
    this.playing = p.playing
    this.position = p.position
    this.duration = p.duration
    this.endOfSide = p.endOfSide
    if (!running || !this.landed) {
      this.landed = true
      this.land()
      return
    }
    this.retarget()
  }

  /** The album and track keys. An album change waits for its band while running. */
  setIdentity(albumKey: string, _trackKey: string, running: boolean): void {
    const s = fnv1a(albumKey)
    if (!running) {
      this.seed = s
      this.bandSeed = s
      this.pendingSeed = null
      this.band = null
      this.bandQueued = false
      return
    }
    const latest = this.pendingSeed ?? (this.band != null ? this.bandSeed : this.seed)
    if (s === latest) return
    // The store can deliver the identity just after the track change: a band that
    // hasn't reached the grooves yet can still carry it.
    if (this.band != null && this.band <= 0 && !this.bandQueued) this.bandSeed = s
    else this.pendingSeed = s
  }

  /** id to a different id. Ignored while stopped, as the other backgrounds do. */
  trackChanged(running: boolean): void {
    if (!running) return
    // A band in flight is never moved back (KAMP-774): the next one follows it.
    if (this.band != null) this.bandQueued = true
    else this.startBand()
  }

  /**
   * The cursor's sideways position, in pane heights. Only sideways travel nudges
   * a platter, so the vertical position is never asked for.
   */
  pointerMove(x: number): void {
    if (this.lastX != null) {
      const n = this.nudge + NUDGE_GAIN * (x - this.lastX)
      this.nudge = Math.max(-NUDGE_MAX, Math.min(NUDGE_MAX, n))
    }
    this.lastX = x
  }

  pointerLeave(): void {
    this.lastX = null
  }

  /** The loop is starting again: land, so a hidden pane never replays a spin-up. */
  resume(): void {
    // `landed` is left alone: before any snapshot there is nothing to land on, and
    // the first one must still land rather than spin up.
    this.lastX = null
    this.nudge = 0
    this.base = this.playing ? NOMINAL : 0
    this.target = runoutTarget(this.snapshot())
    this.runout = this.target
  }

  /** Dev: a dust speck now (__kampGL.forcePop). */
  forceDust(): void {
    this.spawnDust()
    this.dustIn = this.dustGap()
  }

  // --- tick --------------------------------------------------------------------------

  tick(rawDt: number): void {
    const dt = clampDt(rawDt, DT_MAX)
    if (this.playing && this.duration > 0) {
      this.position = Math.min(this.duration, this.position + dt)
    }
    this.retarget()
    this.tickSpeed(dt)
    const step = this.speed * dt
    this.angle = (((this.angle + step) % TAU) + TAU) % TAU
    this.matrixAngle += step
    this.tickRunout(dt)
    this.tickBand(dt)
    this.tickDust(dt, step)
  }

  private tickSpeed(dt: number): void {
    const want = this.playing ? NOMINAL : 0
    const tau = want > this.base ? SPINUP_TAU : SAG_TAU
    this.base += (want - this.base) * (1 - Math.exp(-dt / tau))
    if (Math.abs(want - this.base) < LAND_EPS * NOMINAL) this.base = want
    this.nudge *= Math.exp(-dt / NUDGE_TAU)
    if (Math.abs(this.nudge) < LAND_EPS * NOMINAL) this.nudge = 0
  }

  private tickRunout(dt: number): void {
    const d = this.target - this.runout
    const max = RUNOUT_RATE * dt
    this.runout = Math.abs(d) <= max ? this.target : this.runout + Math.sign(d) * max
  }

  private tickBand(dt: number): void {
    if (this.band != null) {
      this.band += ((GROOVES + 2 * GAP_W) / GAP_S) * dt
      if (this.band > GROOVES + GAP_W) {
        this.seed = this.bandSeed
        this.band = null
        if (this.bandQueued) {
          this.bandQueued = false
          this.startBand()
        }
      }
    }
    // An album change no band will carry (the app restoring a track while the pane
    // is already running): land it rather than hold it forever.
    if (this.pendingSeed != null && this.band == null) {
      this.seed = this.pendingSeed
      this.bandSeed = this.pendingSeed
      this.pendingSeed = null
    }
  }

  private tickDust(dt: number, step: number): void {
    const d = this.dust
    if (d) {
      d.angle += step
      d.age += dt
      const gone =
        d.angle > this.viewMax + DUST_MARGIN ||
        d.angle < this.viewMin - 3 * DUST_MARGIN ||
        d.age > DUST_MAX_S
      if (gone) this.dust = null
      return
    }
    // The schedule only runs while the record turns, so dust never lands on a
    // stopped one.
    if (this.speed < DUST_MIN_SPEED) return
    this.dustIn -= dt
    if (this.dustIn <= 0) this.forceDust()
  }

  // --- helpers -----------------------------------------------------------------------

  private snapshot(): RunoutPlayback {
    return {
      playing: this.playing,
      position: this.position,
      duration: this.duration,
      endOfSide: this.endOfSide
    }
  }

  /** Recompute the runout target; the window opening sends the matrix number in. */
  private retarget(): void {
    const prev = this.target
    this.target = runoutTarget(this.snapshot())
    if (prev === 0 && this.target > 0) {
      this.matrixAngle = this.viewCentre - NOMINAL * MATRIX_LEAD_S
    }
  }

  private land(): void {
    this.base = this.playing ? NOMINAL : 0
    this.target = runoutTarget(this.snapshot())
    this.runout = this.target
    // A still frame shows the matrix number where it can be read.
    if (this.target > 0) this.matrixAngle = this.viewCentre
  }

  private startBand(): void {
    this.band = -GAP_W
    this.bandSeed = this.pendingSeed ?? this.seed
    this.pendingSeed = null
  }

  private spawnDust(): void {
    this.dust = {
      angle: this.viewMin - DUST_MARGIN,
      groove: (0.15 + 0.7 * this.rng()) * GROOVES,
      age: 0
    }
  }

  private dustGap(): number {
    return poissonGap(this.rng(), DUST_GAP_FLOOR_S, DUST_GAP_MEAN_S, DUST_GAP_MAX_S)
  }
}
