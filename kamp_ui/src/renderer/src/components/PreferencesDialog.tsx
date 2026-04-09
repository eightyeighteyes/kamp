import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import type { ExtensionInfo, ExtensionSettingSchema } from '../../../shared/kampAPI'
import type { ExtensionStateHook } from '../hooks/useExtensionState'
import { useExtensionInstall } from '../hooks/useExtensionInstall'

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
// Extensions panel sub-components
// ---------------------------------------------------------------------------

function ExtensionToggle({
  enabled,
  onChange
}: {
  enabled: boolean
  onChange: (v: boolean) => void
}): React.JSX.Element {
  return (
    <label className="prefs-toggle" aria-label={enabled ? 'Disable extension' : 'Enable extension'}>
      <input type="checkbox" checked={enabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="prefs-toggle-track" />
    </label>
  )
}

function ExtensionSettingField({
  ext,
  field,
  extState
}: {
  ext: ExtensionInfo
  field: ExtensionSettingSchema
  extState: ExtensionStateHook
}): React.JSX.Element {
  const rawDefault = field.default !== undefined ? String(field.default) : ''
  const stored = extState.getSettingValue(ext.id, field.key)
  const initialValue = stored !== undefined ? String(stored) : rawDefault

  const onSave = useCallback(
    async (key: string, value: string): Promise<void> => {
      const parsed: unknown = field.type === 'number' ? Number(value) : value
      extState.setSettingValue(ext.id, key, parsed)
    },
    [ext.id, field.type, extState]
  )

  if (field.type === 'boolean') {
    const checked = stored !== undefined ? Boolean(stored) : Boolean(field.default)
    return (
      <div className="prefs-row">
        <div className="prefs-row-header">
          <span className="prefs-label">{field.label}</span>
          <ExtensionToggle
            enabled={checked}
            onChange={(v) => extState.setSettingValue(ext.id, field.key, v)}
          />
        </div>
        {field.hint && <p className="prefs-hint">{field.hint}</p>}
      </div>
    )
  }

  if (field.type === 'select' && field.options && field.options.length > 0) {
    return (
      <SelectRow
        label={field.label}
        configKey={field.key}
        options={field.options}
        initialValue={initialValue || field.options[0]}
        onSave={onSave}
      />
    )
  }

  return (
    <InputRow
      label={field.label}
      configKey={field.key}
      type={field.type === 'number' ? 'number' : 'text'}
      hint={field.hint}
      initialValue={initialValue}
      onSave={onSave}
    />
  )
}

// ---------------------------------------------------------------------------
// InstallForm
// ---------------------------------------------------------------------------

function InstallForm({ onInstalled }: { onInstalled: (id: string) => void }): React.JSX.Element {
  const { npmStatus, localStatus, installByName, installByPath } = useExtensionInstall()
  const [npmInput, setNpmInput] = useState('')
  const [localPath, setLocalPath] = useState<string | null>(null)

  const npmBusy = npmStatus.status === 'installing'
  const localBusy = localStatus.status === 'installing'

  const handleNpmInstall = useCallback(() => {
    void installByName(npmInput, (id) => {
      setNpmInput('')
      onInstalled(id)
    })
  }, [npmInput, installByName, onInstalled])

  const handleChooseDir = useCallback(async () => {
    const chosen = await window.api.openDirectory()
    if (!chosen) return
    setLocalPath(chosen)
    void installByPath(chosen, (id) => {
      setLocalPath(null)
      onInstalled(id)
    })
  }, [installByPath, onInstalled])

  return (
    <div className="prefs-section">
      <div className="prefs-section-label">Add Extension</div>

      {/* npm package name */}
      <div className="prefs-row">
        <div className="prefs-row-header">
          <span className="prefs-label">Package name</span>
        </div>
        <div className="prefs-ext-install-row">
          <input
            className="prefs-input"
            type="text"
            placeholder="kamp-ext-..."
            value={npmInput}
            onChange={(e) => {
              setNpmInput(e.target.value)
              // Clear stale error when the user edits the field.
              if (npmStatus.status === 'error') setNpmInput(e.target.value)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleNpmInstall()
            }}
            disabled={npmBusy}
          />
          <button
            className="prefs-choose-btn"
            disabled={npmBusy || !npmInput.trim()}
            onClick={handleNpmInstall}
          >
            {npmBusy ? 'Installing…' : 'Install'}
          </button>
        </div>
        {npmStatus.status === 'installing' && <div className="prefs-ext-install-progress" />}
        {npmStatus.status === 'error' && (
          <p className="prefs-hint" style={{ color: 'var(--error)' }}>
            {npmStatus.message}
          </p>
        )}
        {npmStatus.status === 'idle' && (
          <p className="prefs-hint">Installs from the npm registry.</p>
        )}
      </div>

      {/* Divider */}
      <div className="prefs-ext-install-or">or</div>

      {/* Local directory */}
      <div className="prefs-row">
        <div className="prefs-row-header">
          <span className="prefs-label">Local path</span>
        </div>
        <div className="prefs-path-display">
          <span className="prefs-path-text">
            {localPath ?? <span style={{ opacity: 0.4 }}>(not set)</span>}
          </span>
          <button
            className="prefs-choose-btn"
            onClick={() => void handleChooseDir()}
            disabled={localBusy}
          >
            {localBusy ? 'Installing…' : 'Choose…'}
          </button>
        </div>
        {localStatus.status === 'installing' && <div className="prefs-ext-install-progress" />}
        {localStatus.status === 'error' && (
          <p className="prefs-hint" style={{ color: 'var(--error)' }}>
            {localStatus.message}
          </p>
        )}
        {localStatus.status === 'idle' && (
          <p className="prefs-hint">Choose a directory containing a package.json.</p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ExtensionRow (with uninstall for community extensions)
// ---------------------------------------------------------------------------

function ExtensionRow({
  ext,
  extState,
  onReviewDenied,
  onUninstall,
  highlight
}: {
  ext: ExtensionInfo
  extState: ExtensionStateHook
  onReviewDenied: (id: string) => void
  onUninstall: (id: string) => void
  highlight: boolean
}): React.JSX.Element {
  // A Phase 2 extension that was denied is not running — treat toggle as off.
  const isDenied = ext.phase === 2 && extState.deniedIds.has(ext.id)
  const enabled = !isDenied && !extState.disabledIds.has(ext.id)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const hasSettings = ext.settings && ext.settings.length > 0

  // Close confirmation on Escape.
  useEffect(() => {
    if (!confirmingRemove) return
    const handler = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setConfirmingRemove(false)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [confirmingRemove])

  if (confirmingRemove) {
    return (
      <div className="prefs-row prefs-ext-remove-confirm">
        <span className="prefs-label">Remove {ext.name}?</span>
        <p className="prefs-hint">The extension will be unloaded and deleted.</p>
        <div className="prefs-ext-meta">
          <button
            className="prefs-choose-btn prefs-choose-btn--destructive"
            onClick={() => onUninstall(ext.id)}
          >
            Remove
          </button>
          <button className="prefs-ext-configure-btn" onClick={() => setConfirmingRemove(false)}>
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`prefs-row${highlight ? ' prefs-ext-highlight' : ''}`}>
      <div className="prefs-row-header">
        <span className="prefs-label">{ext.name}</span>
        <div className="prefs-ext-meta">
          <span className="prefs-ext-version">{ext.version}</span>
          <span
            className={`prefs-ext-badge${ext.phase === 1 ? ' prefs-ext-badge--first-party' : ''}`}
          >
            {ext.phase === 1 ? 'First-party' : 'Community'}
          </span>
          {/* Denied: offer a way to re-review permissions */}
          {isDenied && (
            <button className="prefs-ext-configure-btn" onClick={() => onReviewDenied(ext.id)}>
              Review permissions...
            </button>
          )}
          {/* Disabled (but not denied): signal reload is needed */}
          {!isDenied && !enabled && <span className="prefs-restart-badge">↺ reload</span>}
          {!isDenied && hasSettings && (
            <button
              className="prefs-ext-configure-btn"
              onClick={() => setDrawerOpen((o) => !o)}
              aria-expanded={drawerOpen}
            >
              {drawerOpen ? 'Done' : 'Configure...'}
            </button>
          )}
          {/* User-installed community extensions can be removed; bundled ones cannot. */}
          {ext.phase === 2 && ext.installedFrom === 'npm' && (
            <button className="prefs-ext-configure-btn" onClick={() => setConfirmingRemove(true)}>
              Remove
            </button>
          )}
        </div>
        <ExtensionToggle enabled={enabled} onChange={() => extState.toggleEnabled(ext.id)} />
      </div>

      {drawerOpen && hasSettings && (
        <div className="prefs-ext-drawer">
          {ext.settings!.map((field) => (
            <ExtensionSettingField key={field.key} ext={ext} field={field} extState={extState} />
          ))}
        </div>
      )}
    </div>
  )
}

function ExtensionsPanel({
  extensions,
  extState,
  onReviewDenied,
  onInstalled,
  onUninstall,
  highlightExtId
}: {
  extensions: ExtensionInfo[]
  extState: ExtensionStateHook
  onReviewDenied: (id: string) => void
  onInstalled: (id: string) => void
  onUninstall: (id: string) => void
  highlightExtId: string | null
}): React.JSX.Element {
  return (
    <>
      {extensions.length === 0 ? (
        <div className="prefs-section">
          <p className="prefs-ext-empty">No extensions installed.</p>
        </div>
      ) : (
        <div className="prefs-section">
          <div className="prefs-section-label">Installed</div>
          {extensions.map((ext) => (
            <ExtensionRow
              key={ext.id}
              ext={ext}
              extState={extState}
              onReviewDenied={onReviewDenied}
              onUninstall={onUninstall}
              highlight={ext.id === highlightExtId}
            />
          ))}
        </div>
      )}
      <InstallForm onInstalled={onInstalled} />
    </>
  )
}

// ---------------------------------------------------------------------------
// Main dialog
// ---------------------------------------------------------------------------

export function PreferencesDialog({
  extensions,
  extState,
  onReviewDenied,
  onInstalled,
  onUninstalled
}: {
  extensions: ExtensionInfo[]
  extState: ExtensionStateHook
  onReviewDenied: (id: string) => void
  onInstalled: (id: string) => void
  onUninstalled: () => void
}): React.JSX.Element | null {
  const prefsOpen = useStore((s) => s.prefsOpen)
  const closePrefs = useStore((s) => s.closePrefs)
  const loadConfig = useStore((s) => s.loadConfig)
  const configValues = useStore((s) => s.configValues)
  const setConfigValue = useStore((s) => s.setConfigValue)
  const scanLibrary = useStore((s) => s.scanLibrary)
  const scanStatus = useStore((s) => s.scanStatus)
  const scanProgress = useStore((s) => s.scanProgress)

  const [activeTab, setActiveTab] = useState<'general' | 'extensions'>('general')
  const [highlightExtId, setHighlightExtId] = useState<string | null>(null)
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const handleInstalled = useCallback(
    (id: string) => {
      onInstalled(id)
      setActiveTab('extensions')
      clearTimeout(highlightTimerRef.current)
      setHighlightExtId(id)
      // Clear after animation completes (5s fade + small buffer).
      highlightTimerRef.current = setTimeout(() => setHighlightExtId(null), 5500)
    },
    [onInstalled]
  )

  useEffect(() => () => clearTimeout(highlightTimerRef.current), [])

  const { uninstall } = useExtensionInstall()
  const handleUninstall = useCallback(
    (id: string) => {
      void uninstall(id, () => {
        extState.clearExtensionState(id)
        onUninstalled()
      })
    },
    [uninstall, extState, onUninstalled]
  )

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

  // Show a loading placeholder while config is being fetched (General tab only).
  const generalLoading = configValues === null && activeTab === 'general'

  const hasBandcamp = configValues !== null && configValues['bandcamp.username'] !== null

  const str = (key: keyof NonNullable<typeof configValues>): string => {
    if (!configValues) return ''
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

        {/* Tab bar */}
        <div className="prefs-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={activeTab === 'general'}
            className={`prefs-tab${activeTab === 'general' ? ' prefs-tab--active' : ''}`}
            onClick={() => setActiveTab('general')}
          >
            General
          </button>
          <button
            role="tab"
            aria-selected={activeTab === 'extensions'}
            className={`prefs-tab${activeTab === 'extensions' ? ' prefs-tab--active' : ''}`}
            onClick={() => setActiveTab('extensions')}
          >
            Extensions
          </button>
        </div>

        {/* Scrollable body — content swaps per tab */}
        <div className="prefs-body" role="tabpanel">
          {activeTab === 'general' && (
            <>
              {generalLoading ? null : (
                <>
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
                </>
              )}
            </>
          )}

          {activeTab === 'extensions' && (
            <ExtensionsPanel
              extensions={extensions}
              extState={extState}
              onReviewDenied={onReviewDenied}
              onInstalled={handleInstalled}
              onUninstall={handleUninstall}
              highlightExtId={highlightExtId}
            />
          )}
        </div>
      </div>
    </div>
  )
}
