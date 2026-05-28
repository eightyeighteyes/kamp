import React, { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { TooltipContext } from '../hooks/useTooltip'

const SHOW_DELAY_MS = 500
const VISIBLE_MS = 2000
// Rescind window: hot-switching is active for this long after the tooltip begins fading.
// The CSS fade animation is 300ms (tooltip.css); this timer controls when the portal
// is removed from the DOM, keeping it available for hot-switch detection.
const RESCIND_MS = 1500
const MARGIN = 8

// Threshold below which a target is considered "near the top of the window"
// and the tooltip should appear below rather than above.
const TOP_THRESHOLD = 50

type Phase = 'visible' | 'fading'

interface TooltipDisplay {
  text: string
  x: number
  y: number
  above: boolean
  phase: Phase
}

export function TooltipProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [display, setDisplay] = useState<TooltipDisplay | null>(null)
  const portalRef = useRef<HTMLDivElement>(null)

  // Phase tracked in a ref so timer callbacks always read the current value.
  const phaseRef = useRef<'hidden' | 'delay' | 'visible' | 'fading'>('hidden')
  // The element currently showing a tooltip — receives aria-describedby while visible.
  const targetRef = useRef<HTMLElement | null>(null)

  const showTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const beginFade = useCallback(() => {
    phaseRef.current = 'fading'
    setDisplay((prev) => (prev ? { ...prev, phase: 'fading' } : null))
    fadeTimerRef.current = setTimeout(() => {
      if (targetRef.current) {
        targetRef.current.removeAttribute('aria-describedby')
        targetRef.current = null
      }
      phaseRef.current = 'hidden'
      setDisplay(null)
    }, RESCIND_MS)
  }, [])

  const arm = useCallback(
    (text: string, target: HTMLElement) => {
      clearTimeout(showTimerRef.current)
      clearTimeout(hideTimerRef.current)
      clearTimeout(fadeTimerRef.current)

      const rect = target.getBoundingClientRect()
      const above = rect.top > TOP_THRESHOLD
      const x = rect.left + rect.width / 2
      const y = above ? rect.top : rect.bottom

      const show = (): void => {
        if (targetRef.current) targetRef.current.removeAttribute('aria-describedby')
        targetRef.current = target
        target.setAttribute('aria-describedby', 'kamp-tooltip')
        phaseRef.current = 'visible'
        setDisplay({ text, x, y, above, phase: 'visible' })
        hideTimerRef.current = setTimeout(beginFade, VISIBLE_MS)
      }

      // Hot-switch: during the rescind window, skip the show delay.
      if (phaseRef.current === 'fading') {
        show()
        return
      }

      phaseRef.current = 'delay'
      showTimerRef.current = setTimeout(show, SHOW_DELAY_MS)
    },
    [beginFade]
  )

  // mouseleave only cancels the pending show timer; a visible tooltip times out
  // on its own (the 2000ms visible period is not hover-dependent).
  const disarm = useCallback(() => {
    clearTimeout(showTimerRef.current)
    if (phaseRef.current === 'delay') phaseRef.current = 'hidden'
  }, [])

  // Clamp the tooltip horizontally so it never overflows the viewport edges.
  useLayoutEffect(() => {
    if (!display || !portalRef.current) return
    const el = portalRef.current
    const rect = el.getBoundingClientRect()
    if (rect.left < MARGIN) {
      el.style.left = `${display.x + (MARGIN - rect.left)}px`
    } else if (rect.right > window.innerWidth - MARGIN) {
      el.style.left = `${display.x - (rect.right - (window.innerWidth - MARGIN))}px`
    }
  }, [display])

  return (
    <TooltipContext.Provider value={{ arm, disarm }}>
      {children}
      {display &&
        createPortal(
          <div
            ref={portalRef}
            role="tooltip"
            id="kamp-tooltip"
            className="kamp-tooltip"
            data-phase={display.phase}
            data-direction={display.above ? 'above' : 'below'}
            style={{ left: display.x, top: display.y }}
          >
            {display.text}
          </div>,
          document.body
        )}
    </TooltipContext.Provider>
  )
}
