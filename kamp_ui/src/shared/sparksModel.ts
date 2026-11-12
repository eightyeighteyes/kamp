// KAMP-767: the particle model behind the Sparks Now Playing background
// (campfire sparks rising in album colors).
//
// Framework-free so `node --test` covers it under strip-only TypeScript: no
// parameter properties, no enums, and sibling imports take a `.ts` specifier
// (KAMP-765). Every rule that could go wrong without being visible frame to frame
// lives here (an emptying pane, a burst in the old colors, a gust out of nowhere,
// a late event after a resume), so the canvas engine only draws what it is handed.
//
// Units: pane heights, DOM y-down. x runs 0..aspect and y 0 (top)..1 (bottom).
// Normalised x,y would make motion anisotropic: a gust or a curl would cover
// more pixels sideways in a wide pane than in a tall one.
//
// Storage is a fixed struct-of-arrays pool, so a frame allocates nothing.

import { clampDt, poissonGap, smoothstep } from './rareSchedule.ts'
import { clampAbs, lerp, pick, seededRng, type PaneRect } from './particleUtil.ts'

export type { PaneRect }

/** One spark of the reduced-motion still frame. */
export interface StillSpark {
  x: number // pane heights
  y: number
  size: number // size bucket
  slot: number // palette slot
  alpha: number
}

export interface SparkFieldOptions {
  /** Ambient sparks per square pane height of visible area. 0 disables them. */
  density?: number
}

export const KIND_DEAD = 0
export const KIND_AMBIENT = 1
export const KIND_BURST = 2
export const KIND_POP = 3

// --- tunables ------------------------------------------------------------------------
export const POOL_SIZE = 260
// Held back from ambient spawning so a full field can't swallow a burst or the pop.
export const RESERVED_SLOTS = 24
const DENSITY = 80 // ambient sparks per square pane height of visible area
const MIN_TARGET = 16 // the on-screen floor when the art covers nearly everything
const DT_MAX = 0.1 // s; a longer frame is clamped so nothing jumps

// Sprite radius per size bucket, in pane heights (the engine bakes one sprite per
// bucket). Small sparks are the point, hence the baked blur instead of Bokeh's 28px.
export const SIZE_RADIUS = [0.0035, 0.006, 0.009, 0.013]
export const SIZE_BUCKETS = SIZE_RADIUS.length
const AMBIENT_SIZES = [0.45, 0.3, 0.17, 0.08]
const BURST_SIZES = [0.3, 0.35, 0.25, 0.1]
// Palette slot weights: the dominant swatches carry the fire.
const SLOT_WEIGHTS = [0.35, 0.25, 0.18, 0.12, 0.1]

// Ambient motion. Typical travel is about 0.4 pane heights before a spark winks out.
const LIFE: [number, number] = [3.5, 6.5] // s
const RISE: [number, number] = [0.06, 0.12] // pane heights / s
const RISE_SLOWDOWN = 0.3 // fraction of the rise lost by the end of a spark's life
const SPAWN_Y: [number, number] = [0.7, 1.03]
const CURL_HZ: [number, number] = [0.08, 0.2] // lateral meander, slow
const CURL_AMP: [number, number] = [0.01, 0.035] // pane heights / s
const DRIFT = 0.01 // ± constant sideways drift, pane heights / s
const RETIRE_MARGIN = 0.05 // pane heights beyond the edge before a spark is retired
// Steady-state spawning: the base rate is target / mean life, nudged by how far the
// count is off. Never "spawn while below", which arrives in clumps and reads as flicker.
const RATE_K = 0.5
const RATE_MAX = 2 // multiple of the base rate
// Weight of the area behind the art when choosing where a spark is born. Sparks
// there are hidden, so only a few rise behind it to emerge above.
const BEHIND_ART_WEIGHT = 0.08

// Alpha. A slow fade in, a gentle shimmer and a long fade out. Never a per-frame
// random alpha: that strobes (KAMP-774).
const FADE_IN_S = 0.8
const FADE_OUT_FRAC = 0.45 // of life
export const SHIMMER_HZ: [number, number] = [0.25, 0.8]
const SHIMMER_DEPTH = 0.3
const BASE_ALPHA: [number, number] = [0.55, 0.95]

// Gust: the cursor is a breath of air across the fire.
const GUST_R = 0.18 // pane heights
const GUST_GAIN = 3 // fraction of cursor velocity absorbed per second at the centre
const GUST_VERTICAL = 0.35 // a vertical sweep pushes less than a sideways one
const GUST_MAX = 0.5 // pane heights / s
const GUST_TAU = 0.7 // s; buoyancy then curls the spark back upward
const CURSOR_V_MAX = 3 // pane heights / s; one fast flick is not a gale

// Track-change burst: a log settles.
export const BURST_COUNT = 36
const BURST_SPREAD_S = 0.6
// A track change within the same album brings no new palette; burst anyway.
export const BURST_FALLBACK_S = 0.7
const BURST_LIFE: [number, number] = [2, 3.5]
const BURST_RISE: [number, number] = [0.18, 0.32]

// The rare pop: one spark much higher than the rest, lingering before it winks out.
const POP_GAP_FLOOR_S = 45
const POP_GAP_MEAN_S = 90
const POP_GAP_MAX_S = 240
const POP_IDLE_S = 6 // cursor still this long first, as Bokeh's firefly
const POP_TRAVEL = 0.75 // pane heights covered by the launch
const POP_TAU = 1 // s; how quickly the launch spends itself
const POP_DRIFT = 0.012 // pane heights / s of lingering rise
const POP_LIFE: [number, number] = [9, 12]
const POP_FADE_OUT_FRAC = 0.35

// Palette generations alive at once. Each needs its own sprite sheet.
export const MAX_GENERATIONS = 3

const STILL_COUNT = 48
const STILL_SEED = 767

export class SparkField {
  // Struct-of-arrays pool. Read by the engine; written only here.
  readonly kind = new Uint8Array(POOL_SIZE)
  readonly x = new Float64Array(POOL_SIZE)
  readonly y = new Float64Array(POOL_SIZE)
  readonly spawnX = new Float64Array(POOL_SIZE)
  readonly spawnY = new Float64Array(POOL_SIZE)
  readonly age = new Float64Array(POOL_SIZE)
  readonly life = new Float64Array(POOL_SIZE)
  readonly rise = new Float64Array(POOL_SIZE)
  readonly drift = new Float64Array(POOL_SIZE)
  readonly curlHz = new Float64Array(POOL_SIZE)
  readonly curlAmp = new Float64Array(POOL_SIZE)
  readonly curlPhase = new Float64Array(POOL_SIZE)
  readonly shimmerHz = new Float64Array(POOL_SIZE)
  readonly shimmerPhase = new Float64Array(POOL_SIZE)
  readonly baseAlpha = new Float64Array(POOL_SIZE)
  readonly gustX = new Float64Array(POOL_SIZE)
  readonly gustY = new Float64Array(POOL_SIZE)
  readonly size = new Uint8Array(POOL_SIZE)
  readonly slot = new Uint8Array(POOL_SIZE)
  readonly gen = new Uint32Array(POOL_SIZE)

  /** Seconds the loop has run. Every schedule is on this clock, so a stop freezes it. */
  elapsed = 0
  /** The palette generation new sparks are born into. */
  generation = 0
  /** `elapsed` at which the rare pop is next due. */
  nextPopAt: number

  private readonly rng: () => number
  private readonly density: number
  private aspect = 1
  private art: PaneRect | null = null
  private live = 0
  private freeHint = 0
  private spawnAcc = 0
  private floorGen = 0

  private pointerPresent = false
  private px = 0
  private py = 0
  private prevX = 0
  private prevY = 0
  private cursorVx = 0
  private cursorVy = 0
  private lastPointerAt = 0

  private burstPending = false
  private burstPendingAt = 0
  private burstQueue = 0
  private burstAcc = 0
  private burstGen = 0
  private popIndex = -1

  constructor(rng: () => number, opts: SparkFieldOptions = {}) {
    this.rng = rng
    this.density = opts.density ?? DENSITY
    this.nextPopAt = this.popGap()
  }

  // --- inputs ------------------------------------------------------------------------

  /** The pane's width / height, and the art's box (null while hidden, KAMP-769). */
  setBounds(aspect: number, art: PaneRect | null): void {
    this.aspect = Math.max(0.05, aspect)
    this.art = art
  }

  /** The cursor, in pane heights. The first move after an entry is not a velocity. */
  pointerMove(x: number, y: number): void {
    if (!this.pointerPresent) {
      // Entry: start a new stroke here, so an exit on one side and a re-entry on
      // the other never read as one enormous sweep (KAMP-772).
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

  /**
   * The track changed (id to a different id). Ignored while stopped, so nothing
   * fires late. The burst waits for the new palette (see setPalette), because the
   * palette loads asynchronously after the track id changes.
   */
  trackChanged(running: boolean): void {
    if (!running) return
    this.burstPending = true
    this.burstPendingAt = this.elapsed
  }

  /**
   * A new palette arrived; returns its generation, which the engine bakes a sprite
   * sheet for. While stopped every spark moves to it at once, so the still frame
   * lands on the new colors (KAMP-779). While running new sparks take it, and the
   * crossfade happens as the old ones wink out.
   */
  setPalette(running: boolean): number {
    this.generation++
    this.floorGen = running
      ? Math.max(this.floorGen, this.generation - MAX_GENERATIONS + 1)
      : this.generation
    for (let i = 0; i < POOL_SIZE; i++) {
      if (this.kind[i] !== KIND_DEAD && this.gen[i] < this.floorGen) this.gen[i] = this.generation
    }
    if (this.burstQueue > 0 && this.burstGen < this.floorGen) this.burstGen = this.generation
    if (this.burstPending) this.releaseBurst()
    return this.generation
  }

  /** Called on start(): drop everything that would otherwise replay late. */
  resume(): void {
    this.burstPending = false
    this.burstQueue = 0
    this.burstAcc = 0
    this.pointerPresent = false
    this.cursorVx = this.cursorVy = 0
  }

  /** Launch the rare pop now (dev hook). One at a time. */
  forcePop(): void {
    if (this.popAlive()) return
    this.spawnPop()
  }

  // --- queries -----------------------------------------------------------------------

  /** The lowest palette generation any spark may still use. Older sheets can go. */
  oldestGeneration(): number {
    return this.floorGen
  }

  /** Ambient sparks the field aims to hold, scaled to the visible (non-art) area. */
  targetCount(): number {
    if (this.density <= 0) return 0
    let area = this.aspect
    const a = this.art
    if (a) area -= Math.max(0, a.x1 - a.x0) * this.aspect * Math.max(0, a.y1 - a.y0)
    const n = Math.round(this.density * Math.max(0, area))
    return Math.min(POOL_SIZE - RESERVED_SLOTS, Math.max(MIN_TARGET, n))
  }

  meanLife(): number {
    return (LIFE[0] + LIFE[1]) / 2
  }

  /** The furthest an ambient spark can rise in its life (pane heights). */
  maxAmbientRise(): number {
    return RISE[1] * LIFE[1] * (1 - RISE_SLOWDOWN / 2)
  }

  /** 0..1, snapped to exactly 1 once the fade is done (an eased value must land). */
  fadeIn(i: number): number {
    return smoothstep(this.age[i] / FADE_IN_S)
  }

  alpha(i: number): number {
    if (this.kind[i] === KIND_DEAD) return 0
    const age = this.age[i]
    const t = age / this.life[i]
    const outFrac = this.kind[i] === KIND_POP ? POP_FADE_OUT_FRAC : FADE_OUT_FRAC
    const fadeOut = smoothstep((1 - t) / outFrac)
    const s = Math.sin(2 * Math.PI * this.shimmerHz[i] * age + this.shimmerPhase[i])
    const shimmer = 1 - SHIMMER_DEPTH * 0.5 * (1 - s)
    return this.baseAlpha[i] * this.fadeIn(i) * fadeOut * shimmer
  }

  // --- simulation --------------------------------------------------------------------

  tick(dtRaw: number): void {
    const dt = clampDt(dtRaw, DT_MAX)
    if (dt <= 0) return
    this.elapsed += dt
    this.updateCursor(dt)
    this.spawnAmbient(dt)
    this.spawnBurst(dt)
    this.maybePop()
    this.integrate(dt)
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

  private integrate(dt: number): void {
    const decay = Math.exp(-dt / GUST_TAU)
    const gusting = this.cursorVx !== 0 || this.cursorVy !== 0
    const xMax = this.aspect + RETIRE_MARGIN
    for (let i = 0; i < POOL_SIZE; i++) {
      const kind = this.kind[i]
      if (kind === KIND_DEAD) continue
      const age = (this.age[i] += dt)
      if (age >= this.life[i]) {
        this.retire(i)
        continue
      }
      if (gusting) {
        const d = Math.hypot(this.x[i] - this.px, this.y[i] - this.py)
        if (d < GUST_R) {
          const w = smoothstep(1 - d / GUST_R) * GUST_GAIN * dt
          this.gustX[i] = clampAbs(this.gustX[i] + this.cursorVx * w, GUST_MAX)
          this.gustY[i] = clampAbs(this.gustY[i] + this.cursorVy * GUST_VERTICAL * w, GUST_MAX)
        }
      }
      this.gustX[i] *= decay
      this.gustY[i] *= decay
      const curl =
        this.curlAmp[i] * Math.sin(2 * Math.PI * this.curlHz[i] * age + this.curlPhase[i])
      const vy =
        kind === KIND_POP
          ? -(POP_TRAVEL / POP_TAU) * Math.exp(-age / POP_TAU) - POP_DRIFT
          : -this.rise[i] * (1 - (RISE_SLOWDOWN * age) / this.life[i])
      const x = (this.x[i] += (curl + this.drift[i] + this.gustX[i]) * dt)
      const y = (this.y[i] += (vy + this.gustY[i]) * dt)
      if (y < -RETIRE_MARGIN || x < -RETIRE_MARGIN || x > xMax) this.retire(i)
    }
  }

  private spawnAmbient(dt: number): void {
    const target = this.targetCount()
    if (target <= 0) return
    let ambient = 0
    for (let i = 0; i < POOL_SIZE; i++) if (this.kind[i] === KIND_AMBIENT) ambient++
    const base = target / this.meanLife()
    const rate = Math.min(
      RATE_MAX * base,
      Math.max(0, base * (1 + (RATE_K * (target - ambient)) / target))
    )
    this.spawnAcc += rate * dt
    while (this.spawnAcc >= 1) {
      this.spawnAcc -= 1
      // Leave the reserve free for the burst and the pop.
      if (POOL_SIZE - this.live <= RESERVED_SLOTS) {
        this.spawnAcc = 0
        break
      }
      const r = this.rng
      this.spawn(
        KIND_AMBIENT,
        this.spawnXCoord(r()),
        lerp(SPAWN_Y[0], SPAWN_Y[1], Math.sqrt(r())), // denser near the fire
        AMBIENT_SIZES,
        this.generation
      )
    }
  }

  private releaseBurst(): void {
    this.burstPending = false
    this.burstQueue = BURST_COUNT
    this.burstAcc = 0
    this.burstGen = this.generation
  }

  private spawnBurst(dt: number): void {
    if (this.burstPending && this.elapsed - this.burstPendingAt >= BURST_FALLBACK_S) {
      this.releaseBurst()
    }
    if (this.burstQueue <= 0) return
    this.burstAcc += (BURST_COUNT / BURST_SPREAD_S) * dt
    while (this.burstAcc >= 1 && this.burstQueue > 0) {
      this.burstAcc -= 1
      this.burstQueue--
      const r = this.rng
      const i = this.spawn(
        KIND_BURST,
        this.spawnXCoord(r()),
        lerp(0.97, 1.04, r()),
        BURST_SIZES,
        this.burstGen
      )
      if (i < 0) {
        this.burstQueue = 0
        break
      }
      this.life[i] = lerp(BURST_LIFE[0], BURST_LIFE[1], r())
      this.rise[i] = lerp(BURST_RISE[0], BURST_RISE[1], r())
    }
  }

  private popAlive(): boolean {
    return this.popIndex >= 0 && this.kind[this.popIndex] === KIND_POP
  }

  private popGap(): number {
    return poissonGap(this.rng(), POP_GAP_FLOOR_S, POP_GAP_MEAN_S, POP_GAP_MAX_S)
  }

  private maybePop(): void {
    if (this.popAlive()) return
    if (this.elapsed < this.nextPopAt) return
    if (this.elapsed - this.lastPointerAt < POP_IDLE_S) return
    this.spawnPop()
  }

  private spawnPop(): void {
    const r = this.rng
    const i = this.spawn(KIND_POP, this.spawnXCoord(r()), 0.97, [0, 0, 0, 1], this.generation)
    if (i < 0) return
    this.popIndex = i
    this.life[i] = lerp(POP_LIFE[0], POP_LIFE[1], r())
    this.curlAmp[i] = lerp(0.02, 0.04, r()) // a wider, lazier wander while it lingers
    this.curlHz[i] = lerp(0.05, 0.1, r())
    this.baseAlpha[i] = 1
    this.slot[i] = 0
    this.nextPopAt = this.elapsed + this.life[i] + this.popGap()
  }

  /** Spawn an ambient spark at a given spot (tests, and nothing else). */
  place(x: number, y: number): number {
    return this.spawn(KIND_AMBIENT, x, y, AMBIENT_SIZES, this.generation)
  }

  /** x in pane heights for a uniform sample `u`, weighted toward the side strips. */
  private spawnXCoord(u: number): number {
    const w = this.aspect
    const a = this.art
    if (!a) return u * w
    const l = Math.max(0, a.x0) * w
    const rr = Math.min(1, a.x1) * w
    const left = l
    const behind = Math.max(0, rr - l) * BEHIND_ART_WEIGHT
    const right = Math.max(0, w - rr)
    const total = left + behind + right
    if (total <= 0) return u * w
    let v = u * total
    if (v < left) return v
    v -= left
    if (v < right) return rr + v
    v -= right
    return l + v / BEHIND_ART_WEIGHT
  }

  private spawn(kind: number, x: number, y: number, sizes: number[], gen: number): number {
    const i = this.freeSlot()
    if (i < 0) return -1
    const r = this.rng
    this.kind[i] = kind
    this.x[i] = this.spawnX[i] = x
    this.y[i] = this.spawnY[i] = y
    this.age[i] = 0
    this.life[i] = lerp(LIFE[0], LIFE[1], r())
    this.rise[i] = lerp(RISE[0], RISE[1], r())
    this.drift[i] = lerp(-DRIFT, DRIFT, r())
    this.curlHz[i] = lerp(CURL_HZ[0], CURL_HZ[1], r())
    this.curlAmp[i] = lerp(CURL_AMP[0], CURL_AMP[1], r())
    this.curlPhase[i] = r() * 2 * Math.PI
    this.shimmerHz[i] = lerp(SHIMMER_HZ[0], SHIMMER_HZ[1], r())
    this.shimmerPhase[i] = r() * 2 * Math.PI
    this.baseAlpha[i] = lerp(BASE_ALPHA[0], BASE_ALPHA[1], r())
    this.gustX[i] = this.gustY[i] = 0
    this.size[i] = pick(sizes, r())
    this.slot[i] = pick(SLOT_WEIGHTS, r())
    this.gen[i] = gen
    this.live++
    return i
  }

  private freeSlot(): number {
    for (let k = 0; k < POOL_SIZE; k++) {
      const i = (this.freeHint + k) % POOL_SIZE
      if (this.kind[i] === KIND_DEAD) {
        this.freeHint = (i + 1) % POOL_SIZE
        return i
      }
    }
    return -1
  }

  private retire(i: number): void {
    this.kind[i] = KIND_DEAD
    this.live--
  }
}

/**
 * The reduced-motion still frame: a deterministic composition of sparks, denser
 * and larger near the fire, fading as they rise, laid out around the art (never
 * behind it) or across the whole pane when the art is hidden.
 */
export function stillFrame(aspect: number, art: PaneRect | null): StillSpark[] {
  const r = seededRng(STILL_SEED)
  const out: StillSpark[] = []
  const pad = 0.015 // keep halos clear of the art's edge
  for (let attempt = 0; attempt < STILL_COUNT * 10 && out.length < STILL_COUNT; attempt++) {
    const nx = r()
    const ny = 1 - 0.85 * Math.pow(r(), 1.4) // most near the bottom
    const sizeU = r()
    const slotU = r()
    const alphaU = r()
    if (art && nx > art.x0 - pad && nx < art.x1 + pad && ny > art.y0 - pad && ny < art.y1 + pad) {
      continue
    }
    const low = ny // 1 at the bottom
    out.push({
      x: nx * aspect,
      y: ny,
      size: Math.min(SIZE_BUCKETS - 1, Math.floor(sizeU * SIZE_BUCKETS * (0.4 + 0.6 * low))),
      slot: pick(SLOT_WEIGHTS, slotU),
      alpha: Math.min(1, (0.25 + 0.6 * low) * lerp(0.75, 1, alphaU))
    })
  }
  return out
}
