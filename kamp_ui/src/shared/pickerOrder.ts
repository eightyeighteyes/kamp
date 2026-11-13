/**
 * KAMP-770: the display order shared by the Style rail's pickers (Now Playing
 * Animation, New Arrival Highlight). The "off" entry is pinned first; the rest are
 * alphabetical by label. The locale is explicit because the default follows the
 * runtime's, and some (sv, fi) collate ä after z, so node --test and Electron
 * could disagree. Returns a new array; `ids` is left untouched.
 */
export function offFirstAlphabetical<T extends string>(
  ids: readonly T[],
  offId: T,
  labelOf: Readonly<Record<T, string>>
): T[] {
  const rest = ids.filter((id) => id !== offId)
  rest.sort((a, b) => labelOf[a].localeCompare(labelOf[b], 'en'))
  return [offId, ...rest]
}
