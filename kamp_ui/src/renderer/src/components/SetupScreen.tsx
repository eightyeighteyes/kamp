import React, { useState } from 'react'
import { useStore } from '../store'

export function SetupScreen(): React.JSX.Element {
  const scanStatus = useStore((s) => s.scanStatus)
  const lastScanResult = useStore((s) => s.lastScanResult)
  const scanError = useStore((s) => s.scanError)
  const configuredLibraryPath = useStore((s) => s.configuredLibraryPath)
  const setLibraryPath = useStore((s) => s.setLibraryPath)
  const scanLibrary = useStore((s) => s.scanLibrary)
  const scanProgress = useStore((s) => s.scanProgress)

  const [pickError, setPickError] = useState<string | null>(null)

  async function handleChoose(): Promise<void> {
    setPickError(null)
    const dir = await window.api.openDirectory()
    if (dir === null) return // user cancelled
    try {
      await setLibraryPath(dir)
    } catch {
      setPickError('Could not set library path. Check the server logs.')
    }
  }

  return (
    <div className="setup-screen">
      <div className="setup-icon">♫</div>
      <div className="setup-title">Your library is empty</div>

      {scanStatus === 'idle' && (
        <>
          {configuredLibraryPath ? (
            <div className="setup-path">
              {configuredLibraryPath}
              <button className="setup-change-btn" onClick={handleChoose}>
                Change…
              </button>
            </div>
          ) : (
            <button className="setup-scan-btn" onClick={handleChoose}>
              Choose Library Folder
            </button>
          )}
          {pickError && <div className="setup-error">{pickError}</div>}
          {configuredLibraryPath && (
            <button className="setup-scan-btn" onClick={scanLibrary}>
              Scan Library
            </button>
          )}
        </>
      )}

      {scanStatus === 'scanning' && (
        <div className="setup-scanning">
          {scanProgress && scanProgress.total > 0 ? (
            <>
              <div className="setup-progress-bar">
                <div
                  className="setup-progress-fill"
                  style={{ width: `${(scanProgress.current / scanProgress.total) * 100}%` }}
                />
              </div>
              <div className="setup-progress-label">
                {scanProgress.current} / {scanProgress.total}
              </div>
            </>
          ) : (
            <>
              <span className="setup-spinner" />
              Scanning…
            </>
          )}
        </div>
      )}

      {scanStatus === 'done' && lastScanResult && (
        <div className="setup-result">
          Added {lastScanResult.added} track
          {lastScanResult.added !== 1 ? 's' : ''}
          {lastScanResult.removed > 0 ? `, removed ${lastScanResult.removed}` : ''}.
        </div>
      )}

      {scanStatus === 'error' && scanError && (
        <>
          <div className="setup-error">{scanError}</div>
          <button className="setup-scan-btn" onClick={scanLibrary}>
            Retry
          </button>
        </>
      )}
    </div>
  )
}
