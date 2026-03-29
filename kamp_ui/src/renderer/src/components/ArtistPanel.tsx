import React from 'react'
import { useStore } from '../store'

export function ArtistPanel(): React.JSX.Element {
  const artists = useStore((s) => s.library.artists)
  const selected = useStore((s) => s.library.selectedArtist)
  const selectArtist = useStore((s) => s.selectArtist)
  const configuredLibraryPath = useStore((s) => s.configuredLibraryPath)
  const setLibraryPath = useStore((s) => s.setLibraryPath)
  const scanLibrary = useStore((s) => s.scanLibrary)
  const scanStatus = useStore((s) => s.scanStatus)
  const scanProgress = useStore((s) => s.scanProgress)

  async function handleChangeLibrary(): Promise<void> {
    const dir = await window.api.openDirectory()
    if (dir === null) return
    await setLibraryPath(dir)
  }

  const scanning = scanStatus === 'scanning'
  const progressPct =
    scanProgress && scanProgress.total > 0
      ? (scanProgress.current / scanProgress.total) * 100
      : null

  return (
    <aside className="artist-panel">
      <div className="panel-header">Artists</div>
      <ul className="artist-list">
        <li
          className={selected === null ? 'active' : ''}
          tabIndex={0}
          onClick={() => selectArtist(null)}
          onKeyDown={(e) => e.key === 'Enter' && selectArtist(null)}
        >
          All Artists
        </li>
        {artists.map((artist) => (
          <li
            key={artist}
            className={selected === artist ? 'active' : ''}
            tabIndex={0}
            onClick={() => selectArtist(artist)}
            onKeyDown={(e) => e.key === 'Enter' && selectArtist(artist)}
          >
            {artist}
          </li>
        ))}
      </ul>
      <div className="panel-library-footer">
        <span className="panel-library-path" title={configuredLibraryPath ?? undefined}>
          {configuredLibraryPath ?? '—'}
        </span>
        {/* TODO: move re-scan to preferences panel once DRAFT-8 ships */}
        {scanning ? (
          <>
            <span className="panel-rescan-scanning">Scanning…</span>
            {progressPct !== null && (
              <div className="panel-rescan-progress">
                <div className="panel-rescan-progress-fill" style={{ width: `${progressPct}%` }} />
              </div>
            )}
          </>
        ) : (
          <button className="panel-rescan-btn" onClick={scanLibrary}>
            Re-scan Library
          </button>
        )}
        <button className="panel-library-change-btn" onClick={handleChangeLibrary}>
          Change Library…
        </button>
      </div>
    </aside>
  )
}
