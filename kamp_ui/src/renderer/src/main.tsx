import './assets/main.css'
import './assets/tooltip.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { TooltipProvider } from './components/TooltipProvider'
import { applyTheme } from '../../shared/theme'
import type { ThemeName } from '../../shared/theme'

// Apply all theme tokens on mount from the persisted selection.
// theme.ts is the single source of truth — no themes.css needed.
const savedTheme = (localStorage.getItem('kamp:selected-theme') as ThemeName | null) ?? 'kamp'
applyTheme(savedTheme, document.documentElement)
// KAMP-631: also sync the native window chrome to the saved palette. The window
// is created with the DEFAULT theme bg, so without this a saved non-default
// palette leaves the native bg (and Windows titlebar) mismatched — it bleeds
// through transparent surfaces as a letterbox seam. Mirrors store.setTheme.
window.api.syncThemeChrome(savedTheme)

// Expose the platform to CSS so platform-specific chrome (e.g. right padding
// on .view-tabs that clears the Windows titleBarOverlay) can target it.
document.documentElement.dataset.platform = window.electron.process.platform

// KAMP-676: macOS hides the traffic lights in fullscreen, so the 76px the nav
// bar reserves for them becomes a dead gap. Seeded from the current state
// because a window restored fullscreen entered it before this script ran, then
// kept current. Not unsubscribed — this lives for the document's lifetime.
const setFullScreen = (on: boolean): void => {
  document.documentElement.dataset.fullscreen = on ? 'true' : 'false'
}
setFullScreen(false)
window.api
  .isFullScreen()
  .then(setFullScreen)
  .catch(() => setFullScreen(false))
window.api.onFullScreenChange(setFullScreen)

// How focus was last established, which decides whether a ring paints (KAMP-685).
// Seeded here rather than in App so there is no first frame without it, and seeded
// to 'key' so a keyboard-only user who never touches the mouse gets a ring from
// their very first Tab. App's capture listeners keep it current from then on.
document.documentElement.dataset.focusModality = 'key'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TooltipProvider>
      <App />
    </TooltipProvider>
  </StrictMode>
)
