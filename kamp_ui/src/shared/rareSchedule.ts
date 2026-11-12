// KAMP-765: timing helpers shared by the shader backgrounds' delight moments
// (Halation's hair/jolt, Borealis's surge and edge flicker). Framework-free so
// `node --test` covers it. Node's strip-only TS mode: no enums or parameter
// properties in this file.

/**
 * Seconds until the next rare moment: a floor plus an exponential tail, capped.
 * Exponential (Poisson) spacing is what makes a moment feel unscheduled; the
 * floor keeps two from landing back to back and the cap bounds the dry spell.
 * `u` is a uniform 0..1 sample from the caller's RNG.
 */
export function poissonGap(u: number, floor: number, mean: number, max: number): number {
  return Math.min(max, floor - mean * Math.log(Math.max(1e-6, u)))
}

/**
 * Clamp a frame's dt before it advances an envelope. A frame cap of 1 fps
 * (__kampGL.tune) or a stalled renderer would otherwise hand an envelope a whole
 * second at once and break its bounded-slope guarantee.
 */
export function clampDt(dt: number, max: number): number {
  return Math.min(Math.max(0, dt), max)
}

export const smoothstep = (t: number): number => {
  const x = Math.max(0, Math.min(1, t))
  return x * x * (3 - 2 * x)
}
