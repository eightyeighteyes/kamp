import React from 'react'
import { useStore } from '../store'

export function SetupScreen(): React.JSX.Element {
  const scanLibrary = useStore((s) => s.scanLibrary)
  const scanStatus = useStore((s) => s.scanStatus)
  const lastScanResult = useStore((s) => s.lastScanResult)
  const scanError = useStore((s) => s.scanError)

  return (
    <div className="setup-screen">
      <div className="setup-icon">♫</div>
      <div className="setup-title">Your library is empty</div>

      {scanStatus === 'idle' && (
        <>
          <div className="setup-hint">
            Point <code>paths.library</code> at your music folder in{' '}
            <code>~/.config/kamp/config.toml</code>, then scan to index it.
          </div>
          <button className="setup-scan-btn" onClick={scanLibrary}>
            Scan Library
          </button>
        </>
      )}

      {scanStatus === 'scanning' && (
        <div className="setup-scanning">
          <span className="setup-spinner" />
          Scanning…
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
