export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// Formats aggregated durations (playlist total, album total, stats) as a compact
// multi-unit string using three consecutive tiers from the leading non-zero unit:
//   hours  → h/m/s  e.g. "1h4m30s"
//   days   → d/h/m  e.g. "5d12h30m"
//   months → mo/d/h e.g. "3mo5d12h"
//   years  → y/mo/d e.g. "2y3mo15d"
// Interior zeros within the tier are omitted.
export function formatLongDuration(seconds: number): string {
  if (seconds <= 0) return '—'

  const totalSecs = Math.floor(seconds)
  const s = totalSecs % 60
  const totalMinutes = Math.floor(totalSecs / 60)
  const m = totalMinutes % 60
  const totalHours = Math.floor(totalSecs / 3600)
  const h = totalHours % 24
  const totalDays = Math.floor(totalSecs / 86400)
  const years = Math.floor(totalDays / 365)
  const months = Math.floor((totalDays % 365) / 30)
  const days = totalDays % 30

  const parts: string[] = []
  if (years > 0) {
    parts.push(`${years}y`)
    if (months > 0) parts.push(`${months}mo`)
    if (days > 0) parts.push(`${days}d`)
  } else if (months > 0) {
    parts.push(`${months}mo`)
    if (days > 0) parts.push(`${days}d`)
    if (h > 0) parts.push(`${h}h`)
  } else if (totalDays > 0) {
    parts.push(`${totalDays}d`)
    if (h > 0) parts.push(`${h}h`)
    if (m > 0) parts.push(`${m}m`)
  } else if (totalHours > 0) {
    parts.push(`${totalHours}h`)
    if (m > 0) parts.push(`${m}m`)
    if (s > 0) parts.push(`${s}s`)
  } else if (m > 0) {
    parts.push(`${m}m`)
    if (s > 0) parts.push(`${s}s`)
  } else {
    parts.push(`${s}s`)
  }

  return parts.join('')
}
