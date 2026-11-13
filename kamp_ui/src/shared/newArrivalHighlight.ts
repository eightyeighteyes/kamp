// KAMP-770: the New Arrival Highlight preference. Framework-free so `node --test`
// can exercise it directly.
//
// The picker shows one value ('off' or a style), but storage keeps the two
// pre-KAMP-770 keys with their original meaning: the enabled key is the on/off
// switch, and the style key always holds a real style. 'off' is never written to
// the style key. That keeps a downgraded build (checkbox + style select) working:
// if it re-ticks the box, it lands on a real style rather than 'off', which has no
// CSS. It also means no migration: an unticked box already reads as 'off'.

import { offFirstAlphabetical } from './pickerOrder.ts'

export const HIGHLIGHT_STYLES = [
  'shiny',
  'newmoji',
  'vaporwave',
  'proud',
  'pressed',
  'boring',
  'static'
] as const
export type HighlightStyle = (typeof HIGHLIGHT_STYLES)[number]
export type HighlightChoice = 'off' | HighlightStyle

export const HIGHLIGHT_ENABLED_KEY = 'kamp:highlight-enabled'
export const HIGHLIGHT_STYLE_KEY = 'kamp:highlight-style'

const DEFAULT_STYLE: HighlightStyle = 'shiny'

function isHighlightStyle(value: string | null): value is HighlightStyle {
  return (HIGHLIGHT_STYLES as readonly (string | null)[]).includes(value)
}

/** The stored style, or shiny when it is unset or not a style this build knows. */
export function resolveHighlightStyle(stored: string | null): HighlightStyle {
  return isHighlightStyle(stored) ? stored : DEFAULT_STYLE
}

/** The picker value for the stored switch and style. */
export function highlightChoice(enabled: boolean, style: HighlightStyle): HighlightChoice {
  return enabled ? style : 'off'
}

// Labels are the ids themselves (lowercase, matching the rest of the Style rail).
const CHOICES: readonly HighlightChoice[] = ['off', ...HIGHLIGHT_STYLES]
export const HIGHLIGHT_PICKER_ORDER: readonly HighlightChoice[] = offFirstAlphabetical(
  CHOICES,
  'off',
  Object.fromEntries(CHOICES.map((c) => [c, c])) as Record<HighlightChoice, string>
)
