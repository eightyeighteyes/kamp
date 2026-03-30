/**
 * Design token constants shared between the main process and the renderer.
 *
 * The main process needs these at window-creation time (before the renderer
 * loads), so they can't live in CSS. Defining them here lets both sides stay
 * in sync: the main process reads them directly; the renderer sets them as
 * CSS custom properties on <html> so the stylesheet can reference var(--bg) etc.
 */

export const theme = {
  /** Primary app background — must match the BrowserWindow backgroundColor. */
  bg: '#141414'
} as const
