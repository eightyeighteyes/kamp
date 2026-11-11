// KAMP-776 spike: turns the ~20Hz `audio.level` dB feed into a smooth 0..1 value a
// shader uniform can follow. Framework-free so `node --test` can exercise it.
//
// Three things the raw feed gets wrong for visuals:
//   * it runs AHEAD of the speakers (mpv meters ~audio-buffer before output, plus
//     device latency), so samples are held back by `delayMs` before they count;
//   * it is stepped at ~20Hz, so the value eases toward its target with a
//     framerate-independent exponential (`tauMs`) instead of jumping;
//   * it simply stops on pause/stop, so a sample older than `staleMs` (measured at
//     the delayed playhead) reads as silence and the value decays to 0 rather than
//     freezing on the last loud frame.
// Samples are stamped at ARRIVAL by the caller (performance.now()), not at draw
// time, so the render loop's cadence never re-samples the feed.

export interface LevelFollowerOptions {
  delayMs?: number
  tauMs?: number
  staleMs?: number
  floorDb?: number
}

interface Sample {
  t: number
  level: number
}

// Cap the pending queue so a stalled render loop (hidden window) can't grow it
// without bound; at 20Hz this is ~3s, far beyond any sane delay.
const MAX_PENDING = 64

/** dB -> 0..1, linear in dB between `floorDb` (0) and 0 dBFS (1). Non-finite/null = silence. */
export function dbToLevel(db: number | null, floorDb: number): number {
  if (db == null || Number.isNaN(db)) return 0
  if (db === Infinity) return 1
  if (!Number.isFinite(db)) return 0
  return Math.min(1, Math.max(0, (db - floorDb) / -floorDb))
}

export class LevelFollower {
  private readonly delayMs: number
  private readonly tauMs: number
  private readonly staleMs: number
  private readonly floorDb: number
  private pending: Sample[] = []
  private current: Sample | null = null
  private value = 0
  private lastNow: number | null = null

  constructor(opts: LevelFollowerOptions = {}) {
    this.delayMs = opts.delayMs ?? 300
    this.tauMs = opts.tauMs ?? 150
    this.staleMs = opts.staleMs ?? 250
    this.floorDb = opts.floorDb ?? -60
  }

  push(db: number | null, nowMs: number): void {
    this.pending.push({ t: nowMs, level: dbToLevel(db, this.floorDb) })
    if (this.pending.length > MAX_PENDING) this.pending.shift()
  }

  /** Advance to `nowMs` and return the smoothed, delayed level (0..1). */
  valueAt(nowMs: number): number {
    const playhead = nowMs - this.delayMs
    while (this.pending.length > 0 && this.pending[0].t <= playhead) {
      this.current = this.pending.shift() ?? null
    }
    const cur = this.current
    const target = cur && playhead - cur.t <= this.staleMs ? cur.level : 0
    const dt = this.lastNow == null ? 0 : Math.max(0, nowMs - this.lastNow)
    this.lastNow = nowMs
    const k = this.tauMs <= 0 ? 1 : 1 - Math.exp(-dt / this.tauMs)
    this.value += (target - this.value) * k
    return this.value
  }
}
