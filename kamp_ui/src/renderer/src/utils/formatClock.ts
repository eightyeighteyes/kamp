// m:ss for track and preview times (KAMP-651).
//
// Lives outside the component files because a module that exports components
// must export nothing else, or Fast Refresh stops working for it.
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00'
  const total = Math.floor(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}
