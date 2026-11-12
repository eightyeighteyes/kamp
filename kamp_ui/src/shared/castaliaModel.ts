// KAMP-773: the model behind the Castalia Now Playing background. Fine grains on
// a dark plate settle onto the nodal lines of a resonating surface (Chladni
// figures), one figure per album and a neighbouring harmonic per track.
//
// Framework-free so `node --test` covers it under strip-only TypeScript: no
// parameter properties, no enums, and sibling imports take a `.ts` specifier
// (KAMP-765). The engine (nowplaying/castalia/castaliaEngine.ts) only draws.
//
// Grains live in plate coordinates (u, v), both 0..1 across the whole pane, so a
// resize keeps the figure registered to the pane. Velocities are in pane heights
// per second, so motion reads the same whatever the pane's shape. The plate is
// the whole pane: its nodal lines run on under the art and the full figure reads
// once the art is hidden (KAMP-769).
//
// There is no simulation grid. The field is closed-form, so each grain samples it
// and its gradient where it stands.

import { clampDt, poissonGap } from './rareSchedule.ts'
import { lerp, pick, seededRng } from './particleUtil.ts'
import { RecolorDips } from './recolorDips.ts'

// --- the plate ---------------------------------------------------------------------

/** One free-plate mode: f = (cos nπu·cos mπv + sign·cos mπu·cos nπv) / 2. */
export interface Mode {
  m: number
  n: number
  sign: 1 | -1
}

// Hand-picked (m, n), m < n, each in both signs. Below m + n = 5 a figure is a
// couple of lines and reads as empty; much past 10 it reads as noise in the side
// strips beside the art. (m, n) and (n, m) share a zero set, so m < n suffices,
// and m = n with sign -1 is identically zero, so it never appears.
const MODE_PAIRS: ReadonlyArray<readonly [number, number]> = [
  [1, 4],
  [1, 5],
  [2, 3],
  [2, 4],
  [2, 5],
  [2, 6],
  [3, 4],
  [3, 5],
  [3, 6],
  [4, 5]
]

export const MODES: readonly Mode[] = MODE_PAIRS.flatMap(([m, n]) => [
  { m, n, sign: 1 as const },
  { m, n, sign: -1 as const }
])

/**
 * Each mode's neighbouring harmonics, as indices into MODES: one step in m or n
 * with the same sign, or the same (m, n) with the sign flipped. Derived from the
 * curated table, so a neighbour is always a curated figure too.
 */
export const NEIGHBOURS: ReadonlyArray<readonly number[]> = MODES.map((a, i) =>
  MODES.flatMap((b, j) => {
    if (i === j) return []
    const step = Math.abs(a.m - b.m) + Math.abs(a.n - b.n)
    const near = (step === 1 && a.sign === b.sign) || (step === 0 && a.sign !== b.sign)
    return near ? [j] : []
  })
)

/** The plate's displacement at (u, v). |f| <= 1. */
export function chladni(mode: Mode, u: number, v: number): number {
  const { m, n, sign } = mode
  const pu = Math.PI * u
  const pv = Math.PI * v
  return (Math.cos(n * pu) * Math.cos(m * pv) + sign * Math.cos(m * pu) * Math.cos(n * pv)) / 2
}

/** f and its partial derivatives in u and v, written into `out` as [f, df/du, df/dv]. */
export function chladniGrad(mode: Mode, u: number, v: number, out: Float64Array): void {
  const { m, n, sign } = mode
  const pu = Math.PI * u
  const pv = Math.PI * v
  const cnu = Math.cos(n * pu)
  const snu = Math.sin(n * pu)
  const cmu = Math.cos(m * pu)
  const smu = Math.sin(m * pu)
  const cmv = Math.cos(m * pv)
  const smv = Math.sin(m * pv)
  const cnv = Math.cos(n * pv)
  const snv = Math.sin(n * pv)
  out[0] = (cnu * cmv + sign * cmu * cnv) / 2
  out[1] = (-Math.PI / 2) * (n * snu * cmv + sign * m * smu * cnv)
  out[2] = (-Math.PI / 2) * (m * cnu * smv + sign * n * cmu * snv)
}

/** FNV-1a, 32-bit: a stable hash for album and track keys. */
export function fnv1a(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** The album's own figure: its base mode. */
export function albumMode(albumKey: string): number {
  return fnv1a(albumKey) % MODES.length
}

/** The figures an album's tracks choose between: its base mode, then its neighbours. */
export function harmonicSet(base: number): number[] {
  return [base, ...NEIGHBOURS[base]]
}

// --- tunables ------------------------------------------------------------------------
export const GRAINS = 500
const DT_MAX = 0.1 // s; a longer frame is clamped so nothing jumps
// Settling. The pull is -FORCE * d(f²)/dx, with the gradient normalised by the
// mode's wavenumber so every figure drifts at about the same pace. DAMP makes the
// motion overdamped: grains creep onto the lines, they don't oscillate across them.
// Measured: at 0.3 a scattered plate is about 60% on its lines after 5 s and 90%
// after 20 s, across the table. At 0.1 it was still only ~65% after 20 s.
const FORCE = 0.3
const DAMP = 3 // 1 / s
// Grains bounce where the plate moves most, in proportion to |f|. That is what
// carries a grain off an antinode, where the gradient is zero and the pull alone
// would leave it sitting forever. JITTER_FLOOR keeps a settled line faintly alive.
const JITTER = 0.1 // pane heights / s per sqrt(s), at |f| = 1
const JITTER_FLOOR = 0.006
export const VMAX = 0.3 // pane heights / s
const WALL_RESTITUTION = 0.5
// The still frame and the prewarm accept a point with weight exp(-(f / W)²): a
// smooth falloff onto the line, never a hard |f| < eps edge (KAMP-774).
const SETTLED_W = 0.04
const SETTLE_TRIES = 80
const GRAIN_ALPHA: [number, number] = [0.3, 0.5]
// Palette slot weights: the dominant swatches carry the figure.
const SLOT_WEIGHTS = [0.32, 0.24, 0.18, 0.14, 0.12]

// Track change: the plate rings. Every grain takes a bounded kick and the bounce
// is boosted on an envelope that decays, then the grains resettle on the new figure.
const RING_KICK = 0.15 // pane heights / s
const RING_JITTER = 4 // extra multiple of the bounce at the envelope's peak
const RING_TAU = 1 // s
// A new album brings a new palette, which loads after the track id changes; the
// ring waits for it so the grains resettle in the new colors (KAMP-767). If it
// never comes (identical art), this is how long the ring waits.
export const RING_FALLBACK_S = 0.7

// Cursor: grains near a moving cursor jostle, as if a finger touched the plate.
const JOSTLE_R = 0.07 // pane heights
const JOSTLE_MIN_SPEED = 0.2 // pane heights / s
const JOSTLE_REF_SPEED = 1.5 // speed at which the jostle is at full strength
// Additive, not a multiple of the plate's own bounce: a settled grain sits where
// |f| is ~0, so multiplying its bounce barely moves it.
const JOSTLE_JITTER = 0.15 // pane heights / s per sqrt(s), at full strength
const JOSTLE_PUSH = 2 // pane heights / s², away from the cursor
const CURSOR_V_MAX = 3

// The rare shimmer: the figure is perturbed toward a neighbouring mode and relaxes
// back. f + s * f_neighbour with s small, so the nodal lines deform continuously;
// never a crossfade between two fields, whose midpoint has no lines of its own
// to settle on (KAMP-774).
export const SHIMMER_PEAK = 0.15
export const SHIMMER_S = 6
export const SHIMMER_GAP_FLOOR_S = 40
const SHIMMER_GAP_MEAN_S = 80
export const SHIMMER_GAP_MAX_S = 200
const SHIMMER_IDLE_S = 4 // the cursor still this long first
const SHIMMER_QUIET_RING = 0.05 // and the plate this quiet

// Recolor. Grains never die, so every palette dips them into the new colors; a
// cap of 1 forces a dip on each palette instead of waiting for turnover.
const RECOLOR = { maxGenerations: 1, spreadS: 1.5, dipS: 0.8, swapAtDip: 0.05 }

export interface PlateOptions {
  /** Scales the pull onto the lines; 0 disables it (tests). */
  force?: number
  /** Scales all the bounce, floor included; 0 disables it (tests). */
  jitter?: number
}

/** Irwin-Hall approximation of a unit normal: cheap, bounded, mean 0, variance 1. */
function gauss(r: () => number): number {
  return (r() + r() + r() - 1.5) * 2
}

export class CastaliaPlate {
  // Struct-of-arrays. Read by the engine; written only here.
  readonly u = new Float64Array(GRAINS)
  readonly v = new Float64Array(GRAINS)
  readonly vx = new Float64Array(GRAINS)
  readonly vy = new Float64Array(GRAINS)
  readonly slot = new Uint8Array(GRAINS)
  readonly baseAlpha = new Float64Array(GRAINS)

  /** Seconds the loop has run. Every schedule is on this clock, so a stop freezes it. */
  elapsed = 0
  /** Index into MODES of the figure the grains are settling onto. */
  mode = 0
  /** 0..1, the ring's envelope. */
  ringLevel = 0
  /** The shimmer's current amount, 0..SHIMMER_PEAK; exactly 0 when idle. */
  shimmer = 0
  /** `elapsed` at which the shimmer is next due. */
  nextShimmerAt: number

  private readonly rng: () => number
  private readonly recolor: RecolorDips
  private readonly force: number
  private readonly jitter: number
  private readonly g = new Float64Array(3)
  private readonly gn = new Float64Array(3)
  private readonly isLive = (): boolean => true
  private aspect = 1
  private placed = false

  private albumKey: string | null = null
  private seed = 0
  private staged = -1
  private newAlbum = false
  private ringPending = false
  private ringPendingAt = 0

  private shimmerT = -1
  private shimmerMode = 0

  private pointerPresent = false
  private px = 0
  private py = 0
  private prevX = 0
  private prevY = 0
  private cursorVx = 0
  private cursorVy = 0
  private lastPointerAt = -Infinity

  constructor(rng: () => number, opts: PlateOptions = {}) {
    this.rng = rng
    this.recolor = new RecolorDips(GRAINS, rng, RECOLOR)
    this.force = FORCE * (opts.force ?? 1)
    this.jitter = opts.jitter ?? 1
    this.nextShimmerAt = this.shimmerGap()
    for (let i = 0; i < GRAINS; i++) {
      this.slot[i] = pick(SLOT_WEIGHTS, rng())
      this.baseAlpha[i] = lerp(GRAIN_ALPHA[0], GRAIN_ALPHA[1], rng())
      this.recolor.assign(i)
    }
  }

  /** Each grain's palette generation. Read by the engine to pick a sprite sheet. */
  get gen(): Uint32Array {
    return this.recolor.gen
  }

  get generation(): number {
    return this.recolor.generation
  }

  // --- inputs ------------------------------------------------------------------------

  /** The pane's width / height. */
  setBounds(aspect: number): void {
    this.aspect = Math.max(0.05, aspect)
  }

  /**
   * The playing track's identity: its album key and its own key. Called on mount
   * and before every trackChanged(). While stopped, or on the first call, the new
   * figure lands at once (re-placing the grains already on the plate), so a still
   * frame shows it. While running it is staged: a ring that follows applies it,
   * and otherwise (the app's null -> id restore) the next tick morphs quietly.
   */
  setIdentity(albumKey: string, trackKey: string, running: boolean): void {
    const set = harmonicSet(albumMode(albumKey))
    const sameAlbum = albumKey === this.albumKey
    const showing = this.staged >= 0 ? this.staged : this.mode
    let h = fnv1a(trackKey) % set.length
    // A skip within the album must visibly change the figure.
    if (sameAlbum && set[h] === showing) h = (h + 1) % set.length
    const next = set[h]
    this.newAlbum = this.albumKey != null && !sameAlbum
    this.albumKey = albumKey
    this.seed = fnv1a(albumKey) ^ fnv1a(trackKey)
    if (!running || !this.placed) {
      this.staged = -1
      if (next !== this.mode || !sameAlbum) {
        this.mode = next
        if (this.placed) this.settleAll()
      }
      return
    }
    this.staged = next
  }

  /** id to a different id. Ignored while stopped, so nothing fires late. */
  trackChanged(running: boolean): void {
    if (!running) return
    if (this.newAlbum) {
      this.newAlbum = false
      this.ringPending = true
      this.ringPendingAt = this.elapsed
      return
    }
    this.ring()
  }

  /** A new palette arrived; returns its generation for the engine to bake. */
  setPalette(running: boolean): number {
    const gen = this.recolor.setPalette(running, this.elapsed, this.isLive)
    if (this.ringPending) this.ring()
    return gen
  }

  /** The cursor, in pane heights. The first move after an entry is not a velocity. */
  pointerMove(x: number, y: number): void {
    if (!this.pointerPresent) {
      // Entry starts a new stroke, so exit and re-entry never read as a sweep (KAMP-772).
      this.pointerPresent = true
      this.prevX = x
      this.prevY = y
    }
    this.px = x
    this.py = y
    this.lastPointerAt = this.elapsed
  }

  pointerLeave(): void {
    this.pointerPresent = false
    this.cursorVx = this.cursorVy = 0
    this.lastPointerAt = this.elapsed
  }

  /** Called on start(): drop everything that would otherwise replay late. */
  resume(): void {
    this.ringPending = false
    this.newAlbum = false
    this.pointerPresent = false
    this.cursorVx = this.cursorVy = 0
  }

  /** Place every grain on the settled figure, already at rest. */
  prewarm(): void {
    this.placed = true
    this.settleAll()
  }

  /** Start a shimmer now (dev hook). */
  forceShimmer(): void {
    if (this.shimmerT < 0) this.startShimmer()
  }

  /** Ring onto a given mode (dev hook, to review the table). */
  ringTo(mode: number): void {
    this.staged = ((mode % MODES.length) + MODES.length) % MODES.length
    this.ring()
  }

  // --- queries -----------------------------------------------------------------------

  /** The field at a plate point, shimmer included. */
  field(u: number, v: number): number {
    const f = chladni(MODES[this.mode], u, v)
    return this.shimmer > 0 ? f + this.shimmer * chladni(MODES[this.shimmerMode], u, v) : f
  }

  alpha(i: number): number {
    return this.baseAlpha[i] * this.recolor.dip(i, this.elapsed)
  }

  oldestGenerationInUse(): number {
    return this.recolor.oldestInUse(this.isLive)
  }

  /** True while a ring waits for its palette. */
  get ringIsPending(): boolean {
    return this.ringPending
  }

  // --- simulation --------------------------------------------------------------------

  tick(dtRaw: number): void {
    const dt = clampDt(dtRaw, DT_MAX)
    if (dt <= 0) return
    this.elapsed += dt
    if (this.ringPending && this.elapsed - this.ringPendingAt >= RING_FALLBACK_S) this.ring()
    // Staged with no ring coming: the app's restore. Morph quietly.
    if (this.staged >= 0 && !this.ringPending) {
      this.mode = this.staged
      this.staged = -1
    }
    this.ringLevel *= Math.exp(-dt / RING_TAU)
    this.updateCursor(dt)
    this.updateShimmer(dt)
    this.integrate(dt)
  }

  private ring(): void {
    this.ringPending = false
    this.newAlbum = false
    if (this.staged >= 0) {
      this.mode = this.staged
      this.staged = -1
    }
    // A ring during a ring lifts the envelope back to its peak; it never restarts
    // it from zero and never moves a grain (KAMP-774 re-trigger rule).
    this.ringLevel = 1
    const r = this.rng
    for (let i = 0; i < GRAINS; i++) {
      const a = r() * 2 * Math.PI
      const k = RING_KICK * lerp(0.5, 1, r())
      this.vx[i] += Math.cos(a) * k
      this.vy[i] += Math.sin(a) * k
      this.capSpeed(i)
    }
  }

  private updateCursor(dt: number): void {
    if (!this.pointerPresent) {
      this.cursorVx = this.cursorVy = 0
      return
    }
    let vx = (this.px - this.prevX) / dt
    let vy = (this.py - this.prevY) / dt
    this.prevX = this.px
    this.prevY = this.py
    const speed = Math.hypot(vx, vy)
    if (speed > CURSOR_V_MAX) {
      vx *= CURSOR_V_MAX / speed
      vy *= CURSOR_V_MAX / speed
    }
    this.cursorVx = vx
    this.cursorVy = vy
  }

  private shimmerGap(): number {
    return poissonGap(this.rng(), SHIMMER_GAP_FLOOR_S, SHIMMER_GAP_MEAN_S, SHIMMER_GAP_MAX_S)
  }

  private startShimmer(): void {
    const nb = NEIGHBOURS[this.mode]
    this.shimmerMode = nb[Math.floor(this.rng() * nb.length)]
    this.shimmerT = 0
  }

  private updateShimmer(dt: number): void {
    if (this.shimmerT < 0) {
      if (this.elapsed < this.nextShimmerAt) return
      if (this.elapsed - this.lastPointerAt < SHIMMER_IDLE_S) return
      if (this.ringLevel > SHIMMER_QUIET_RING || this.ringPending) return
      this.startShimmer()
    }
    this.shimmerT += dt
    if (this.shimmerT >= SHIMMER_S) {
      // Land exactly on zero (KAMP-772): the figure is fully back.
      this.shimmer = 0
      this.shimmerT = -1
      this.nextShimmerAt = this.elapsed + this.shimmerGap()
      return
    }
    // sin² rises and falls with zero slope at both ends.
    const s = Math.sin((Math.PI * this.shimmerT) / SHIMMER_S)
    this.shimmer = SHIMMER_PEAK * s * s
  }

  private integrate(dt: number): void {
    const asp = this.aspect
    const mode = MODES[this.mode]
    const nbMode = MODES[this.shimmerMode]
    const s = this.shimmer
    const k = Math.PI * Math.max(mode.m, mode.n)
    const damp = Math.exp(-DAMP * dt)
    const sq = Math.sqrt(dt)
    const ringBoost = 1 + RING_JITTER * this.ringLevel
    const cs = Math.hypot(this.cursorVx, this.cursorVy)
    const jostling = this.pointerPresent && cs >= JOSTLE_MIN_SPEED
    const jostleK = Math.min(1, cs / JOSTLE_REF_SPEED)
    const g = this.g
    const gn = this.gn
    const r = this.rng
    for (let i = 0; i < GRAINS; i++) {
      const u = this.u[i]
      const v = this.v[i]
      chladniGrad(mode, u, v, g)
      if (s > 0) {
        chladniGrad(nbMode, u, v, gn)
        g[0] += s * gn[0]
        g[1] += s * gn[1]
        g[2] += s * gn[2]
      }
      const f = g[0]
      // d(f²)/dx = 2 f df/dx, in pane heights (x = u * aspect, y = v).
      let ax = (-this.force * 2 * f * (g[1] / asp)) / k
      let ay = (-this.force * 2 * f * g[2]) / k
      let amp = this.jitter * (JITTER * Math.abs(f) + JITTER_FLOOR) * ringBoost
      if (jostling) {
        const x = u * asp
        const dx = x - this.px
        const dy = v - this.py
        const d = Math.hypot(dx, dy)
        if (d < JOSTLE_R) {
          const w = 1 - d / JOSTLE_R
          const ww = w * w * (3 - 2 * w) * jostleK
          amp += JOSTLE_JITTER * ww
          if (d > 1e-6) {
            ax += (dx / d) * JOSTLE_PUSH * ww
            ay += (dy / d) * JOSTLE_PUSH * ww
          }
        }
      }
      this.vx[i] = (this.vx[i] + ax * dt) * damp + amp * gauss(r) * sq
      this.vy[i] = (this.vy[i] + ay * dt) * damp + amp * gauss(r) * sq
      this.capSpeed(i)
      this.move(i, dt)
    }
  }

  private capSpeed(i: number): void {
    const sp = Math.hypot(this.vx[i], this.vy[i])
    if (sp > VMAX) {
      this.vx[i] *= VMAX / sp
      this.vy[i] *= VMAX / sp
    }
  }

  /** Advance one grain, reflecting off the plate's edges so none ever leaves it. */
  private move(i: number, dt: number): void {
    const asp = this.aspect
    let x = this.u[i] * asp + this.vx[i] * dt
    let y = this.v[i] + this.vy[i] * dt
    if (x < 0) {
      x = -x
      this.vx[i] = -this.vx[i] * WALL_RESTITUTION
    } else if (x > asp) {
      x = 2 * asp - x
      this.vx[i] = -this.vx[i] * WALL_RESTITUTION
    }
    if (y < 0) {
      y = -y
      this.vy[i] = -this.vy[i] * WALL_RESTITUTION
    } else if (y > 1) {
      y = 2 - y
      this.vy[i] = -this.vy[i] * WALL_RESTITUTION
    }
    this.u[i] = Math.min(1, Math.max(0, x / asp))
    this.v[i] = Math.min(1, Math.max(0, y))
    this.recolor.step(i, this.elapsed)
  }

  /** Every grain onto the current figure, at rest, deterministically for the track. */
  private settleAll(): void {
    settledPositions(MODES[this.mode], seededRng(this.seed), this.u, this.v)
    this.vx.fill(0)
    this.vy.fill(0)
  }
}

/**
 * Fill `u`, `v` with points on the figure's nodal lines: each candidate is kept
 * with weight exp(-(f / W)²), a smooth falloff with no hard edge (KAMP-774).
 */
export function settledPositions(
  mode: Mode,
  rng: () => number,
  u: Float64Array,
  v: Float64Array
): void {
  for (let i = 0; i < u.length; i++) {
    let cu = 0
    let cv = 0
    for (let t = 0; t < SETTLE_TRIES; t++) {
      cu = rng()
      cv = rng()
      const q = chladni(mode, cu, cv) / SETTLED_W
      if (rng() < Math.exp(-q * q)) break
    }
    u[i] = cu
    v[i] = cv
  }
}
