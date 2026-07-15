export function revealInFinderLabel(): string {
  const p = window.electron.process.platform
  if (p === 'darwin') return '↗ Reveal in Finder'
  if (p === 'win32') return '↗ Show in Explorer'
  return '↗ Show in Files'
}

/** The platform's file-manager name: Finder (macOS), Explorer (Windows), Files. */
export function fileManagerName(): string {
  const p = window.electron.process.platform
  if (p === 'darwin') return 'Finder'
  if (p === 'win32') return 'Explorer'
  return 'Files'
}
