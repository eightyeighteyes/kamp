// Structural equality for values that came out of JSON.parse (KAMP-684).
//
// Exists so the store can hand back the PREVIOUS object when a server payload
// is field-equal to it. Every parse mints fresh objects, so without this any
// component subscribing to a slice of that payload re-renders on every message
// even when nothing moved. Comparing once here lets Object.is short-circuit
// every subscriber, rather than each of them selecting defensively.
//
// Deliberately not a general deep-equal: the inputs are parsed JSON, so there
// are no Dates, Maps, Sets, functions or cycles to handle and pretending
// otherwise would be untestable dead code. It compares by key rather than
// against a hand-written field list so a field added to a payload is covered
// automatically instead of silently going stale.
export function jsonEqual(a: unknown, b: unknown): boolean {
  // Covers primitives, identical references, and null === null.
  if (a === b) return true
  if (a === null || b === null) return false
  if (typeof a !== 'object' || typeof b !== 'object') return false

  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false
    if (a.length !== b.length) return false
    return a.every((item, i) => jsonEqual(item, b[i]))
  }

  const aRecord = a as Record<string, unknown>
  const bRecord = b as Record<string, unknown>
  const aKeys = Object.keys(aRecord)
  if (aKeys.length !== Object.keys(bRecord).length) return false
  return aKeys.every(
    (key) =>
      Object.prototype.hasOwnProperty.call(bRecord, key) && jsonEqual(aRecord[key], bRecord[key])
  )
}
