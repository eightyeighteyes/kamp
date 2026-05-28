import { createContext, useCallback, useContext } from 'react'
import type React from 'react'

export interface TooltipContextValue {
  arm: (text: string, target: HTMLElement) => void
  disarm: () => void
}

export const TooltipContext = createContext<TooltipContextValue>({
  arm: () => {},
  disarm: () => {}
})

/**
 * Returns a function that, given a tooltip string, produces onMouseEnter /
 * onMouseLeave props for any interactive element.
 *
 * Usage:
 *   const tooltip = useTooltip()
 *   <button {...tooltip(TOOLTIPS.TRANSPORT_PLAY)} onClick={...} />
 *
 * For dynamic text, pass the computed string directly:
 *   <button {...tooltip(playing ? TOOLTIPS.TRANSPORT_PAUSE : TOOLTIPS.TRANSPORT_PLAY)} />
 */
export function useTooltip(): (text: string) => {
  onMouseEnter: (e: React.MouseEvent<HTMLElement>) => void
  onMouseLeave: () => void
} {
  const { arm, disarm } = useContext(TooltipContext)
  return useCallback(
    (text: string) => ({
      onMouseEnter: (e: React.MouseEvent<HTMLElement>) => arm(text, e.currentTarget),
      onMouseLeave: disarm
    }),
    [arm, disarm]
  )
}
