// KAMP-773: the staggered recolor dip for particles that never die (Wintermute's
// flakes, Castalia's grains). Extracted from wintermuteModel (KAMP-768) so the two
// backgrounds share one copy of the rule rather than two that drift apart.
//
// A particle shows the colors of a palette generation. A long-lived particle can't
// wait for turnover to pick up a new palette, and snapping its color is visible, so
// it dips out (1 -> 0 -> 1, zero slope at both ends) and changes generation only
// near the bottom of the dip. Dips are staggered so the field recolors as a wave.
// While the loop is stopped nothing ticks, so every particle moves to the new
// generation at once and a still frame lands on the new colors (KAMP-779).
//
// Framework-free so `node --test` covers it: no enums, no parameter properties.

export interface RecolorDipOptions {
  /** Palette generations alive at once; older ones are forced to dip. */
  maxGenerations: number
  /** Dips start at a random point within this many seconds. */
  spreadS: number
  /** Length of one dip, seconds. */
  dipS: number
  /** The generation changes only while the dip is at or below this. */
  swapAtDip: number
}

export class RecolorDips {
  /** Each particle's palette generation. Read by the engine to pick a sprite sheet. */
  readonly gen: Uint32Array
  /** The generation newborn particles take. */
  generation = 0

  private readonly swapAt: Float64Array
  private readonly swapGen: Uint32Array
  private readonly rng: () => number
  private readonly opts: RecolorDipOptions
  private floorGen = 0

  constructor(size: number, rng: () => number, opts: RecolorDipOptions) {
    this.gen = new Uint32Array(size)
    this.swapAt = new Float64Array(size).fill(-1)
    this.swapGen = new Uint32Array(size)
    this.rng = rng
    this.opts = opts
  }

  /** A particle (re)born now: current generation, no dip in progress. */
  assign(i: number): void {
    this.gen[i] = this.generation
    this.swapAt[i] = -1
  }

  /**
   * A new palette arrived; returns its generation. Stopped: every live particle
   * moves to it now. Running: particles on a generation past the cap get a dip
   * scheduled; ones already dipping are retargeted to the newest generation.
   */
  setPalette(running: boolean, now: number, isLive: (i: number) => boolean): number {
    this.generation++
    const n = this.gen.length
    if (!running) {
      this.floorGen = this.generation
      for (let i = 0; i < n; i++) {
        if (!isLive(i)) continue
        this.gen[i] = this.generation
        this.swapAt[i] = -1
      }
      return this.generation
    }
    this.floorGen = Math.max(this.floorGen, this.generation - this.opts.maxGenerations + 1)
    for (let i = 0; i < n; i++) {
      if (!isLive(i)) continue
      const dipping = this.swapAt[i] >= 0
      if (this.gen[i] >= this.floorGen && !dipping) continue
      this.swapGen[i] = this.generation
      if (!dipping) this.swapAt[i] = now + this.rng() * this.opts.spreadS
    }
    return this.generation
  }

  /** 1, or the dip: 1 -> 0 -> 1 with zero slope at both ends. */
  dip(i: number, now: number): number {
    const at = this.swapAt[i]
    if (at < 0) return 1
    const u = (now - at) / this.opts.dipS
    if (u <= 0 || u >= 1) return 1
    return (Math.cos(2 * Math.PI * u) + 1) / 2
  }

  /** Advance particle i's dip; its color changes only near the bottom. */
  step(i: number, now: number): void {
    const at = this.swapAt[i]
    if (at < 0 || now < at) return
    if (this.gen[i] !== this.swapGen[i] && this.dip(i, now) <= this.opts.swapAtDip) {
      this.gen[i] = this.swapGen[i]
    }
    if (now - at >= this.opts.dipS) {
      // A later palette retargeted it after the bottom: dip again rather than snap.
      this.swapAt[i] = this.gen[i] !== this.swapGen[i] ? now : -1
    }
  }

  /** The lowest generation any live particle still shows. Older sheets can go. */
  oldestInUse(isLive: (i: number) => boolean): number {
    let min = this.generation
    for (let i = 0; i < this.gen.length; i++) {
      if (isLive(i) && this.gen[i] < min) min = this.gen[i]
    }
    return min
  }
}
