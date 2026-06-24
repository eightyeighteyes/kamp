import './assets/main.css'
import './assets/tooltip.css'
import './assets/themes.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { TooltipProvider } from './components/TooltipProvider'
import { themes } from '../../shared/theme'
import type { ThemeName } from '../../shared/theme'

// Apply the persisted theme on mount. --bg must be set as an inline style
// (not just via [data-theme] CSS) because the main process sets backgroundColor
// from the same value and inline styles take precedence over attribute selectors.
const savedTheme = (localStorage.getItem('kamp:selected-theme') as ThemeName | null) ?? 'kamp'
const initialTokens = themes[savedTheme] ?? themes.kamp
document.documentElement.dataset.theme = savedTheme
document.documentElement.style.setProperty('--bg', initialTokens.bg)

// Expose the platform to CSS so platform-specific chrome (e.g. right padding
// on .view-tabs that clears the Windows titleBarOverlay) can target it.
document.documentElement.dataset.platform = window.electron.process.platform

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TooltipProvider>
      <App />
    </TooltipProvider>
  </StrictMode>
)
