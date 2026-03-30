import './assets/main.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { theme } from '../../shared/theme'

// Apply shared design tokens as CSS custom properties so the stylesheet can
// reference var(--bg) etc. while the main process uses the same values for
// BrowserWindow options (e.g. backgroundColor).
document.documentElement.style.setProperty('--bg', theme.bg)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
)
