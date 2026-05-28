import './assets/main.css'
import './assets/tooltip.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { TooltipProvider } from './components/TooltipProvider'
import { theme } from '../../shared/theme'

// Apply shared design tokens as CSS custom properties so the stylesheet can
// reference var(--bg) etc. while the main process uses the same values for
// BrowserWindow options (e.g. backgroundColor).
document.documentElement.style.setProperty('--bg', theme.bg)

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
