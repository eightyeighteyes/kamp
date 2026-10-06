/**
 * Animation-activity probe (KAMP-716, instrumentation for KAMP-704).
 *
 * KAMP-704 reports that kamp degrades the draw performance of other apps,
 * especially other Electron apps, and that closing kamp immediately fixes it.
 * The leading hypothesis is structural: nothing in kamp pauses animation when
 * the window loses focus. Every animated surface gates on `document.hidden`
 * (BokehBackground, StereoRackModule, bokehEngine), and `document.hidden` only
 * goes true when the window is minimized or *fully* occluded — not when the
 * user task-switches to Chrome and leaves kamp visible behind it. There is no
 * `blur`/`focus` gating anywhere in the main process.
 *
 * This probe turns that hypothesis into a number before any of the 4 rAF
 * engines or 41 infinite CSS animations are touched, per the CLAUDE.md
 * diagnosis-discipline rule: if `runningAnimations` stays high while `focused`
 * is false, the hypothesis is confirmed by measurement. If it does not, the
 * cause is elsewhere and the planned fix would have been wasted work.
 *
 * What each field answers:
 *   focused           - is kamp the active app right now?
 *   hidden            - what `document.hidden` believes (expected: false while
 *                       merely unfocused, which is the whole point)
 *   rafTicks          - requestAnimationFrame callbacks served in the window;
 *                       ~60/s means Chromium is still driving full-rate frames
 *   runningAnimations  - CSS/Web animations actually running, via
 *                       document.getAnimations(). The headline number.
 *
 * Self-gating: the hook asks the main process whether KAMP_DIAGNOSTICS is set
 * and stays completely inert otherwise. That gate matters more here than
 * elsewhere -- an always-on rAF loop would itself be another animation running
 * on an unfocused window, i.e. the bug under investigation.
 */

import { useEffect, useState } from 'react'

const SAMPLE_INTERVAL_MS = 10_000

function countRunningAnimations(): number {
  // getAnimations() is the only way to see CSS animations the renderer is
  // actually driving -- counting stylesheet rules would count declarations,
  // not running work.
  try {
    return document.getAnimations().filter((a) => a.playState === 'running').length
  } catch {
    return -1 // unsupported; distinguishable from a genuine zero
  }
}

export function useDiagnosticsProbe(): void {
  const [enabled, setEnabled] = useState(false)

  useEffect(() => {
    window.api
      ?.diagnosticsEnabled?.()
      .then(setEnabled)
      .catch(() => setEnabled(false))
  }, [])

  useEffect(() => {
    if (!enabled) return

    let rafTicks = 0
    let rafId = 0
    const tick = (): void => {
      rafTicks += 1
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)

    const timer = setInterval(() => {
      window.api?.reportDiagnosticsSample?.({
        focused: document.hasFocus(),
        hidden: document.hidden,
        rafTicks,
        runningAnimations: countRunningAnimations()
      })
      rafTicks = 0
    }, SAMPLE_INTERVAL_MS)

    return () => {
      cancelAnimationFrame(rafId)
      clearInterval(timer)
    }
  }, [enabled])
}
