import React from 'react'
import { useStore } from '../store'

export function ArtistPanel(): React.JSX.Element {
  const artists = useStore((s) => s.library.artists)
  const selected = useStore((s) => s.library.selectedArtist)
  const selectArtist = useStore((s) => s.selectArtist)
  const configuredLibraryPath = useStore((s) => s.configuredLibraryPath)
  const setLibraryPath = useStore((s) => s.setLibraryPath)

  async function handleChangeLibrary(): Promise<void> {
    const dir = await window.api.openDirectory()
    if (dir === null) return
    await setLibraryPath(dir)
  }

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
        <button className="panel-library-change-btn" onClick={handleChangeLibrary}>
          Change Library…
        </button>
      </div>
    </aside>
  )
}
