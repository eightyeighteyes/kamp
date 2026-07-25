/** Truncate *title* to at most *maxLen* characters, appending "…" if cut. */
export const truncateTitle = (title: string, maxLen = 100): string =>
  title.length > maxLen ? title.slice(0, maxLen - 1) + '…' : title
