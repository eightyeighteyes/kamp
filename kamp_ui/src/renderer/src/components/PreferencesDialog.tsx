import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

// Keys whose values must be integers — sent as strings over the wire but
// stored as numbers in the config.
const INT_KEYS = new Set([
  'artwork.min_dimension',
  'artwork.max_bytes',
  'bandcamp.poll_interval_minutes'
])

// Keys that require a server restart to take effect.
const RESTART_KEYS = new Set([
  'paths.staging',
  'paths.library',
  'musicbrainz.contact',
  'bandcamp.poll_interval_minutes'
])

const BANDCAMP_FORMATS = ['mp3-v0', 'mp3-320', 'flac', 'aac-hi', 'vorbis', 'alac', 'wav']

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RestartBadge(): React.JSX.Element {
  return <span className="prefs-restart-badge">↺ restart</span>
}

function SavedCheck({ visible }: { visible: boolean }): React.JSX.Element {
  return (
    <span className={`prefs-saved-check${visible ? ' prefs-saved-check--visible' : ''}`}>✓</span>
  )
}

// A single text/email/number input row.
// Uses an uncontrolled input (defaultValue + ref) to avoid sync effects.
// The `key` prop on the input remounts it whenever the store value changes,
// which ensures the field always reflects the latest persisted value.
function InputRow({
  label,
  configKey,
  type = 'text',
  unit,
  hint,
  initialValue,
  onSave
}: {
  label: string
  configKey: string
  type?: 'text' | 'email' | 'number'
  unit?: string
  hint?: string
  initialValue: string
  onSave: (key: string, value: string) => Promise<void>
}): React.JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const handleBlur = useCallback(async () => {
    const value = inputRef.current?.value ?? ''
    if (value === initialValue) return
    setError(null)
    try {
      await onSave(configKey, value)
      setSaved(true)
      clearTimeout(savedTimerRef.current)
      savedTimerRef.current = setTimeout(() => setSaved(false), 1500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }, [initialValue, configKey, onSave])

  const isNumber = type === 'number'

  return (
    <div className="prefs-row">
      <div className="prefs-row-header">
        <span className="prefs-label">{label}</span>
        {RESTART_KEYS.has(configKey) && <RestartBadge />}
        <SavedCheck visible={saved} />
      </div>
      <div className={isNumber ? 'prefs-number-row' : undefined}>
        <input
          // key remounts the input whenever the persisted value changes, so
          // the field always reflects what the server has confirmed.
          key={initialValue}
          ref={inputRef}
          className={`prefs-input${isNumber ? ' prefs-input--number' : ''}`}
          type={type}
          min={isNumber ? 0 : undefined}
          defaultValue={initialValue}
          onBlur={handleBlur}
        />
        {unit && <span className="prefs-unit">{unit}</span>}
      </div>
      {hint && <p className="prefs-hint">{hint}</p>}
      {error && (
        <p className="prefs-hint" style={{ color: 'var(--error)' }}>
          {error}
        </p>
      )}
    </div>
  )
}

// A folder-picker row (read-only display + Choose... button).
function PathRow({
  label,
  configKey,
  initialValue,
  onSave
}: {
  label: string
  configKey: string
  initialValue: string
  onSave: (key: string, value: string) => Promise<void>
}): React.JSX.Element {
  const [displayPath, setDisplayPath] = useState(initialValue)
  const [flashSaved, setFlashSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const handleChoose = useCallback(async () => {
    const chosen = await window.api.openDirectory()
    if (!chosen) return
    setError(null)
    try {
      await onSave(configKey, chosen)
      setDisplayPath(chosen)
      setFlashSaved(true)
      clearTimeout(flashTimerRef.current)
      flashTimerRef.current = setTimeout(() => setFlashSaved(false), 600)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }, [configKey, onSave])

  return (
    <div className="prefs-row">
      <div className="prefs-row-header">
        <span className="prefs-label">{label}</span>
        <RestartBadge />
      </div>
      <div className="prefs-path-display">
        <span className={`prefs-path-text${flashSaved ? ' prefs-path-text--saved' : ''}`}>
          {displayPath || '(not set)'}
        </span>
        <button className="prefs-choose-btn" onClick={handleChoose}>
          Choose...
        </button>
      </div>
      {error && (
        <p className="prefs-hint" style={{ color: 'var(--error)' }}>
          {error}
        </p>
      )}
    </div>
  )
}

// A select (dropdown) row.
function SelectRow({
  label,
  configKey,
  options,
  initialValue,
  onSave
}: {
  label: string
  configKey: string
  options: string[]
  initialValue: string
  onSave: (key: string, value: string) => Promise<void>
}): React.JSX.Element {
  const [localValue, setLocalValue] = useState(initialValue || options[0])
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const handleChange = useCallback(
    async (e: React.ChangeEvent<HTMLSelectElement>) => {
      const value = e.target.value
      setLocalValue(value)
      setError(null)
      try {
        await onSave(configKey, value)
        setSaved(true)
        clearTimeout(savedTimerRef.current)
        savedTimerRef.current = setTimeout(() => setSaved(false), 1500)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Save failed')
      }
    },
    [configKey, onSave]
  )

  return (
    <div className="prefs-row">
      <div className="prefs-row-header">
        <span className="prefs-label">{label}</span>
        {RESTART_KEYS.has(configKey) && <RestartBadge />}
        <SavedCheck visible={saved} />
      </div>
      <select className="prefs-select" value={localValue} onChange={handleChange}>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      {error && (
        <p className="prefs-hint" style={{ color: 'var(--error)' }}>
          {error}
        </p>
      )}
    </div>
  )
}

// A textarea row (used for path_template).
function TextareaRow({
  label,
  configKey,
  hint,
  initialValue,
  onSave
}: {
  label: string
  configKey: string
  hint?: string
  initialValue: string
  onSave: (key: string, value: string) => Promise<void>
}): React.JSX.Element {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const handleBlur = useCallback(async () => {
    const value = textareaRef.current?.value ?? ''
    if (value === initialValue) return
    setError(null)
    try {
      await onSave(configKey, value)
      setSaved(true)
      clearTimeout(savedTimerRef.current)
      savedTimerRef.current = setTimeout(() => setSaved(false), 1500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    }
  }, [initialValue, configKey, onSave])

  return (
    <div className="prefs-row">
      <div className="prefs-row-header">
        <span className="prefs-label">{label}</span>
        <SavedCheck visible={saved} />
      </div>
      <textarea
        key={initialValue}
        ref={textareaRef}
        className="prefs-textarea"
        rows={2}
        defaultValue={initialValue}
        onBlur={handleBlur}
      />
      {hint && <p className="prefs-hint">{hint}</p>}
      {error && (
        <p className="prefs-hint" style={{ color: 'var(--error)' }}>
          {error}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main dialog
// ---------------------------------------------------------------------------

export function PreferencesDialog(): React.JSX.Element | null {
  const prefsOpen = useStore((s) => s.prefsOpen)
  const closePrefs = useStore((s) => s.closePrefs)
  const loadConfig = useStore((s) => s.loadConfig)
  const configValues = useStore((s) => s.configValues)
  const setConfigValue = useStore((s) => s.setConfigValue)
  const scanLibrary = useStore((s) => s.scanLibrary)
  const scanStatus = useStore((s) => s.scanStatus)
  const scanProgress = useStore((s) => s.scanProgress)

  // Load config the first time the dialog opens.
  useEffect(() => {
    if (prefsOpen && configValues === null) {
      void loadConfig()
    }
  }, [prefsOpen, configValues, loadConfig])

  // Close on Escape.
  useEffect(() => {
    if (!prefsOpen) return
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') closePrefs()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [prefsOpen, closePrefs])

  if (!prefsOpen) return null

  // Show a loading placeholder while config is being fetched.
  if (configValues === null) {
    return (
      <div className="prefs-overlay">
        <div className="prefs-dialog">
          <div className="prefs-header">
            <span className="prefs-title">PREFERENCES</span>
            <button className="prefs-close-btn" onClick={closePrefs} aria-label="Close preferences">
              ✕
            </button>
          </div>
          <div className="prefs-body" />
        </div>
      </div>
    )
  }

  const hasBandcamp = configValues['bandcamp.username'] !== null

  const str = (key: keyof typeof configValues): string => {
    const v = configValues[key]
    return v != null ? String(v) : ''
  }

  const handleSave = async (key: string, value: string): Promise<void> => {
    if (INT_KEYS.has(key)) {
      const n = Number(value)
      if (value.trim() === '' || !Number.isInteger(n) || n < 0)
        throw new Error('Enter a whole number (0 or greater).')
    }
    if (key === 'musicbrainz.contact') {
      if (!value.includes('@') || value.trim() === '')
        throw new Error('Enter a valid email address.')
    }
    await setConfigValue(key, value)
  }

  return (
    <div className="prefs-overlay" onClick={(e) => e.target === e.currentTarget && closePrefs()}>
      <div className="prefs-dialog" role="dialog" aria-modal="true" aria-label="Preferences">
        {/* Title bar */}
        <div className="prefs-header">
          <span className="prefs-title">PREFERENCES</span>
          <button className="prefs-close-btn" onClick={closePrefs} aria-label="Close preferences">
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div className="prefs-body">
          {/* PATHS */}
          <div className="prefs-section">
            <div className="prefs-section-label">Paths</div>
            <PathRow
              label="Library folder"
              configKey="paths.library"
              initialValue={str('paths.library')}
              onSave={handleSave}
            />
            <PathRow
              label="Staging folder"
              configKey="paths.staging"
              initialValue={str('paths.staging')}
              onSave={handleSave}
            />
            <div className="prefs-row">
              <div className="prefs-row-header">
                <span className="prefs-label">Library index</span>
              </div>
              {scanStatus === 'scanning' ? (
                <div className="prefs-scan-status">
                  <span className="prefs-scan-scanning">Scanning…</span>
                  {scanProgress && scanProgress.total > 0 && (
                    <div className="prefs-scan-progress">
                      <div
                        className="prefs-scan-progress-fill"
                        style={{
                          width: `${(scanProgress.current / scanProgress.total) * 100}%`
                        }}
                      />
                    </div>
                  )}
                </div>
              ) : (
                <button className="prefs-choose-btn" onClick={() => void scanLibrary()}>
                  Re-scan Library
                </button>
              )}
              {scanStatus === 'done' && <p className="prefs-hint">Scan complete.</p>}
              {scanStatus === 'error' && (
                <p className="prefs-hint" style={{ color: 'var(--error)' }}>
                  Scan failed — check server logs.
                </p>
              )}
            </div>
          </div>

          {/* LIBRARY */}
          <div className="prefs-section">
            <div className="prefs-section-label">Library</div>
            <TextareaRow
              label="Path template"
              configKey="library.path_template"
              initialValue={str('library.path_template')}
              hint="{album_artist}  {year}  {album}  {track}  {title}  {ext}"
              onSave={handleSave}
            />
          </div>

          {/* ARTWORK */}
          <div className="prefs-section">
            <div className="prefs-section-label">Artwork</div>
            <InputRow
              label="Minimum dimension"
              configKey="artwork.min_dimension"
              type="number"
              unit="px"
              initialValue={str('artwork.min_dimension')}
              onSave={handleSave}
            />
            <InputRow
              label="Maximum file size"
              configKey="artwork.max_bytes"
              type="number"
              unit="bytes"
              initialValue={str('artwork.max_bytes')}
              onSave={handleSave}
            />
          </div>

          {/* MUSICBRAINZ */}
          <div className="prefs-section">
            <div className="prefs-section-label">MusicBrainz</div>
            <InputRow
              label="Contact email"
              configKey="musicbrainz.contact"
              type="email"
              initialValue={str('musicbrainz.contact')}
              hint="Sent in MusicBrainz User-Agent; required by their policy."
              onSave={handleSave}
            />
          </div>

          {/* BANDCAMP — only when the section is configured */}
          {hasBandcamp && (
            <div className="prefs-section">
              <div className="prefs-section-label">Bandcamp</div>
              <InputRow
                label="Username"
                configKey="bandcamp.username"
                initialValue={str('bandcamp.username')}
                onSave={handleSave}
              />
              <SelectRow
                label="Download format"
                configKey="bandcamp.format"
                options={BANDCAMP_FORMATS}
                initialValue={str('bandcamp.format')}
                onSave={handleSave}
              />
              <InputRow
                label="Poll interval"
                configKey="bandcamp.poll_interval_minutes"
                type="number"
                unit="min"
                initialValue={str('bandcamp.poll_interval_minutes')}
                hint="0 = manual only"
                onSave={handleSave}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
