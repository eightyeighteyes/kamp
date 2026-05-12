import React from 'react'

// Inline-SVG transport icons. Replace the Unicode glyphs (⏮ ▶ ⏸ ⏹ ⏭ 🔊 ☰)
// that rendered inconsistently between macOS (Apple Color Emoji / system fallback) and
// Windows (Segoe UI Symbol / Emoji) — KAMP-291. viewBox 24, currentColor, and even-pixel
// vertex coords match the existing favorite-heart in TransportBar.tsx.

interface IconProps {
  size?: number
}

const FILL_PROPS = {
  viewBox: '0 0 24 24',
  fill: 'currentColor',
  'aria-hidden': true,
  focusable: 'false'
} as const

const STROKE_PROPS = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: 'false'
} as const

export function PrevIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M8 12 L19 5 L19 19 Z" />
      <rect x="5" y="5" width="2" height="14" />
    </svg>
  )
}

export function PlayIcon({ size = 26 }: IconProps): React.JSX.Element {
  // M8 5 L19 12 L8 19 Z — nudged 1px left from the geometric center so the
  // triangle appears optically centered (visual mass of an isoceles triangle
  // sits ~6% right of its geometric centroid).
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M8 5 L19 12 L8 19 Z" />
    </svg>
  )
}

export function PauseIcon({ size = 26 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <rect x="7" y="5" width="3" height="14" />
      <rect x="14" y="5" width="3" height="14" />
    </svg>
  )
}

export function StopIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <rect x="6" y="6" width="12" height="12" />
    </svg>
  )
}

export function NextIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M5 5 L16 12 L5 19 Z" />
      <rect x="17" y="5" width="2" height="14" />
    </svg>
  )
}

export function VolumeIcon({ size = 20 }: IconProps): React.JSX.Element {
  // Speaker cone + two arc waves. Matches Lucide's Volume2 geometry so the
  // optical weight aligns with other stroke-based icons (the heart).
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  )
}

export function QueueIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <line x1="4" y1="7" x2="20" y2="7" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="17" x2="20" y2="17" />
    </svg>
  )
}

interface FavoriteIconProps {
  active: boolean
  size?: number
}

export function FavoriteIcon({ active, size = 16 }: FavoriteIconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={active ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
    </svg>
  )
}
