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

export function QueueAddIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <line x1="3" y1="7" x2="15" y2="7" />
      <line x1="3" y1="12" x2="15" y2="12" />
      <line x1="3" y1="17" x2="11" y2="17" />
      <line x1="18" y1="14" x2="18" y2="20" />
      <line x1="15" y1="17" x2="21" y2="17" />
    </svg>
  )
}

export function PlayNextIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <rect x="3" y="5" width="2" height="14" />
      <path d="M7 5 L18 12 L7 19 Z" />
    </svg>
  )
}

export function GoToAlbumIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

export function GoToArtistIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  )
}

export function PencilIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  )
}

export function TagIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
      <circle cx="7" cy="7" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function ChevronIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

interface FavoriteIconProps {
  active: boolean
  size?: number
}

export function ShuffleIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M16.4697 5.46967C16.1768 5.76256 16.1768 6.23744 16.4697 6.53033L17.1893 7.25H13.3768C12.706 7.25 12.0942 7.63343 11.8018 8.23713L11.5914 8.67144C11.5381 8.78157 11.5381 8.91006 11.5914 9.02018L12.1603 10.1947C12.2332 10.3451 12.4474 10.3451 12.5203 10.1947L13.1518 8.89102C13.1935 8.80478 13.2809 8.75 13.3768 8.75H17.1893L16.4697 9.46967C16.1768 9.76256 16.1768 10.2374 16.4697 10.5303C16.7626 10.8232 17.2374 10.8232 17.5303 10.5303L19.5303 8.53033C19.8232 8.23744 19.8232 7.76256 19.5303 7.46967L17.5303 5.46967C17.2374 5.17678 16.7626 5.17678 16.4697 5.46967Z" />
      <path d="M10.0336 15.3286C10.0869 15.2184 10.0869 15.0899 10.0336 14.9798L9.46469 13.8053C9.39183 13.6549 9.17755 13.6549 9.10469 13.8053L8.47324 15.109C8.43146 15.1952 8.34407 15.25 8.24824 15.25H5C4.58579 15.25 4.25 15.5858 4.25 16C4.25 16.4142 4.58579 16.75 5 16.75H8.24824C8.91903 16.75 9.53079 16.3666 9.82321 15.7629L10.0336 15.3286Z" />
      <path d="M16.4697 18.5303C16.1768 18.2374 16.1768 17.7626 16.4697 17.4697L17.1893 16.75H13.3768C12.706 16.75 12.0942 16.3666 11.8018 15.7629L8.47324 8.89102C8.43146 8.80478 8.34407 8.75 8.24824 8.75H5C4.58579 8.75 4.25 8.41421 4.25 8C4.25 7.58579 4.58579 7.25 5 7.25H8.24824C8.91903 7.25 9.53079 7.63343 9.82321 8.23713L13.1518 15.109C13.1935 15.1952 13.2809 15.25 13.3768 15.25H17.1893L16.4697 14.5303C16.1768 14.2374 16.1768 13.7626 16.4697 13.4697C16.7626 13.1768 17.2374 13.1768 17.5303 13.4697L19.5303 15.4697C19.8232 15.7626 19.8232 16.2374 19.5303 16.5303L17.5303 18.5303C17.2374 18.8232 16.7626 18.8232 16.4697 18.5303Z" />
    </svg>
  )
}

export function RepeatIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M6.54544 8.16273C6.33022 8.10595 6.15134 7.95651 6.05718 7.75482C5.96302 7.55313 5.96331 7.32004 6.05797 7.11859L7.71872 3.5842C7.84248 3.32081 8.10743 3.15279 8.39845 3.15315C8.68946 3.15351 8.95399 3.32219 9.0771 3.58588L9.80973 5.15511C9.83592 5.14482 9.86297 5.13589 9.8908 5.12843C14.2381 3.96357 18.7067 6.54347 19.8715 10.8908C21.0364 15.2382 18.4565 19.7067 14.1092 20.8716C9.76181 22.0364 5.29328 19.4565 4.12841 15.1092C3.75798 13.7267 3.76632 12.3299 4.09075 11.0311C4.19114 10.6293 4.5983 10.3849 5.00016 10.4853C5.40203 10.5856 5.64642 10.9928 5.54603 11.3947C5.28174 12.4527 5.27445 13.5907 5.5773 14.721C6.52775 18.2681 10.1738 20.3731 13.7209 19.4227C17.2681 18.4722 19.3731 14.8262 18.4227 11.2791C17.4877 7.7899 13.9447 5.69609 10.4531 6.53314L11.1923 8.11644C11.3154 8.38013 11.2748 8.69124 11.0883 8.91457C10.9017 9.1379 10.6028 9.23314 10.3214 9.15891L6.54544 8.16273Z" />
    </svg>
  )
}

export function RemoveFromQueueIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M8.46447 15.5355L15.5355 8.46446" />
      <path d="M8.46447 8.46447L15.5355 15.5355" />
    </svg>
  )
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
