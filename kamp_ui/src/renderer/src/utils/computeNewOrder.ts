/**
 * Compute a new ordering after drag-and-drop reorder.
 *
 * @param trackCount - total number of tracks
 * @param selectedIdxs - indices of the dragged tracks (in their original positions)
 * @param dropIdx - display index of the row the drag was dropped onto
 * @returns array of original indices in the new display order
 */
export function computeNewOrder(
  trackCount: number,
  selectedIdxs: number[],
  dropIdx: number
): number[] {
  const selected = new Set(selectedIdxs)
  const unselected = Array.from({ length: trackCount }, (_, i) => i).filter((i) => !selected.has(i))
  // Math.min handles tail-drop (dropIdx === trackCount) without an off-by-one.
  const insertPos = Math.min(dropIdx, unselected.length)
  return [...unselected.slice(0, insertPos), ...selectedIdxs, ...unselected.slice(insertPos)]
}
