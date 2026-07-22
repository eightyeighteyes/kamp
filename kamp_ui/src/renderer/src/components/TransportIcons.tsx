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

export function CollectionIcon({ size = 20 }: IconProps): React.JSX.Element {
  // Stacked-crate "collection" glyph (KAMP-612 asset). Native viewBox is 1200.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 1200 1200"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="m938.63 911.52h-677.27c-26.664 0-48.359-21.695-48.359-48.359v-349.62c0-26.664 21.695-48.359 48.359-48.359h677.26c26.676 0 48.371 21.695 48.371 48.359v349.62c0.011719 26.664-21.695 48.359-48.359 48.359zm-677.27-404.33c-3.457 0-6.3594 2.9141-6.3594 6.3594v349.61c0 3.4453 2.9141 6.3594 6.3594 6.3594h677.26c3.457 0 6.3711-2.9141 6.3711-6.3594v-349.62c0-3.4453-2.9141-6.3594-6.3711-6.3594h-677.26zm639.86-109.36c0-11.594-9.3945-21-21-21h-560.45c-11.594 0-21 9.4062-21 21s9.4062 21 21 21h560.44c11.602 0 21.008-9.4062 21.008-21zm-68.496-88.344c0-11.594-9.3945-21-21-21h-423.45c-11.594 0-21 9.4062-21 21s9.4062 21 21 21h423.46c11.59 0 20.996-9.4062 20.996-21z" />
    </svg>
  )
}

export function MergeIcon({ size = 20 }: IconProps): React.JSX.Element {
  // Two streams converging into one — "merge" glyph (KAMP-607 asset). viewBox 1200.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 710 605"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M698.768 336.022C698.179 336.611 697.549 337.205 696.919 337.757L538.452 496.223C523.692 510.983 499.759 510.983 484.999 496.223C470.28 481.463 470.28 457.53 484.999 442.77L585.207 342.598H382.5C366.402 342.952 354.594 345.119 346.484 356.177L231.787 554.83C230.605 556.877 229.266 558.768 227.771 560.497H227.813C197.229 605.366 169.01 605.133 118.354 604.658C115.011 604.616 111.547 604.58 108.005 604.58H37.7867C16.9267 604.58 0 587.653 0 566.793C0 545.933 16.9267 529.007 37.7867 529.007H133.199C148.001 528.496 159.453 525.975 167.125 515.627L287.729 306.773C288.714 305.081 289.776 303.508 290.959 302.049L291.078 301.929C291.985 300.747 293.005 299.648 294.031 298.622C317.843 273.315 342.286 268.273 376.015 267.253C377.432 267.096 378.89 267.018 380.385 267.018H576.318L485.001 175.701C470.282 160.941 470.282 137.008 485.001 122.248C499.761 107.488 523.694 107.488 538.454 122.248L694.161 277.994C694.239 278.036 694.281 278.114 694.359 278.151L696.916 280.708C697.547 281.26 698.177 281.849 698.765 282.442C706.166 289.843 709.864 299.562 709.828 309.286C709.828 318.969 706.166 328.614 698.765 336.01L698.768 336.022Z" />
      <path d="M293.554 232.663C275.486 243.095 252.383 236.876 241.956 218.808L166.701 88.5213C156.389 75.2973 139.545 75.4535 117.228 75.6879C114.275 75.6879 111.207 75.7296 108.02 75.7296V75.5733H37.8009C16.9409 75.5733 0.0142212 58.6467 0.0142212 37.7867C0.0142212 16.9267 16.9409 0 37.8009 0H143.604C145.098 0 146.557 0.078127 148.01 0.234375C180.916 1.53126 203.39 8.81251 227.203 43.2917C228.62 44.9844 229.917 46.8333 231.099 48.8438L307.38 181.016C317.849 199.084 311.63 222.188 293.563 232.656L293.554 232.663Z" />
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

const REPEAT_ICON_PATH =
  'M6.54544 8.16273C6.33022 8.10595 6.15134 7.95651 6.05718 7.75482C5.96302 7.55313 5.96331 7.32004 6.05797 7.11859L7.71872 3.5842C7.84248 3.32081 8.10743 3.15279 8.39845 3.15315C8.68946 3.15351 8.95399 3.32219 9.0771 3.58588L9.80973 5.15511C9.83592 5.14482 9.86297 5.13589 9.8908 5.12843C14.2381 3.96357 18.7067 6.54347 19.8715 10.8908C21.0364 15.2382 18.4565 19.7067 14.1092 20.8716C9.76181 22.0364 5.29328 19.4565 4.12841 15.1092C3.75798 13.7267 3.76632 12.3299 4.09075 11.0311C4.19114 10.6293 4.5983 10.3849 5.00016 10.4853C5.40203 10.5856 5.64642 10.9928 5.54603 11.3947C5.28174 12.4527 5.27445 13.5907 5.5773 14.721C6.52775 18.2681 10.1738 20.3731 13.7209 19.4227C17.2681 18.4722 19.3731 14.8262 18.4227 11.2791C17.4877 7.7899 13.9447 5.69609 10.4531 6.53314L11.1923 8.11644C11.3154 8.38013 11.2748 8.69124 11.0883 8.91457C10.9017 9.1379 10.6028 9.23314 10.3214 9.15891L6.54544 8.16273Z'

export function RepeatIcon({
  size = 20,
  mode = 'off'
}: {
  size?: number
  mode?: string
}): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d={REPEAT_ICON_PATH} />
      {mode === 'album' && <circle cx="12" cy="13.5" r="2.5" />}
      {mode === 'single' && (
        <text
          x="12"
          y="15.5"
          textAnchor="middle"
          fontSize="7"
          fontWeight="bold"
          fontFamily="system-ui, sans-serif"
        >
          1
        </text>
      )}
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
      <path d="M0.46447 23.5355L23.5355 0.46446" />
      <path d="M0.46447 0.46447L23.5355 23.5355" />
    </svg>
  )
}

// KAMP-570: retry a failed download — a clockwise circular arrow.
export function RetryIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...STROKE_PROPS}>
      <path d="M21 5v6h-6" />
      <path d="M19 13a8 8 0 1 1-2-6.34L21 11" />
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

export function CloudIcon({ size = 10 }: IconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M12.932 4.70825C11.0205 4.70825 9.34183 5.69967 8.38009 7.19396C7.89905 7.06678 7.39433 6.99913 6.87467 6.99913C3.62978 6.99913 0.999268 9.62964 0.999268 12.8745C0.999268 16.1194 3.62978 18.7499 6.87467 18.7499H18.5233C20.9962 18.7499 23.0009 16.7453 23.0009 14.2724C23.0009 11.7995 20.9962 9.79481 18.5233 9.79481C18.4593 9.79481 18.3956 9.79616 18.3322 9.79883C18.1671 6.9597 15.8125 4.70825 12.932 4.70825Z"
        fill="currentColor"
      />
    </svg>
  )
}

export function BandcampIcon({ size = 10 }: IconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path d="M0 18.75l7.437-13.5H24L16.563 18.75z" fill="#4DA9D1" />
    </svg>
  )
}

export function DiscordIcon({ size = 16 }: IconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057.102 18.081.116 18.104.136 18.117a19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.419 0 1.334-.956 2.419-2.157 2.419zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.419 0 1.334-.946 2.419-2.157 2.419z"
        fill="currentColor"
      />
    </svg>
  )
}

export function WarnIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z" fill="currentColor" />
    </svg>
  )
}

export function DownloadArrowIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 17"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M1.5 9.00024C1.5 8.58603 1.16421 8.25024 0.75 8.25024C0.335787 8.25024 -1.46777e-08 8.58603 -3.27835e-08 9.00024L-3.38753e-07 16C-3.81e-07 16.9665 0.0335013 16.7502 1 16.7502L14.5 16.7502C15.4665 16.7502 15.5 16.9665 15.5 16L15.5 9.00024C15.5 8.58603 15.1642 8.25024 14.75 8.25024C14.3358 8.25024 14 8.58603 14 9.00024L14 15.0002C14 15.1383 13.8881 15.2502 13.75 15.2502L1.75 15.2502C1.61193 15.2502 1.5 15.1383 1.5 15.0002L1.5 9.00024Z"
        fill="currentColor"
      />
      <path
        d="M8.53947 10.4393L11.4023 7.71967C11.7106 7.42678 12.2105 7.42678 12.5188 7.71967C12.8271 8.01256 12.8271 8.48744 12.5188 8.78033L8 13C7.69169 13.2929 7.80831 13.2929 7.5 13L2.98123 8.78033C2.67292 8.48744 2.67292 8.01256 2.98123 7.71967C3.28954 7.42678 3.78941 7.42678 4.09772 7.71967L6.96053 10.4393L6.96053 0.75C6.96053 0.335787 7.31398 0 7.75 0C8.18601 0 8.53947 0.335787 8.53947 0.75L8.53947 10.4393Z"
        fill="currentColor"
      />
    </svg>
  )
}

export function ShareIcon({ size = 24 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M14.25 5.5C14.25 3.70507 15.7051 2.25 17.5 2.25C19.2949 2.25 20.75 3.70507 20.75 5.5C20.75 7.29493 19.2949 8.75 17.5 8.75C16.5404 8.75 15.6779 8.33409 15.083 7.6727L12.3657 9.15487L9.32515 10.8923C9.59552 11.3664 9.75 11.9152 9.75 12.5C9.75 12.7963 9.71034 13.0834 9.63603 13.3562L15.083 16.3273C15.6779 15.6659 16.5404 15.25 17.5 15.25C19.2949 15.25 20.75 16.7051 20.75 18.5C20.75 20.2949 19.2949 21.75 17.5 21.75C15.7051 21.75 14.25 20.2949 14.25 18.5C14.25 18.2036 14.2897 17.9166 14.364 17.6438L8.91704 14.6727C8.32212 15.3341 7.45963 15.75 6.5 15.75C4.70507 15.75 3.25 14.2949 3.25 12.5C3.25 10.7051 4.70507 9.25 6.5 9.25C7.15068 9.25 7.7567 9.44122 8.26492 9.77052L11.6343 7.84514L14.364 6.35625C14.2897 6.08344 14.25 5.79635 14.25 5.5ZM17.5 3.75C16.5335 3.75 15.75 4.5335 15.75 5.5C15.75 6.4665 16.5335 7.25 17.5 7.25C18.4665 7.25 19.25 6.4665 19.25 5.5C19.25 4.5335 18.4665 3.75 17.5 3.75ZM6.5 10.75C5.5335 10.75 4.75 11.5335 4.75 12.5C4.75 13.4665 5.5335 14.25 6.5 14.25C7.4665 14.25 8.25 13.4665 8.25 12.5C8.25 11.5335 7.4665 10.75 6.5 10.75ZM15.75 18.5C15.75 17.5335 16.5335 16.75 17.5 16.75C18.4665 16.75 19.25 17.5335 19.25 18.5C19.25 19.4665 18.4665 20.25 17.5 20.25C16.5335 20.25 15.75 19.4665 15.75 18.5Z"
        fill="currentColor"
      />
    </svg>
  )
}

export function SortAscIcon({ size = 12 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M12 5 L20 19 L4 19 Z" />
    </svg>
  )
}

export function SortDescIcon({ size = 12 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M12 19 L4 5 L20 5 Z" />
    </svg>
  )
}

export function SparkleIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <path d="M12 2 L13.5 10.5 L22 12 L13.5 13.5 L12 22 L10.5 13.5 L2 12 L10.5 10.5 Z" />
    </svg>
  )
}

export function GridViewIcon({ size = 20 }: IconProps): React.JSX.Element {
  return (
    <svg width={size} height={size} {...FILL_PROPS}>
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
    </svg>
  )
}
