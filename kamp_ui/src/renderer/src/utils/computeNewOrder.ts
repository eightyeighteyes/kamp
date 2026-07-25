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
  // Count surviving items that sit before the drop target's original index. This stays
  // correct when the dragged items precede the target (those removals shift the array left,
  // which a raw `dropIdx` would ignore) and naturally caps at unselected.length for tail-drop
  // (dropIdx === trackCount).
  const insertPos = unselected.filter((i) => i < dropIdx).length
  return [...unselected.slice(0, insertPos), ...selectedIdxs, ...unselected.slice(insertPos)]
}
