import React from 'react'
import { useStore } from '../store'
import { themes, applyTheme } from '../../../shared/theme'
import type { ThemeName } from '../../../shared/theme'

type ThemeMeta = {
  label: string
  note: string
  isDefault?: boolean
}

const THEME_META: Record<ThemeName, ThemeMeta> = {
  kamp: { label: 'kamp', note: 'house sound', isDefault: true },
  'strawberry-switchblade': {
    label: 'strawberry switchblade',
    note: 'Glasgow, 1983, bows in hair'
  },
  'beach-house': { label: 'beach house', note: 'dream pop, Baltimore, 2006' },
  blackpink: { label: 'blackpink', note: 'K-pop maximalism' },
  'deep-purple': { label: 'deep purple', note: 'Smoke on the Water, Montreux, 1971' },
  'green-day': { label: 'green day', note: 'three chords and a lot of eyeliner' },
  'foxy-brown': { label: 'foxy brown', note: 'Brooklyn, 1996' },
  'golden-smog': { label: 'golden smog', note: 'Minneapolis supergroup, loose by design' }
}

const THEME_ORDER: ThemeName[] = [
  'blackpink',
  'strawberry-switchblade',
  'foxy-brown',
  'golden-smog',
  'green-day',
  'beach-house',
  'kamp',
  'deep-purple'
]

export function ThemePicker(): React.JSX.Element {
  const selectedTheme = useStore((s) => s.selectedTheme)
  const setTheme = useStore((s) => s.setTheme)

  return (
    <div className="theme-picker">
      {THEME_ORDER.map((name) => {
        const tokens = themes[name]
        const meta = THEME_META[name]
        const isActive = selectedTheme === name
        return (
          <button
            key={name}
            className={`theme-swatch${isActive ? ' theme-swatch--active' : ''}`}
            onClick={() => setTheme(name)}
            onMouseEnter={() => applyTheme(name, document.documentElement)}
            onMouseLeave={() => applyTheme(selectedTheme, document.documentElement)}
            title={meta.note}
            aria-label={`${meta.label}${meta.isDefault ? ' (default)' : ''}`}
            aria-pressed={isActive}
          >
            <span className="theme-swatch-colors">
              <span style={{ background: tokens.bg }} />
              <span style={{ background: tokens.accent }} />
              <span style={{ background: tokens.surface }} />
            </span>
            <span className="theme-swatch-label" style={{ fontWeight: meta.isDefault ? 600 : 400 }}>
              {meta.label}
              {meta.isDefault && <span className="theme-swatch-default">(default)</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}
