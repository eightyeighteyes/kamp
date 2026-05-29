import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { connectStateStream, getDeferredOps } from './api/client'
import { ArtistPanel } from './components/ArtistPanel'
import { BaseKampView } from './components/BaseKampView'
import { ExtensionPanel } from './components/ExtensionPanel'
import { LibraryView } from './components/LibraryView'
import { NowPlayingView } from './components/NowPlayingView'
import { PanelPicker } from './components/PanelPicker'
import { PreferencesDialog } from './components/PreferencesDialog'
import { QueuePanel } from './components/QueuePanel'
import { BandcampButton } from './components/BandcampButton'
import { PipelineIndicator } from './components/PipelineIndicator'
import { SearchBar } from './components/SearchBar'
import { SearchView } from './components/SearchView'
import { OnboardingScreen } from './components/OnboardingScreen'
import { SplashScreen } from './components/SplashScreen'
import { TransportBar } from './components/TransportBar'
import { SandboxedExtensionLoader } from './components/SandboxedExtensionLoader'
import { ExtensionPermissionPrompt } from './components/ExtensionPermissionPrompt'
import { UpdateBanner } from './components/UpdateBanner'
import { registerBuiltInPanel, usePanelLayout } from './hooks/usePanelLayout'
import { useExtensionState } from './hooks/useExtensionState'
import type { UnifiedPanel } from './hooks/usePanelLayout'
import type { ExtensionInfo } from '../../shared/kampAPI'

// ---------------------------------------------------------------------------
// Register built-in panels before the component mounts.
// Each call is idempotent — safe across HMR and React StrictMode re-runs.
// ---------------------------------------------------------------------------
registerBuiltInPanel({
  id: 'kamp.base-kamp',
  title: 'Base Kamp',
  defaultSlot: 'main',
  compatibleSlots: ['main'],
  component: BaseKampView
})
registerBuiltInPanel({
  id: 'kamp.library',
  title: 'Library',
  defaultSlot: 'main',
  compatibleSlots: ['main'],
  component: LibraryView
})
registerBuiltInPanel({
  id: 'kamp.now-playing',
  title: 'Now Playing',
  defaultSlot: 'main',
  compatibleSlots: ['main'],
  component: NowPlayingView
})
registerBuiltInPanel({
  id: 'kamp.artist-list',
  title: 'Artists',
  defaultSlot: 'left',
  compatibleSlots: ['left', 'right'],
  component: ArtistPanel
})
registerBuiltInPanel({
  id: 'kamp.queue',
  title: 'Queue',
  defaultSlot: 'right',
  compatibleSlots: ['left', 'right'],
  component: QueuePanel
})
registerBuiltInPanel({
  id: 'kamp.transport',
  title: 'Transport',
  defaultSlot: 'bottom',
  compatibleSlots: ['bottom'],
  component: TransportBar
})

// ---------------------------------------------------------------------------
// SlotPanel: renders a single panel regardless of whether it is a built-in
// React component or an extension DOM renderer.
// ---------------------------------------------------------------------------
function SlotPanel({ panel }: { panel: UnifiedPanel }): React.JSX.Element {
  if (panel.kind === 'builtin') {
    return <panel.component />
  }
  return <ExtensionPanel panel={panel} />
}

export default function App(): React.JSX.Element {
  const loadLibrary = useStore((s) => s.loadLibrary)
  const refreshOpenAlbum = useStore((s) => s.refreshOpenAlbum)
  const setAlbumRenameProgress = useStore((s) => s.setAlbumRenameProgress)
  const clearDeferredOp = useStore((s) => s.clearDeferredOp)
  const loadUiState = useStore((s) => s.loadUiState)
  const loadConfig = useStore((s) => s.loadConfig)
  const applyServerState = useStore((s) => s.applyServerState)
  const setServerStatus = useStore((s) => s.setServerStatus)
  const setAudioLevel = useStore((s) => s.setAudioLevel)
  const bumpLastPlayedVersion = useStore((s) => s.bumpLastPlayedVersion)
  const serverStatus = useStore((s) => s.serverStatus)
  const configuredLibraryPath = useStore((s) => s.configuredLibraryPath)
  const activeView = useStore((s) => s.activeView)
  const setActiveView = useStore((s) => s.setActiveView)
  const togglePlayPause = useStore((s) => s.togglePlayPause)
  const next = useStore((s) => s.next)
  const prev = useStore((s) => s.prev)
  const searchQuery = useStore((s) => s.searchQuery)
  const setSearchQuery = useStore((s) => s.setSearchQuery)
  const loadQueue = useStore((s) => s.loadQueue)
  const queueVisible = useStore((s) => s.queueVisible)
  const toggleQueuePanel = useStore((s) => s.toggleQueuePanel)
  const artistPanelVisible = useStore((s) => s.artistPanelVisible)
  const toggleArtistPanel = useStore((s) => s.toggleArtistPanel)
  const openPrefs = useStore((s) => s.openPrefs)
  const selectArtist = useStore((s) => s.selectArtist)
  const setUpdateAvailable = useStore((s) => s.setUpdateAvailable)

  const layout = usePanelLayout()
  const extState = useExtensionState()

  // Active extension panel id, or null when a built-in view is showing.
  const [activeExtPanel, setActiveExtPanel] = useState<string | null>(null)
  // All discovered extensions (used by PreferencesDialog for the Extensions tab).
  const [allExtensions, setAllExtensions] = useState<ExtensionInfo[]>([])
  // Phase 2 (community) extensions approved and ready to render in sandboxed iframes.
  const [phase2Extensions, setPhase2Extensions] = useState<ExtensionInfo[]>([])
  // Queue of Phase 2 extensions awaiting permission approval — shown one at a time.
  const [permissionQueue, setPermissionQueue] = useState<ExtensionInfo[]>([])
  // Incrementing counter — bump to re-run extension discovery after install/uninstall.
  const [extensionGeneration, setExtensionGeneration] = useState(0)

  const searchBarRef = useRef<HTMLInputElement>(null)
  const mainContentRef = useRef<HTMLElement>(null)

  // Per-view scroll positions — kept current by a scroll listener so we never
  // read a browser-clamped value when the outgoing view's content was taller.
  // Key: active main panel id (built-in view name or extension panel id).
  const viewScrollRef = useRef<Partial<Record<string, number>>>({})

  // Onboarding: required when no library path is configured.
  // Determined once (after the splash clears) so the check is stable.
  const [onboardingRequired, setOnboardingRequired] = useState<boolean | null>(null)
  const [onboardingComplete, setOnboardingComplete] = useState(false)
  const [onboardingTitle, setOnboardingTitle] = useState('Welcome to Kamp')
  const handleOnboardingComplete = useCallback(() => setOnboardingComplete(true), [])

  // Splash: shown while reconnecting, then lingers 1s after connect so the
  // library fetch completes before the app is revealed, then fades out.
  const [splashHiding, setSplashHiding] = useState(false)
  const [splashGone, setSplashGone] = useState(false)
  const splashLingerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const splashFadeRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => {
    if (serverStatus !== 'reconnecting') {
      splashLingerRef.current = setTimeout(() => {
        setSplashHiding(true)
        splashFadeRef.current = setTimeout(() => setSplashGone(true), 500)
      }, 1000)
    } else {
      // Reset a mid-fade splash so it stays fully visible while retrying.
      // No-op if splashGone is already true (splash is not in the DOM).
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSplashHiding(false)
    }
    return () => {
      clearTimeout(splashLingerRef.current)
      clearTimeout(splashFadeRef.current)
    }
  }, [serverStatus])

  // In packaged mode, show a slow-start hint on the splash after 60s of reconnecting
  // so users understand a first-launch antivirus scan is the cause of the delay.
  const [slowStart, setSlowStart] = useState(false)
  const slowStartTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => {
    if (serverStatus === 'reconnecting' && window.api.isPackaged) {
      slowStartTimerRef.current = setTimeout(() => setSlowStart(true), 60_000)
    } else {
      clearTimeout(slowStartTimerRef.current)
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSlowStart(false)
    }
    return () => clearTimeout(slowStartTimerRef.current)
  }, [serverStatus])

  // Subscribe to update notifications pushed from the main process.
  useEffect(
    () => window.api.onUpdateAvailable((data) => setUpdateAvailable(data)),
    [setUpdateAvailable]
  )

  // Determine onboarding requirement once the splash clears (by which time
  // loadConfig has had ~1.5s to complete and configuredLibraryPath is stable).
  // Keyed off library path rather than album count: a returning user with an
  // empty library should not see onboarding again.
  useEffect(() => {
    if (splashGone && onboardingRequired === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setOnboardingRequired(configuredLibraryPath === null)
    }
  }, [splashGone, configuredLibraryPath, onboardingRequired])

  useEffect(() => {
    loadUiState().then(() => loadLibrary())
    void loadConfig()

    let attempts = 0
    // In packaged mode the daemon is always auto-started; a long startup means
    // Defender is scanning (first launch), not that the server is permanently down.
    // Never give up in packaged mode — retry indefinitely at the 30s cap.
    const MAX_ATTEMPTS = window.api.isPackaged ? Infinity : 8

    const connect = (): (() => void) => {
      return connectStateStream(
        applyServerState,
        () => {
          attempts++
          if (attempts >= MAX_ATTEMPTS) {
            setServerStatus('disconnected')
          } else {
            setServerStatus('reconnecting')
            const delay = Math.min(1000 * 2 ** (attempts - 1), 30000)
            setTimeout(connect, delay)
          }
        },
        () => {
          attempts = 0
          setServerStatus('connected')
          void loadUiState().then(() => loadLibrary())
          void loadQueue()
          void loadConfig()
          // Reconcile pip state in case deferred_op.completed was missed
          // while the WS was disconnected.
          void getDeferredOps().then((ops) => {
            const map: Record<number, number> = {}
            for (const { track_id, op_id } of ops) map[track_id] = op_id
            useStore.setState({ deferredOps: map })
          })
        },
        () => {
          void loadLibrary().then(() => refreshOpenAlbum())
          void loadQueue()
        },
        (done, total) => {
          setAlbumRenameProgress(total === done ? null : { done, total })
        },
        (trackId) => {
          clearDeferredOp(trackId)
          void refreshOpenAlbum()
        },
        setAudioLevel,
        bumpLastPlayedVersion
      )
    }

    const disconnect = connect()
    return disconnect
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Global keyboard shortcuts
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        searchBarRef.current?.focus()
        searchBarRef.current?.select()
        return
      }

      if (e.key === ',' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        openPrefs()
        return
      }

      if (e.key === 'Escape' && searchQuery) {
        void setSearchQuery('')
        searchBarRef.current?.blur()
        return
      }

      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return

      switch (e.key) {
        case ' ':
          e.preventDefault()
          void togglePlayPause()
          break
        case 'ArrowRight':
          e.preventDefault()
          void next()
          break
        case 'ArrowLeft':
          e.preventDefault()
          void prev()
          break
        case 'l':
        case 'L':
          void setActiveView(activeView === 'library' ? 'now-playing' : 'library')
          setActiveExtPanel(null)
          break
        case 'q':
        case 'Q':
          // Don't intercept Cmd+Q (macOS quit) or Ctrl+Q.
          if (e.metaKey || e.ctrlKey) break
          toggleQueuePanel()
          break
        case 'a':
        case 'A':
          // Artist panel is only relevant in Library.
          if (activeView === 'library') toggleArtistPanel()
          break
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [
    togglePlayPause,
    next,
    prev,
    setActiveView,
    activeView,
    searchQuery,
    setSearchQuery,
    toggleQueuePanel,
    toggleArtistPanel,
    openPrefs
  ])

  useEffect(() => {
    if (!window.api.onOpenPreferences) return
    const cleanup = window.api.onOpenPreferences(openPrefs)
    return cleanup
  }, [openPrefs])

  // Discover and load frontend extensions.
  // extState.disabledIds is captured at mount; re-runs if the disabled set changes
  // so that newly-enabled extensions are loaded immediately.
  const {
    disabledIds,
    approvedIds,
    deniedIds,
    approve,
    deny,
    resetDenied,
    getSettingValue,
    setSettingValue
  } = extState
  useEffect(() => {
    async function loadExtensions(): Promise<void> {
      try {
        const extensions = await window.KampAPI.extensions.getAll()
        setAllExtensions(extensions)

        const phase2Approved: ExtensionInfo[] = []
        const phase2Pending: ExtensionInfo[] = []

        for (const ext of extensions) {
          // Skip extensions the user has explicitly disabled.
          if (disabledIds.has(ext.id)) continue

          if (ext.phase === 2) {
            // Phase 2 (community): route through permission approval.
            if (approvedIds.has(ext.id)) {
              phase2Approved.push(ext)
            } else if (!deniedIds.has(ext.id)) {
              // Not yet decided — queue for the permission prompt.
              phase2Pending.push(ext)
            }
            // denied extensions are silently skipped.
            continue
          }

          // Phase 1: on the allow-list; pass a permission-scoped KampAPI.
          // Each SDK namespace is only present when the extension declared the
          // corresponding permission — undeclared capabilities are simply absent.
          const pset = new Set(ext.permissions)
          const scopedAPI = {
            panels: window.KampAPI.panels,
            extensions: window.KampAPI.extensions,
            ...(pset.has('player.read') ? { player: window.KampAPI.player } : {}),
            ...(pset.has('library.read') ? { library: window.KampAPI.library } : {}),
            ...(pset.has('settings')
              ? {
                  settings: {
                    get: (key: string) => getSettingValue(ext.id, key),
                    set: (key: string, value: unknown) => setSettingValue(ext.id, key, value)
                  }
                }
              : {})
          }
          const blob = new Blob([ext.code], { type: 'text/javascript' })
          const blobUrl = URL.createObjectURL(blob)
          try {
            const mod = await import(/* @vite-ignore */ blobUrl)
            if (typeof mod.register === 'function') {
              mod.register(scopedAPI)
            }
          } catch (err) {
            console.error(`[kamp] failed to load extension "${ext.id}":`, err)
          } finally {
            URL.revokeObjectURL(blobUrl)
          }
        }

        setPhase2Extensions(phase2Approved)
        setPermissionQueue(phase2Pending)
      } catch (err) {
        console.error('[kamp] extension discovery failed:', err)
      }
    }
    void loadExtensions()
  }, [disabledIds, extensionGeneration]) // eslint-disable-line react-hooks/exhaustive-deps

  // Bump the generation counter to re-run extension discovery after install/uninstall.
  // id is provided by the install callback but not needed here — discovery re-runs for all.
  const refreshExtensions = (/* id */): void => setExtensionGeneration((g) => g + 1)

  // After uninstall the panel registry in the preload (which has no unregister API) still
  // holds the removed extension's panel. A renderer reload is the cleanest way to reset it.
  const reloadAfterUninstall = (): void => window.location.reload()

  // Re-queue a previously-denied Phase 2 extension for the permission prompt.
  const handleReviewDenied = (id: string): void => {
    const ext = allExtensions.find((e) => e.id === id)
    if (!ext) return
    resetDenied(id)
    setPermissionQueue((q) => [...q, ext])
  }

  // Permission prompt handlers — advance the queue one extension at a time.
  const handlePermissionApprove = (): void => {
    const ext = permissionQueue[0]
    if (!ext) return
    approve(ext.id)
    setPhase2Extensions((prev) => [...prev, ext])
    setPermissionQueue((q) => q.slice(1))
  }
  const handlePermissionDeny = (): void => {
    const ext = permissionQueue[0]
    if (!ext) return
    deny(ext.id)
    setPermissionQueue((q) => q.slice(1))
  }

  // Track scroll position for the active main panel key (built-in name or ext ID).
  const activeMainKey = activeExtPanel ?? activeView
  useEffect(() => {
    const el = mainContentRef.current
    if (!el) return
    const onScroll = (): void => {
      viewScrollRef.current[activeMainKey] = el.scrollTop
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [activeMainKey])

  useLayoutEffect(() => {
    const el = mainContentRef.current
    if (!el) return
    el.scrollTop = viewScrollRef.current[activeMainKey] ?? 0
  }, [activeMainKey])

  if (serverStatus === 'disconnected') {
    return (
      <>
        <div className="server-offline">
          <div className="server-offline-icon">⏻</div>
          <div className="server-offline-title">kamp server is not running</div>
          <div className="server-offline-hint">
            Start it with <code>kamp server</code>
          </div>
        </div>
        {!splashGone && <SplashScreen hiding={splashHiding} slowStart={slowStart} />}
        <PreferencesDialog
          extensions={allExtensions}
          extState={extState}
          onReviewDenied={handleReviewDenied}
          onInstalled={refreshExtensions}
          onUninstalled={reloadAfterUninstall}
        />
      </>
    )
  }

  const showSetup = onboardingRequired === true && !onboardingComplete

  // Panels to show as tabs in the main area nav bar.
  const mainPanels = layout.panelsInSlot('main')

  // Determine whether a given main-slot panel tab is active.
  const isActiveMain = (panel: UnifiedPanel): boolean => {
    if (activeExtPanel) return panel.id === activeExtPanel
    if (panel.kind === 'builtin' && panel.id === 'kamp.base-kamp') return activeView === 'home'
    if (panel.kind === 'builtin' && panel.id === 'kamp.library') return activeView === 'library'
    if (panel.kind === 'builtin' && panel.id === 'kamp.now-playing')
      return activeView === 'now-playing'
    return false
  }

  // Activate a main-slot panel tab.
  const activateMain = (panel: UnifiedPanel): void => {
    void setSearchQuery('')
    if (panel.kind === 'builtin' && panel.id === 'kamp.base-kamp') {
      void setActiveView('home')
      setActiveExtPanel(null)
    } else if (panel.kind === 'builtin' && panel.id === 'kamp.library') {
      void setActiveView('library')
      setActiveExtPanel(null)
      const { selectedArtist, selectedAlbum } = useStore.getState().library
      if (selectedArtist !== null || selectedAlbum !== null) selectArtist(null)
    } else if (panel.kind === 'builtin' && panel.id === 'kamp.now-playing') {
      void setActiveView('now-playing')
      setActiveExtPanel(null)
    } else if (panel.kind === 'extension') {
      setActiveExtPanel(panel.id)
    }
  }

  // Determine what to render in the main content area.
  function renderMainContent(): React.JSX.Element {
    if (showSetup)
      return (
        <OnboardingScreen
          onComplete={handleOnboardingComplete}
          onTitleChange={setOnboardingTitle}
        />
      )
    const extPanel = activeExtPanel ? mainPanels.find((p) => p.id === activeExtPanel) : null
    // Library and NowPlaying are always mounted so <img> elements (and their
    // artLoaded state) survive view switches. The inactive pane is hidden via
    // display:none (.view-pane) and the active one uses display:contents
    // (.view-pane--active) so it has no layout box of its own.
    const isHomePane = !searchQuery && !activeExtPanel && activeView === 'home'
    const isLibraryPane = !searchQuery && !activeExtPanel && activeView === 'library'
    const isNowPlayingPane = !searchQuery && !activeExtPanel && activeView === 'now-playing'
    return (
      <>
        {/* key=panel.id forces a fresh component+DOM node on extension panel
            switch so the previous extension's container is never reused. */}
        {extPanel?.kind === 'extension' && <ExtensionPanel key={extPanel.id} panel={extPanel} />}
        {searchQuery && <SearchView />}
        <div className={isHomePane ? 'view-pane view-pane--active' : 'view-pane'}>
          <BaseKampView />
        </div>
        <div className={isLibraryPane ? 'view-pane view-pane--active' : 'view-pane'}>
          <LibraryView />
        </div>
        <div className={isNowPlayingPane ? 'view-pane view-pane--active' : 'view-pane'}>
          <NowPlayingView />
        </div>
      </>
    )
  }

  // Panels for each sidebar/bottom slot (first assigned panel wins).
  // Panel-specific visibility: each panel's toggle is independent of its slot.
  const isPanelVisible = (p: UnifiedPanel | undefined): boolean => {
    if (!p) return false
    if (p.id === 'kamp.queue') return queueVisible
    if (p.id === 'kamp.artist-list') return activeView === 'library' && artistPanelVisible
    return true
  }

  const leftPanel = layout.panelsInSlot('left')[0]
  const rightPanel = layout.panelsInSlot('right')[0]
  const bottomPanel = layout.panelsInSlot('bottom')[0]

  return (
    <div className="app">
      {serverStatus === 'reconnecting' && (
        <div className="reconnecting-banner">Reconnecting to server…</div>
      )}
      {showSetup && <div className="onboarding-titlebar">{onboardingTitle}</div>}
      <nav className="view-tabs">
        {mainPanels.map((panel) => (
          <button
            key={panel.id}
            className={isActiveMain(panel) && !searchQuery ? 'active' : ''}
            onClick={() => activateMain(panel)}
          >
            {panel.title}
          </button>
        ))}
        <SearchBar ref={searchBarRef} />
        <div className="status-rail">
          <PipelineIndicator />
          <BandcampButton />
        </div>
        <PanelPicker layout={layout} />
      </nav>
      <div className="app-body">
        {!showSetup && isPanelVisible(leftPanel) && <SlotPanel panel={leftPanel!} />}
        <main className="main-content" ref={mainContentRef}>
          {renderMainContent()}
        </main>
        {!showSetup && isPanelVisible(rightPanel) && <SlotPanel panel={rightPanel!} />}
      </div>
      {bottomPanel && <SlotPanel panel={bottomPanel} />}
      <UpdateBanner />
      {!splashGone && <SplashScreen hiding={splashHiding} slowStart={slowStart} />}
      <PreferencesDialog
        extensions={allExtensions}
        extState={extState}
        onReviewDenied={handleReviewDenied}
        onInstalled={refreshExtensions}
        onUninstalled={reloadAfterUninstall}
      />
      {/* Permission prompt: shown for Phase 2 extensions awaiting user approval. */}
      {permissionQueue[0] && (
        <ExtensionPermissionPrompt
          extension={permissionQueue[0]}
          onApprove={handlePermissionApprove}
          onDeny={handlePermissionDeny}
        />
      )}
      {/* Phase 2 iframes live here in a hidden holding area until their panel tab is activated */}
      <SandboxedExtensionLoader extensions={phase2Extensions} />
    </div>
  )
}
