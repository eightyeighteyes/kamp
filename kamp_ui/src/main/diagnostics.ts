/**
 * Opt-in process metrics sampling for the Electron side (KAMP-716).
 *
 * Prerequisite instrumentation for KAMP-680 ("1.6 GB of memory usage" after a
 * few days) and KAMP-704 (kamp degrading other applications' draw performance).
 *
 * KAMP-680's blocking question is *which* process holds the memory. kamp spans
 * four long-lived processes — Electron main, the Chromium renderer, the GPU
 * process, and the Python daemon — plus mpv and spawn-mode sync workers.
 * `app.getAppMetrics()` answers that for the Electron half directly and for
 * free: it returns CPU and memory per process, already tagged by type and pid,
 * with no native code and no extra dependency. The Python half is covered by
 * `kamp_core/diagnostics.py`, which writes the same JSONL shape.
 *
 * The CPU figures matter for KAMP-704 too: that ticket notes a CPU pass has
 * already been done, so the open question is whether the cost now sits in the
 * GPU process. `getAppMetrics()` reports the GPU process as its own row, which
 * is exactly the attribution needed before touching any animation code.
 *
 * Off unless KAMP_DIAGNOSTICS is set — no cost in normal use.
 */

import { app } from 'electron'
import { appendFileSync, mkdirSync } from 'fs'
import { join } from 'path'

const TRUTHY = new Set(['1', 'true', 'yes', 'on'])

// A leak that takes days to surface does not need second-resolution sampling,
// and a tighter interval only inflates the log. Matches the Python side.
const DEFAULT_INTERVAL_MS = 60_000

export function diagnosticsEnabled(): boolean {
  return TRUTHY.has((process.env.KAMP_DIAGNOSTICS ?? '').trim().toLowerCase())
}

export function diagnosticsDir(): string {
  return join(app.getPath('userData'), 'diagnostics')
}

/** Per-process row, mirroring kamp_core.diagnostics.ProcessSample. */
interface ProcRow {
  pid: number
  role: string
  rss_bytes: number
  cpu_pct: number
}

/**
 * One tick of per-process metrics.
 *
 * Exported for the renderer probe to reuse the shape; `procs` carries one row
 * per Electron process so a tick's processes stay correlated — the question is
 * "where did the total go", which needs them read together.
 */
export interface MetricsRecord {
  t: number
  procs: ProcRow[]
}

export function collectMetrics(now: number = Date.now()): MetricsRecord {
  return {
    t: now / 1000, // seconds, to match the Python side's time.time()
    procs: app.getAppMetrics().map((m) => ({
      pid: m.pid,
      // 'Tab' is Chromium's internal name for a renderer; relabel it so the
      // log reads the way the bug reports do.
      role: m.type === 'Tab' ? 'renderer' : m.type,
      // workingSetSize is reported in kilobytes.
      rss_bytes: m.memory.workingSetSize * 1024,
      cpu_pct: m.cpu?.percentCPUUsage ?? 0
    }))
  }
}

function logPath(now: number): string {
  // Date-stamped so a multi-day capture does not land in one unbounded file.
  const day = new Date(now).toISOString().slice(0, 10)
  return join(diagnosticsDir(), `electron-${day}.jsonl`)
}

/** Append one tick to the log. Never throws. */
export function writeSample(now: number = Date.now()): void {
  try {
    mkdirSync(diagnosticsDir(), { recursive: true })
    appendFileSync(logPath(now), JSON.stringify(collectMetrics(now)) + '\n', 'utf8')
  } catch {
    // Diagnostics are never worth taking the app down for.
  }
}

let timer: NodeJS.Timeout | null = null

/** Begin sampling. Idempotent; a no-op unless KAMP_DIAGNOSTICS is set. */
export function startDiagnostics(intervalMs: number = DEFAULT_INTERVAL_MS): void {
  if (!diagnosticsEnabled() || timer !== null) return
  // Sample immediately so a short session still records something.
  writeSample()
  timer = setInterval(writeSample, intervalMs)
  // Do not hold the event loop open on this alone.
  timer.unref?.()
  console.log(`[diagnostics] sampling every ${intervalMs}ms -> ${diagnosticsDir()}`)
}

export function stopDiagnostics(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

/**
 * Record one renderer probe reading (KAMP-704's hypothesis test).
 *
 * `runningAnimations` is the headline: the claim under test was that kamp keeps
 * animating when its window is not focused, because every animated surface gates
 * on `document.hidden` — true only when minimized or fully occluded, not when the
 * user task-switches away. Measuring it settled the question (it is ~0).
 *
 * `appRafRequests` counts animation frames *application code* asked for. It
 * replaced an earlier `rafTicks` field that counted callbacks served to the
 * probe's own rAF loop, which measured Chromium's frame cadence rather than the
 * app's demand for frames — and kept the compositor awake in the process. The
 * rename is deliberate: the two numbers are not comparable, so old captures must
 * not be read as if they were new ones.
 */
export function writeRendererSample(sample: {
  focused: boolean
  hidden: boolean
  appRafRequests: number
  runningAnimations: number
}): void {
  try {
    mkdirSync(diagnosticsDir(), { recursive: true })
    const now = Date.now()
    const day = new Date(now).toISOString().slice(0, 10)
    appendFileSync(
      join(diagnosticsDir(), `renderer-${day}.jsonl`),
      JSON.stringify({ t: now / 1000, ...sample }) + '\n',
      'utf8'
    )
  } catch {
    // As above.
  }
}
