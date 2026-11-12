// KAMP-768: the particle model behind the Wintermute Now Playing background
// (a subdued snowfall in album colors).
//
// Framework-free so `node --test` covers it under strip-only TypeScript: no
// parameter properties, no enums, and sibling imports take a `.ts` specifier
// (KAMP-765). The engine (nowplaying/wintermute/wintermuteEngine.ts) only draws.
//
// Units: pane heights, DOM y-down. x runs 0..aspect and y 0 (top)..1 (bottom),
// so wind and swirl cover the same distance whatever the pane's shape.
// Storage is a fixed struct-of-arrays pool, so a frame allocates nothing.
//
// Unlike Sparks, a flake has no life timer: it falls until it leaves the pane and
// is then reborn on the inflow boundary (the top, plus the upwind side while the
// wind blows). That is what keeps the pane from thinning out, including the
// upwind strip during a sideways gust.

import { clampDt, poissonGap, smoothstep } from './rareSchedule.ts'
import { clampAbs, lerp, pick, seededRng, type PaneRect } from './particleUtil.ts'

export type { PaneRect }

/** One flake of the reduced-motion still frame. */
export interface StillFlake {
  x: number // pane heights
  y: number
  layer: number
  slot: number // palette slot
  alpha: number
}

export interface SnowFieldOptions {
  /** Flakes per square pane height of visible area. 0 disables them. */
  density?: number
}

export const KIND_DEAD = 0
export const KIND_FLAKE = 1
export const KIND_CLOSE = 2

// --- tunables ------------------------------------------------------------------------
export const POOL_SIZE = 240
// Held back from ordinary flakes so a full field never blocks the close flake.
export const RESERVED_SLOTS = 2
const DENSITY = 95 // flakes per square pane height of visible area
const MIN_TARGET = 24 // the floor when the art covers nearly everything
const DT_MAX = 0.1 // s; a longer frame is clamped so nothing jumps
const FILL_S = 3 // a raised target fills in over about this long, never in one frame
const FADE_IN_S = 1.2 // a flake born inside the pane fades in
const EDGE = 0.03 // pane heights beyond an edge where flakes are born
const EXIT = 0.06 // pane heights beyond an edge before a flake counts as gone
const EXIT_TOP = 0.5 // a swirl can lift a flake; only this far above counts as gone

// Depth layers, far to near. Near flakes are larger, faster, more blurred (the
// engine bakes one sprite per layer) and few, because they cost the most overdraw.
export const LAYER_FAR = 0
export const LAYER_MID = 1
export const LAYER_NEAR = 2
export const LAYERS = 3
export const LAYER_RADIUS = [0.0032, 0.0062, 0.012] // sprite core, pane heights
const LAYER_WEIGHTS = [0.55, 0.33, 0.12]
export const NEAR_MAX = 18
const LAYER_FALL = [0.035, 0.06, 0.1] // pane heights / s, each flake ±20%
const FALL_SPREAD = 0.2
const LAYER_SWAY = [0.006, 0.011, 0.018] // sway velocity amplitude, pane heights / s
const SWAY_HZ: [number, number] = [0.06, 0.16]
const DRIFT = 0.004 // ± constant sideways drift per flake, pane heights / s
const LAYER_WIND = [0.6, 0.8, 1] // parallax: near flakes feel the gust most
const LAYER_ALPHA = [0.4, 0.55, 0.45]
const ALPHA_SPREAD: [number, number] = [0.7, 1]
// Palette slot weights: the dominant swatches carry the snowfall.
const SLOT_WEIGHTS = [0.32, 0.24, 0.18, 0.14, 0.12]
// Share of top births above the art. They fall behind it and emerge below.
const BEHIND_ART_WEIGHT = 0.5

// Track-change gust: a soft sideways breath, then calm. The level ramps linearly
// and the wind is its smoothstep, so it starts and ends with zero slope.
const GUST_PEAK = 0.25 // pane heights / s
const GUST_ATTACK_S = 0.8
const GUST_HOLD_S = 0.8
const GUST_RELEASE_S = 2
// A track change within the same album brings no new palette; gust anyway.
export const GUST_FALLBACK_S = 0.7

// Cursor swirl: flakes the cursor passes are set circling in its wake, then settle.
const SWIRL_R = 0.14 // pane heights
const SWIRL_MIN_SPEED = 0.3 // pane heights / s; below this the direction is ambiguous
const SWIRL_GAIN = 1
const SWIRL_MAX = 0.1 // pane heights / s of tangential speed
const SWIRL_CORE = 0.03 // keeps a flake right at the centre from spinning wildly
const SWIRL_TAU = 1.2 // s
const SWIRL_RELEASE = 0.002 // below this a flake is free to be captured afresh
const DRAG_GAIN = 0.4
const DRAG_MAX = 0.12
const CURSOR_V_MAX = 3 // pane heights / s; one fast flick is not a gale

// Palette generations alive at once. Each needs its own sprite sheet. Flakes live
// far longer than sparks, so a forced recolor dips the flake out and back instead
// of snapping (a staggered dip, recolored at the bottom).
export const MAX_GENERATIONS = 3
const SWAP_SPREAD_S = 1.5
const SWAP_DIP_S = 0.8
const SWAP_AT_DIP = 0.05 // recolor only while the dip is this deep

// The rare close flake: one large crystal drifting near the camera, in sharp
// focus for a moment beside the art, then falling out of view.
export const CLOSE_GAP_FLOOR_S = 60
const CLOSE_GAP_MEAN_S = 120
export const CLOSE_GAP_MAX_S = 300
const CLOSE_IDLE_S = 6 // the cursor still this long first
export const CLOSE_RADIUS = 0.05 // pane heights at its closest
const CLOSE_START_SCALE = 0.45
const CLOSE_FALL = 0.06
const CLOSE_SWAY = 0.01 // sway velocity amplitude
const CLOSE_SWAY_HZ = 0.07
const CLOSE_SPIN = 0.12 // rad / s
const CLOSE_ALPHA = 0.9
const CLOSE_FADE_IN_S = 1.5
const FOCUS_START_S = 2
const FOCUS_IN_S = 2.5
const FOCUS_HOLD_S = 1.2
const FOCUS_OUT_S = 2.5
const CLOSE_MAX_LIFE_S = 60

const STILL_COUNT = 90
const STILL_NEAR_MAX = 6
const STILL_SEED = 768
const STILL_PAD = 0.02

export class SnowField {
  // Struct-of-arrays pool. Read by the engine; written only here.
  readonly kind = new Uint8Array(POOL_SIZE)
  readonly layer = new Uint8Array(POOL_SIZE)
  readonly x = new Float64Array(POOL_SIZE)
  readonly y = new Float64Array(POOL_SIZE)
  readonly age = new Float64Array(POOL_SIZE)
  readonly fall = new Float64Array(POOL_SIZE)
  readonly drift = new Float64Array(POOL_SIZE)
  readonly swayAmp = new Float64Array(POOL_SIZE)
  readonly swayHz = new Float64Array(POOL_SIZE)
  readonly swayPhase = new Float64Array(POOL_SIZE)
  readonly baseAlpha = new Float64Array(POOL_SIZE)
  readonly slot = new Uint8Array(POOL_SIZE)
  readonly gen = new Uint32Array(POOL_SIZE)
  readonly swapAt = new Float64Array(POOL_SIZE).fill(-1)
  readonly swapGen = new Uint32Array(POOL_SIZE)
  readonly swirl = new Float64Array(POOL_SIZE)
  readonly swirlSense = new Int8Array(POOL_SIZE)
  readonly swirlCX = new Float64Array(POOL_SIZE)
  readonly swirlCY = new Float64Array(POOL_SIZE)
  readonly dragX = new Float64Array(POOL_SIZE)
  readonly dragY = new Float64Array(POOL_SIZE)
  private readonly spawnX = new Float64Array(POOL_SIZE)
  private readonly spawnY = new Float64Array(POOL_SIZE)
  private readonly respawnTick = new Float64Array(POOL_SIZE).fill(-1)

  /** Seconds the loop has run. Every schedule is on this clock, so a stop freezes it. */
  elapsed = 0
  /** The palette generation new flakes are born into. */
  generation = 0
  /** `elapsed` at which the close flake is next due. */
  nextCloseAt: number
  /** Pool index of the close flake, or -1. */
  closeIndex = -1
  /** Births on each boundary since construction (diagnostics and tests). */
  readonly inflow = { top: 0, left: 0, right: 0 }

  private readonly rng: () => number
  private readonly density: number
  private aspect = 1
  private art: PaneRect | null = null
  private flakes = 0
  private nearLive = 0
  private fillAcc = 0
  private floorGen = 0
  private freeHint = 0
  private ticks = 0

  private pointerPresent = false
  private px = 0
  private py = 0
  private prevX = 0
  private prevY = 0
  private cursorVx = 0
  private cursorVy = 0
  private lastPointerAt = 0

  private gustPending = false
  private gustPendingAt = 0
  private gustT = Infinity // time since the gust was triggered
  private gustLevel = 0 // 0..1, linear; the wind is its smoothstep
  private gustSign = 1

  constructor(rng: () => number, opts: SnowFieldOptions = {}) {
    this.rng = rng
    this.density = opts.density ?? DENSITY
    this.nextCloseAt = this.closeGap()
  }

  // --- inputs ------------------------------------------------------------------------

  /** The pane's width / height, and the art's box (null while hidden, KAMP-769). */
  setBounds(aspect: number, art: PaneRect | null): void {
    this.aspect = Math.max(0.05, aspect)
    this.art = art
  }

  /** Fill the pane to its target at once, spread top to bottom, already faded in. */
  prewarm(): void {
    const target = this.targetCount()
    while (this.flakes < target) {
      const i = this.spawnFlake(this.spawnXCoord(this.rng()), this.rng(), this.pickLayer())
      if (i < 0) break
      this.age[i] = FADE_IN_S
    }
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
   * fires late. The gust waits for the new palette (see setPalette), because the
   * palette loads asynchronously after the track id changes.
   */
  trackChanged(running: boolean): void {
    if (!running) return
    this.gustPending = true
    this.gustPendingAt = this.elapsed
  }

  /**
   * A new palette arrived; returns its generation, which the engine bakes a sprite
   * sheet for. While stopped every flake moves to it at once, so the still frame
   * lands on the new colors (KAMP-779). While running new flakes are born into it
   * (on the gust, from the upwind side), and flakes on a generation past the cap
   * dip out and back in the new colors.
   */
  setPalette(running: boolean): number {
    this.generation++
    if (!running) {
      this.floorGen = this.generation
      for (let i = 0; i < POOL_SIZE; i++) {
        if (this.kind[i] === KIND_DEAD) continue
        this.gen[i] = this.generation
        this.swapAt[i] = -1
      }
    } else {
      this.floorGen = Math.max(this.floorGen, this.generation - MAX_GENERATIONS + 1)
      for (let i = 0; i < POOL_SIZE; i++) {
        if (this.kind[i] === KIND_DEAD) continue
        const swapping = this.swapAt[i] >= 0
        if (this.gen[i] >= this.floorGen && !swapping) continue
        this.swapGen[i] = this.generation
        if (!swapping) this.swapAt[i] = this.elapsed + this.rng() * SWAP_SPREAD_S
      }
    }
    if (this.gustPending) this.releaseGust()
    return this.generation
  }

  /** Called on start(): drop everything that would otherwise replay late. */
  resume(): void {
    this.gustPending = false
    // A gust frozen mid-blow eases out from where it was rather than stopping dead.
    this.gustT = Infinity
    this.pointerPresent = false
    this.cursorVx = this.cursorVy = 0
  }

  /** Bring the close flake in now (dev hook). One at a time. */
  forceClose(): void {
    if (this.closeAlive()) return
    this.spawnClose()
  }

  // --- queries -----------------------------------------------------------------------

  /** Flakes the field aims to hold, scaled to the visible (non-art) area. */
  targetCount(): number {
    if (this.density <= 0) return 0
    let area = this.aspect
    const a = this.art
    if (a) area -= Math.max(0, a.x1 - a.x0) * this.aspect * Math.max(0, a.y1 - a.y0)
    const n = Math.round(this.density * Math.max(0, area))
    return Math.min(POOL_SIZE - RESERVED_SLOTS, Math.max(MIN_TARGET, n))
  }

  /** Horizontal wind, pane heights / s. Zero when calm. */
  windX(): number {
    return this.gustSign * GUST_PEAK * smoothstep(this.gustLevel)
  }

  /** The lowest palette generation any live flake still shows. Older sheets can go. */
  oldestGenerationInUse(): number {
    let min = this.generation
    for (let i = 0; i < POOL_SIZE; i++) {
      if (this.kind[i] !== KIND_DEAD && this.gen[i] < min) min = this.gen[i]
    }
    return min
  }

  /** True if flake i was reborn on the boundary during the latest tick. */
  respawnedThisTick(i: number): boolean {
    return this.respawnTick[i] === this.ticks
  }

  /** 1, or the recolor dip: 1 -> 0 -> 1 with zero slope at both ends. */
  dip(i: number): number {
    const at = this.swapAt[i]
    if (at < 0) return 1
    const u = (this.elapsed - at) / SWAP_DIP_S
    if (u <= 0 || u >= 1) return 1
    return (Math.cos(2 * Math.PI * u) + 1) / 2
  }

  alpha(i: number): number {
    const kind = this.kind[i]
    if (kind === KIND_DEAD) return 0
    if (kind === KIND_CLOSE) {
      return CLOSE_ALPHA * smoothstep(this.age[i] / CLOSE_FADE_IN_S) * this.dip(i)
    }
    const fadeIn = smoothstep(this.age[i] / FADE_IN_S)
    return LAYER_ALPHA[this.layer[i]] * this.baseAlpha[i] * fadeIn * this.dip(i)
  }

  /** The close flake's focus: exactly 0, eased to exactly 1, held, eased back to 0. */
  focus(i: number): number {
    if (this.kind[i] !== KIND_CLOSE) return 0
    const a = this.age[i] - FOCUS_START_S
    if (a <= 0) return 0
    if (a < FOCUS_IN_S) return smoothstep(a / FOCUS_IN_S)
    const out = a - FOCUS_IN_S - FOCUS_HOLD_S
    if (out <= 0) return 1
    return 1 - smoothstep(out / FOCUS_OUT_S)
  }

  /** The close flake's radius (pane heights): it grows as it drifts toward the camera. */
  closeRadius(i: number): number {
    const t = smoothstep(this.age[i] / (FOCUS_START_S + FOCUS_IN_S))
    return CLOSE_RADIUS * lerp(CLOSE_START_SCALE, 1, t)
  }

  /** The close flake's rotation, radians. */
  angle(i: number): number {
    return this.swayPhase[i] + CLOSE_SPIN * this.age[i]
  }

  // --- simulation --------------------------------------------------------------------

  tick(dtRaw: number): void {
    const dt = clampDt(dtRaw, DT_MAX)
    if (dt <= 0) return
    this.elapsed += dt
    this.ticks++
    this.updateCursor(dt)
    this.updateGust(dt)
    this.fill(dt)
    this.maybeClose()
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

  private releaseGust(): void {
    this.gustPending = false
    // Keep the direction of a gust still blowing; a reversal would jump the wind.
    if (this.gustLevel === 0) this.gustSign = this.rng() < 0.5 ? -1 : 1
    this.gustT = 0
  }

  private updateGust(dt: number): void {
    if (this.gustPending && this.elapsed - this.gustPendingAt >= GUST_FALLBACK_S) {
      this.releaseGust()
    }
    this.gustT += dt
    const target = this.gustT < GUST_ATTACK_S + GUST_HOLD_S ? 1 : 0
    const step = target - this.gustLevel
    this.gustLevel += Math.max(-dt / GUST_RELEASE_S, Math.min(dt / GUST_ATTACK_S, step))
    if (this.gustLevel < 0) this.gustLevel = 0
  }

  /** Grow toward a raised target gradually, with a fade-in, never in one frame. */
  private fill(dt: number): void {
    const target = this.targetCount()
    if (this.flakes >= target) {
      this.fillAcc = 0
      return
    }
    this.fillAcc += (target / FILL_S) * dt
    while (this.fillAcc >= 1 && this.flakes < target) {
      this.fillAcc -= 1
      if (this.spawnFlake(this.spawnXCoord(this.rng()), this.rng(), this.pickLayer()) < 0) break
    }
  }

  private integrate(dt: number): void {
    const wind = this.windX()
    const swirlDecay = Math.exp(-dt / SWIRL_TAU)
    const speed = Math.hypot(this.cursorVx, this.cursorVy)
    const stirring = this.pointerPresent && speed >= SWIRL_MIN_SPEED
    const target = this.targetCount()
    for (let i = 0; i < POOL_SIZE; i++) {
      const kind = this.kind[i]
      if (kind === KIND_DEAD) continue
      const age = (this.age[i] += dt)
      this.stepSwap(i)
      if (kind === KIND_CLOSE) {
        this.stepClose(i, age)
        continue
      }
      if (stirring) this.stir(i, speed, dt)
      this.swirl[i] *= swirlDecay
      this.dragX[i] *= swirlDecay
      this.dragY[i] *= swirlDecay
      if (this.swirlSense[i] !== 0 && this.swirl[i] < SWIRL_RELEASE) {
        this.swirlSense[i] = 0
        this.swirl[i] = 0
      }
      let vx =
        this.swayAmp[i] * Math.sin(2 * Math.PI * this.swayHz[i] * age + this.swayPhase[i]) +
        this.drift[i] +
        wind * LAYER_WIND[this.layer[i]] +
        this.dragX[i]
      let vy = this.fall[i] + this.dragY[i]
      if (this.swirl[i] > 0) {
        const dx = this.x[i] - this.swirlCX[i]
        const dy = this.y[i] - this.swirlCY[i]
        const d = Math.max(SWIRL_CORE, Math.hypot(dx, dy))
        const k = (this.swirl[i] * this.swirlSense[i]) / d
        vx += -dy * k
        vy += dx * k
      }
      const x = (this.x[i] += vx * dt)
      const y = (this.y[i] += vy * dt)
      if (y > 1 + EXIT || y < -EXIT_TOP || x < -EXIT || x > this.aspect + EXIT) {
        if (this.flakes > target) this.retire(i)
        else this.respawn(i, wind)
      }
    }
  }

  /** Capture a flake the cursor passes, and feed its swirl while in reach. */
  private stir(i: number, speed: number, dt: number): void {
    const dx = this.x[i] - this.px
    const dy = this.y[i] - this.py
    const d = Math.hypot(dx, dy)
    if (d >= SWIRL_R) return
    const w = smoothstep(1 - d / SWIRL_R)
    if (this.swirlSense[i] === 0) {
      // Lock the direction now, so it never flips as the cursor passes. A flake
      // beside the path circles forward with it, so the two sides counter-rotate
      // like a real wake. Dead ahead or behind is ambiguous, so pick one.
      const along = -dy * this.cursorVx + dx * this.cursorVy
      this.swirlSense[i] =
        Math.abs(along) > 1e-9 * speed ? (along > 0 ? 1 : -1) : this.rng() < 0.5 ? -1 : 1
    }
    this.swirlCX[i] = this.px
    this.swirlCY[i] = this.py
    this.swirl[i] = Math.min(SWIRL_MAX, this.swirl[i] + speed * SWIRL_GAIN * w * dt)
    this.dragX[i] = clampAbs(this.dragX[i] + this.cursorVx * DRAG_GAIN * w * dt, DRAG_MAX)
    this.dragY[i] = clampAbs(this.dragY[i] + this.cursorVy * DRAG_GAIN * w * dt, DRAG_MAX)
  }

  /** Advance a recolor dip; the color changes only near the bottom of it. */
  private stepSwap(i: number): void {
    const at = this.swapAt[i]
    if (at < 0 || this.elapsed < at) return
    if (this.gen[i] !== this.swapGen[i] && this.dip(i) <= SWAP_AT_DIP) {
      this.gen[i] = this.swapGen[i]
    }
    if (this.elapsed - at >= SWAP_DIP_S) {
      // A later skip retargeted it after the bottom: dip again rather than snap.
      this.swapAt[i] = this.gen[i] !== this.swapGen[i] ? this.elapsed : -1
    }
  }

  // --- births ------------------------------------------------------------------------

  private pickLayer(): number {
    const l = pick(LAYER_WEIGHTS, this.rng())
    return l === LAYER_NEAR && this.nearLive >= NEAR_MAX ? LAYER_MID : l
  }

  /** A new flake inside the pane, fading in. */
  private spawnFlake(x: number, y: number, layer: number): number {
    if (this.flakes >= POOL_SIZE - RESERVED_SLOTS) return -1
    const i = this.freeSlot()
    if (i < 0) return -1
    this.kind[i] = KIND_FLAKE
    this.layer[i] = layer
    this.flakes++
    if (layer === LAYER_NEAR) this.nearLive++
    this.dress(i)
    this.x[i] = x
    this.y[i] = y
    this.age[i] = 0
    return i
  }

  /** Re-randomize a flake's own motion and color, keeping its layer. */
  private dress(i: number): void {
    const r = this.rng
    const layer = this.layer[i]
    this.fall[i] = LAYER_FALL[layer] * lerp(1 - FALL_SPREAD, 1 + FALL_SPREAD, r())
    this.drift[i] = lerp(-DRIFT, DRIFT, r())
    this.swayAmp[i] = LAYER_SWAY[layer] * lerp(0.6, 1.2, r())
    this.swayHz[i] = lerp(SWAY_HZ[0], SWAY_HZ[1], r())
    this.swayPhase[i] = r() * 2 * Math.PI
    this.baseAlpha[i] = lerp(ALPHA_SPREAD[0], ALPHA_SPREAD[1], r())
    this.slot[i] = pick(SLOT_WEIGHTS, r())
    this.gen[i] = this.generation
    this.swapAt[i] = -1
    this.swirl[i] = this.dragX[i] = this.dragY[i] = 0
    this.swirlSense[i] = 0
  }

  /**
   * Rebirth on the inflow boundary: the top, or the upwind side in proportion to
   * the wind's flux through it. Already off screen, so no fade-in is needed.
   */
  private respawn(i: number, wind: number): void {
    this.dress(i)
    this.age[i] = FADE_IN_S
    this.respawnTick[i] = this.ticks
    const r = this.rng
    const wx = Math.abs(wind * LAYER_WIND[this.layer[i]])
    const side = wx / (wx + this.fall[i] * this.aspect)
    if (r() < side) {
      this.x[i] = wind > 0 ? -EDGE : this.aspect + EDGE
      this.y[i] = r()
      if (wind > 0) this.inflow.left++
      else this.inflow.right++
    } else {
      this.x[i] = this.spawnXCoord(r())
      this.y[i] = -EDGE
      this.inflow.top++
    }
  }

  /** x in pane heights for a uniform sample `u`, weighted toward the side strips. */
  private spawnXCoord(u: number): number {
    const w = this.aspect
    const a = this.art
    if (!a) return u * w
    const l = Math.max(0, a.x0) * w
    const rr = Math.min(1, a.x1) * w
    const behind = Math.max(0, rr - l) * BEHIND_ART_WEIGHT
    const right = Math.max(0, w - rr)
    const total = l + behind + right
    if (total <= 0) return u * w
    let v = u * total
    if (v < l) return v
    v -= l
    if (v < right) return rr + v
    v -= right
    return l + v / BEHIND_ART_WEIGHT
  }

  /** Place a flake at a given spot (tests, and nothing else). */
  place(x: number, y: number, layer: number): number {
    return this.spawnFlake(x, y, layer)
  }

  // --- the close flake ---------------------------------------------------------------

  private closeAlive(): boolean {
    return this.closeIndex >= 0 && this.kind[this.closeIndex] === KIND_CLOSE
  }

  private closeGap(): number {
    return poissonGap(this.rng(), CLOSE_GAP_FLOOR_S, CLOSE_GAP_MEAN_S, CLOSE_GAP_MAX_S)
  }

  private maybeClose(): void {
    if (this.closeAlive()) return
    if (this.elapsed < this.nextCloseAt) return
    if (this.elapsed - this.lastPointerAt < CLOSE_IDLE_S) return
    if (!this.spawnClose()) this.nextCloseAt = this.elapsed + this.closeGap()
  }

  /** Horizontal sway offset of the close flake at `age`, from its spawn point. */
  private closeSway(phase: number, age: number): number {
    const w = 2 * Math.PI * CLOSE_SWAY_HZ
    return (CLOSE_SWAY / w) * (Math.cos(phase) - Math.cos(w * age + phase))
  }

  /**
   * Choose where the close flake is in focus (a side strip, or above or below the
   * art, never over it), then run its path back to where it must start. False if
   * nothing fits, e.g. art covering the whole pane.
   */
  private spawnClose(): boolean {
    const r = this.rng
    const swayReach = (2 * CLOSE_SWAY) / (2 * Math.PI * CLOSE_SWAY_HZ)
    const mx = CLOSE_RADIUS + swayReach + 0.005
    const my = CLOSE_RADIUS + (CLOSE_FALL * FOCUS_HOLD_S) / 2 + 0.005
    const w = this.aspect
    const a = this.art
    // Candidate regions for the peak point, in pane heights: [x0, x1, y0, y1].
    const regions: number[][] = []
    if (!a) {
      regions.push([mx, w - mx, my, 1 - my])
    } else {
      regions.push([mx, a.x0 * w - mx, my, 1 - my])
      regions.push([a.x1 * w + mx, w - mx, my, 1 - my])
      regions.push([mx, w - mx, a.y1 + my, 1 - my])
      regions.push([mx, w - mx, my, a.y0 - my])
    }
    let total = 0
    const areas = regions.map(([x0, x1, y0, y1]) => {
      const area = x1 > x0 && y1 > y0 ? (x1 - x0) * (y1 - y0) : 0
      total += area
      return area
    })
    if (total <= 0) return false
    let v = r() * total
    let k = 0
    while (k < regions.length - 1 && v >= areas[k]) v -= areas[k++]
    const [x0, x1, y0, y1] = regions[k]
    const peakX = lerp(x0, x1, r())
    const peakY = lerp(y0, y1, r())

    const i = this.freeSlot()
    if (i < 0) return false
    const phase = r() * 2 * Math.PI
    const tPeak = FOCUS_START_S + FOCUS_IN_S + FOCUS_HOLD_S / 2
    this.kind[i] = KIND_CLOSE
    this.layer[i] = LAYER_NEAR
    this.age[i] = 0
    this.swayPhase[i] = phase
    this.slot[i] = 0
    this.gen[i] = this.generation
    this.swapAt[i] = -1
    this.spawnX[i] = peakX - this.closeSway(phase, tPeak)
    this.spawnY[i] = peakY - CLOSE_FALL * tPeak
    this.x[i] = this.spawnX[i]
    this.y[i] = this.spawnY[i]
    this.closeIndex = i
    const life = (1 + CLOSE_RADIUS - this.spawnY[i]) / CLOSE_FALL
    this.nextCloseAt = this.elapsed + life + this.closeGap()
    return true
  }

  /** The close flake follows an exact path, so where it peaks is where it was aimed. */
  private stepClose(i: number, age: number): void {
    this.x[i] = this.spawnX[i] + this.closeSway(this.swayPhase[i], age)
    this.y[i] = this.spawnY[i] + CLOSE_FALL * age
    if (this.y[i] - this.closeRadius(i) > 1 || age > CLOSE_MAX_LIFE_S) {
      this.kind[i] = KIND_DEAD
      this.closeIndex = -1
    }
  }

  // --- pool --------------------------------------------------------------------------

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
    if (this.layer[i] === LAYER_NEAR) this.nearLive--
    this.kind[i] = KIND_DEAD
    this.flakes--
  }
}

/**
 * The reduced-motion still frame: a deterministic composition of flakes in all
 * three layers, laid out around the art and the title (never behind either), or
 * across the whole pane when the art is hidden.
 */
export function stillFrame(
  aspect: number,
  art: PaneRect | null,
  title: PaneRect | null
): StillFlake[] {
  const r = seededRng(STILL_SEED)
  const out: StillFlake[] = []
  const inside = (rect: PaneRect | null, nx: number, ny: number): boolean =>
    rect != null &&
    nx > rect.x0 - STILL_PAD &&
    nx < rect.x1 + STILL_PAD &&
    ny > rect.y0 - STILL_PAD &&
    ny < rect.y1 + STILL_PAD
  let near = 0
  for (let attempt = 0; attempt < STILL_COUNT * 10 && out.length < STILL_COUNT; attempt++) {
    const nx = r()
    const ny = r()
    let layer = pick(LAYER_WEIGHTS, r())
    const slotU = r()
    const alphaU = r()
    if (inside(art, nx, ny) || inside(title, nx, ny)) continue
    if (layer === LAYER_NEAR && near >= STILL_NEAR_MAX) layer = LAYER_MID
    if (layer === LAYER_NEAR) near++
    out.push({
      x: nx * aspect,
      y: ny,
      layer,
      slot: pick(SLOT_WEIGHTS, slotU),
      alpha: LAYER_ALPHA[layer] * lerp(ALPHA_SPREAD[0], ALPHA_SPREAD[1], alphaU)
    })
  }
  return out
}
