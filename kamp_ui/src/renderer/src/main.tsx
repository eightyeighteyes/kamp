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
