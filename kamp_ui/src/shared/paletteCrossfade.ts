// KAMP-780: the CPU-side palette crossfade for the WebGL Now Playing backgrounds.
// Framework-free (type-only import) so `node --test` can exercise it.
//
// Pulled out of the engine because it carries the one behavioural rule the
// KAMP-776 spike found broken in Bokeh (KAMP-779): when no render loop is
// running, nothing advances the fade, so a palette change must land directly on
// the new colors. Otherwise a reduced-motion still frame shows the previous
// track's colors indefinitely.

import type { Rgb } from './paletteUniforms'

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t
const smoothstep = (t: number): number => t * t * (3 - 2 * t)

export class PaletteCrossfade {
  private from: Rgb[]
  private to: Rgb[]
  private t = 1
  // A plain field, not a parameter property: node's strip-only TS mode rejects those.
  private readonly durationMs: number

  constructor(initial: Rgb[], durationMs = 1000) {
    this.durationMs = durationMs
    this.from = initial
    this.to = initial
  }

  /** True once the fade has reached its target. */
  get settled(): boolean {
    return this.t >= 1
  }

  /**
   * Retarget to `next`. With `animate` false (the loop is stopped) the new colors
   * apply immediately; otherwise the fade starts from whatever is on screen now,
   * so a mid-fade retarget (a fast skip) never jumps.
   */
  set(next: Rgb[], animate: boolean): void {
    this.from = this.current()
    this.to = next
    this.t = animate && this.durationMs > 0 ? 0 : 1
  }

  tick(dtMs: number): void {
    if (this.t >= 1) return
    this.t = Math.min(1, this.t + dtMs / this.durationMs)
  }

  current(): Rgb[] {
    if (this.t >= 1) return this.to
    const k = smoothstep(this.t)
    // A slot the previous palette lacked fades from its own target (i.e. holds).
    return this.to.map((c, i) => {
      const f = this.from[i] ?? c
      return { r: lerp(f.r, c.r, k), g: lerp(f.g, c.g, k), b: lerp(f.b, c.b, k) }
    })
  }
}
