// Format a byte count as a compact human-readable size, e.g. 85180416 → "85.2 MB".
// Uses decimal units (1 MB = 1_000_000 bytes) to match Bandcamp's own `size_mb`
// figures (KAMP-563), so an estimate and its label agree. Returns "" for a null
// size (unknown), which the caller can omit; the caller adds a "~" prefix when the
// size is an estimate (KAMP-564 size_is_estimate).
export function formatBytes(n: number | null | undefined): string {
  if (n == null || n <= 0) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let unit = 0
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000
    unit += 1
  }
  // Whole bytes/KB read cleaner without a decimal; MB+ keep one decimal place.
  const digits = unit === 0 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}
