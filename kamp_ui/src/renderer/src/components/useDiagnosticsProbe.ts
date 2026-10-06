/**
 * Animation-activity probe (KAMP-716, instrumentation for KAMP-704).
 *
 * Reports, every 10s: whether the window is focused, what `document.hidden`
 * believes, how many animations are actually running, and how many animation
 * frames *application code* asked for.
 *
 * ## Why this does not run its own rAF loop
 *
 * The first version counted frames by running `requestAnimationFrame` in a
 * self-rearming loop. That was wrong in two ways, both found by reading its own
 * output (see the KAMP-704 Phase 0 comment):
 *
 *  1. **It measured the wrong thing.** rAF is a single per-document callback
 *     queue, so one requester is enough to see ~600 callbacks per 10s window.
 *     The count therefore reported the cadence Chromium happened to be driving
 *     the renderer at — not whether anything in kamp wanted frames. With zero
 *     animations running it still read 600, which is uninterpretable.
 *  2. **It perturbed what it measured.** Requesting a frame every frame keeps
 *     the compositor awake. A probe for "does kamp animate while unfocused?"
 *     that itself animates while unfocused is doing a mild version of the bug.
 *
 * So instead of generating frames, this counts the ones app code asks for, by
 * wrapping `requestAnimationFrame` while the probe is active. `appRafRequests`
 * of 0 is now meaningful: nothing in the app is driving frames, and Chromium is
 * free to idle. Non-zero means something is in a rAF loop, and the magnitude
 * tells you the rate it is being served at (~600/10s = 60fps, ~100 = throttled
 * to 10fps).
 *
 * Note the field was **renamed** from `rafTicks` to `appRafRequests` rather than
 * redefined in place: the two are not comparable, and a silently changed meaning
 * would make old and new captures impossible to tell apart.
 *
 * `runningAnimations` is unchanged — it was the uncontaminated metric that
 * actually settled KAMP-704, and it needs no fixing.
 *
 * Self-gating on KAMP_DIAGNOSTICS: with the flag unset, nothing is wrapped,
 * nothing is timed, and nothing is sent.
 */

import { useEffect, useState } from 'react'

const SAMPLE_INTERVAL_MS = 10_000

function countRunningAnimations(): number {
  // getAnimations() is the only way to see the CSS animations the renderer is
  // actually driving -- counting stylesheet rules would count declarations, not
  // running work.
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

    let appRafRequests = 0

    // Count what app code requests. Ids pass through untouched so
    // cancelAnimationFrame keeps working, and the callback is handed straight to
    // the native implementation -- no extra frame is requested by doing this.
    const nativeRaf = window.requestAnimationFrame.bind(window)
    window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
      appRafRequests += 1
      return nativeRaf(callback)
    }

    const timer = setInterval(() => {
      window.api?.reportDiagnosticsSample?.({
        focused: document.hasFocus(),
        hidden: document.hidden,
        appRafRequests,
        runningAnimations: countRunningAnimations()
      })
      appRafRequests = 0
    }, SAMPLE_INTERVAL_MS)

    return () => {
      clearInterval(timer)
      // Restores the native implementation. If something else wrapped rAF on top
      // of this, that wrapper is dropped -- acceptable for a probe that only runs
      // behind an opt-in dev flag, and the alternative (leaving the counter
      // installed forever) is worse.
      window.requestAnimationFrame = nativeRaf
    }
  }, [enabled])
}
