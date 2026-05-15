import { useLayoutEffect } from 'react'
import type React from 'react'

/**
 * Clamps a context menu element to the visible viewport.
 *
 * After the menu renders, measures its bounding rect and shifts it left/up
 * by the overflow amount so it never escapes the window edge.  Works for
 * both cursor-spawned menus (positioned at click coordinates) and
 * button-anchored menus (positioned via CSS).
 *
 * @param ref     Ref attached to the menu element.
 * @param trigger Changing this value re-runs the clamp (pass the menu state
 *                object or open boolean — falsy value means menu is closed).
 */
const MARGIN = 8

export function useMenuBounds(ref: React.RefObject<HTMLElement | null>, trigger: unknown): void {
  useLayoutEffect(() => {
    if (!trigger) return
    const el = ref.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    if (rect.right > window.innerWidth - MARGIN) {
      el.style.left = `${el.offsetLeft - (rect.right - (window.innerWidth - MARGIN))}px`
    }
    if (rect.bottom > window.innerHeight - MARGIN) {
      el.style.top = `${el.offsetTop - (rect.bottom - (window.innerHeight - MARGIN))}px`
    }
  }, [ref, trigger])
}
