import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { connectStateStream } from './api/client'
import { ArtistPanel } from './components/ArtistPanel'
import { ExtensionPanel } from './components/ExtensionPanel'
import { LibraryView } from './components/LibraryView'
import { NowPlayingView } from './components/NowPlayingView'
import { PanelPicker } from './components/PanelPicker'
import { PreferencesDialog } from './components/PreferencesDialog'
import { QueuePanel } from './components/QueuePanel'
import { SearchBar } from './components/SearchBar'
import { SearchView } from './components/SearchView'
import { SetupScreen } from './components/SetupScreen'
import { SplashScreen } from './components/SplashScreen'
import { TransportBar } from './components/TransportBar'
import { SandboxedExtensionLoader } from './components/SandboxedExtensionLoader'
import { registerBuiltInPanel, usePanelLayout } from './hooks/usePanelLayout'
import type { UnifiedPanel } from './hooks/usePanelLayout'
import type { ExtensionInfo } from '../../shared/kampAPI'

// ---------------------------------------------------------------------------
// Register built-in panels before the component mounts.
// Each call is idempotent — safe across HMR and React StrictMode re-runs.
// ---------------------------------------------------------------------------
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
  const loadUiState = useStore((s) => s.loadUiState)
  const applyServerState = useStore((s) => s.applyServerState)
  const setServerStatus = useStore((s) => s.setServerStatus)
  const serverStatus = useStore((s) => s.serverStatus)
  const hasAlbums = useStore((s) => s.library.albums.length > 0)
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

  const layout = usePanelLayout()

  // Active extension panel id, or null when a built-in view is showing.
  const [activeExtPanel, setActiveExtPanel] = useState<string | null>(null)
  // Phase 2 (community) extensions to render in sandboxed iframes.
  const [phase2Extensions, setPhase2Extensions] = useState<ExtensionInfo[]>([])

  const searchBarRef = useRef<HTMLInputElement>(null)
  const mainContentRef = useRef<HTMLElement>(null)

  // Per-view scroll positions — kept current by a scroll listener so we never
  // read a browser-clamped value when the outgoing view's content was taller.
  // Key: active main panel id (built-in view name or extension panel id).
  const viewScrollRef = useRef<Partial<Record<string, number>>>({})

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
    }
    return () => {
      clearTimeout(splashLingerRef.current)
      clearTimeout(splashFadeRef.current)
    }
  }, [serverStatus])

  useEffect(() => {
    loadUiState().then(() => loadLibrary())

    let attempts = 0
    const MAX_ATTEMPTS = 8

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
        },
        () => {
          void loadLibrary().then(() => refreshOpenAlbum())
        }
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
          toggleArtistPanel()
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
  useEffect(() => {
    async function loadExtensions(): Promise<void> {
      try {
        const extensions = await window.KampAPI.extensions.getAll()
        const phase2: ExtensionInfo[] = []
        for (const ext of extensions) {
          if (ext.phase === 2) {
            // Phase 2 (community) extensions: collected and passed to
            // SandboxedExtensionLoader, which renders them in isolated iframes.
            phase2.push(ext)
            continue
          }

          // Phase 1: extension is on the allow-list; pass a permission-scoped KampAPI.
          // Panels and serverUrl are always available (they are the extension ABC contract).
          // Future capabilities (library.read, player.read, etc.) will be gated here.
          const pset = new Set(ext.permissions)
          const scopedAPI = {
            serverUrl: window.KampAPI.serverUrl,
            panels: window.KampAPI.panels,
            extensions: window.KampAPI.extensions,
            // Placeholder gates for declared capabilities — expand as the API grows.
            _permissions: pset
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
        setPhase2Extensions(phase2)
      } catch (err) {
        console.error('[kamp] extension discovery failed:', err)
      }
    }
    void loadExtensions()
  }, [])

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
        {!splashGone && <SplashScreen hiding={splashHiding} />}
        <PreferencesDialog />
      </>
    )
  }

  const showSetup = serverStatus === 'connected' && !hasAlbums

  // Panels to show as tabs in the main area nav bar.
  const mainPanels = layout.panelsInSlot('main')

  // Determine whether a given main-slot panel tab is active.
  const isActiveMain = (panel: UnifiedPanel): boolean => {
    if (activeExtPanel) return panel.id === activeExtPanel
    if (panel.kind === 'builtin' && panel.id === 'kamp.library') return activeView === 'library'
    if (panel.kind === 'builtin' && panel.id === 'kamp.now-playing')
      return activeView === 'now-playing'
    return false
  }

  // Activate a main-slot panel tab.
  const activateMain = (panel: UnifiedPanel): void => {
    if (panel.kind === 'builtin' && panel.id === 'kamp.library') {
      void setActiveView('library')
      setActiveExtPanel(null)
    } else if (panel.kind === 'builtin' && panel.id === 'kamp.now-playing') {
      void setActiveView('now-playing')
      setActiveExtPanel(null)
    } else if (panel.kind === 'extension') {
      setActiveExtPanel(panel.id)
    }
  }

  // Determine what to render in the main content area.
  function renderMainContent(): React.JSX.Element {
    if (showSetup) return <SetupScreen />
    if (searchQuery) return <SearchView />
    if (activeExtPanel) {
      const extPanel = mainPanels.find((p) => p.id === activeExtPanel)
      if (extPanel && extPanel.kind === 'extension') return <ExtensionPanel panel={extPanel} />
    }
    if (activeView === 'now-playing') return <NowPlayingView />
    return <LibraryView />
  }

  // Panels for each sidebar/bottom slot (first assigned panel wins).
  // Panel-specific visibility: each panel's toggle is independent of its slot.
  const isPanelVisible = (p: UnifiedPanel | undefined): boolean => {
    if (!p) return false
    if (p.id === 'kamp.queue') return queueVisible
    if (p.id === 'kamp.artist-list') return artistPanelVisible
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
      {!showSetup && (
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
          <PanelPicker layout={layout} />
        </nav>
      )}
      <div className="app-body">
        {!showSetup && isPanelVisible(leftPanel) && <SlotPanel panel={leftPanel!} />}
        <main className="main-content" ref={mainContentRef}>
          {renderMainContent()}
        </main>
        {!showSetup && isPanelVisible(rightPanel) && <SlotPanel panel={rightPanel!} />}
      </div>
      {bottomPanel && <SlotPanel panel={bottomPanel} />}
      {!splashGone && <SplashScreen hiding={splashHiding} />}
      <PreferencesDialog />
      {/* Phase 2 iframes live here in a hidden holding area until their panel tab is activated */}
      <SandboxedExtensionLoader extensions={phase2Extensions} />
    </div>
  )
}
