/**
 * Design token constants shared between the main process and the renderer.
 *
 * The main process needs these at window-creation time (before the renderer
 * loads), so they can't live in CSS. Defining them here lets both sides stay
 * in sync: the main process reads them directly; the renderer sets them as
 * CSS custom properties on <html> so the stylesheet can reference var(--bg) etc.
 */

export type ThemeName =
  | 'kamp'
  | 'strawberry-switchblade'
  | 'beach-house'
  | 'blackpink'
  | 'green-day'
  | 'foxy-brown'
  | 'golden-smog'

export type ThemeTokens = {
  bg: string
  surface: string
  surfaceHover: string
  border: string
  text: string
  textDim: string
  accent: string
  accentDim: string
  textOnAccent: string
}

export const themes: Record<ThemeName, ThemeTokens> = {
  kamp: {
    bg: '#141414',
    surface: '#1e1e1e',
    surfaceHover: '#2a2a2a',
    border: '#2e2e2e',
    text: '#e0e0e0',
    textDim: '#888888',
    accent: '#7c86e1',
    accentDim: '#252b5c',
    textOnAccent: '#000000'
  },
  'strawberry-switchblade': {
    bg: '#110d0f',
    surface: '#1c1318',
    surfaceHover: '#271b20',
    border: '#3a2028',
    text: '#f0dde3',
    textDim: '#9a7a82',
    accent: '#e8305a',
    accentDim: '#3d0d1a',
    textOnAccent: '#ffffff'
  },
  'beach-house': {
    bg: '#0c1014',
    surface: '#131a20',
    surfaceHover: '#1c2630',
    border: '#243040',
    text: '#d4dde8',
    textDim: '#6a8090',
    accent: '#5fb3c4',
    accentDim: '#102830',
    textOnAccent: '#000000'
  },
  blackpink: {
    bg: '#0f0a0d',
    surface: '#1a1018',
    surfaceHover: '#261820',
    border: '#38182c',
    text: '#f5e6ef',
    textDim: '#9a7088',
    accent: '#f72585',
    accentDim: '#3d0525',
    textOnAccent: '#ffffff'
  },
  'green-day': {
    bg: '#0d100a',
    surface: '#141910',
    surfaceHover: '#1e2418',
    border: '#2a3020',
    text: '#dce8d0',
    textDim: '#7a9060',
    accent: '#78d42a',
    accentDim: '#1e3a08',
    textOnAccent: '#000000'
  },
  'foxy-brown': {
    bg: '#100c08',
    surface: '#1c1510',
    surfaceHover: '#28201a',
    border: '#3a2a1e',
    text: '#f0e2cc',
    textDim: '#9a7a50',
    accent: '#c8882a',
    accentDim: '#3a2008',
    textOnAccent: '#000000'
  },
  'golden-smog': {
    bg: '#0f0e09',
    surface: '#1a1910',
    surfaceHover: '#252418',
    border: '#353320',
    text: '#ede8cc',
    textDim: '#8a8060',
    accent: '#c8a832',
    accentDim: '#322800',
    textOnAccent: '#000000'
  }
}

/** Primary app background for the default theme — used by the main process at window-creation time. */
export const theme = themes.kamp
