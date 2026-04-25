import React, { useEffect, useRef, useState } from 'react'
import { type ScanProgress, connectLastfm } from '../api/client'
import { useStore } from '../store'

type OnboardingStep = 'welcome' | 'library' | 'watch-folder' | 'bandcamp' | 'lastfm' | 'almost-done'
type VinylPhase = 'rising' | 'spinning' | 'sinking'

const STEP_TITLES: Record<OnboardingStep, string> = {
  welcome: 'Welcome to Kamp',
  library: 'Library Setup',
  'watch-folder': 'Watch Folder Setup',
  bandcamp: 'Bandcamp Setup',
  lastfm: 'Last.fm Setup',
  'almost-done': 'Almost Done'
}

const STATIC_STRINGS = [
  'Almost done…',
  'Just a little bit longer…',
  "In the Library, press 'A' to show or hide the Artist panel",
  "The 'Now Playing' tab shows off your album art",
  "Press 'Q' at any time to show or hide the Queue panel",
  'Good things are worth waiting for…',
  'What are you gonna listen to first?'
]

function buildDynamicStrings(progress: ScanProgress | null): string[] {
  if (!progress?.active) return []
  const strings: string[] = []
  if (progress.current_file && progress.current_artist)
    strings.push(`My favorite song is ${progress.current_file} by ${progress.current_artist}…`)
  if (progress.top_artist) strings.push(`${progress.top_artist}? Exquisite taste.`)
  if (progress.current_artist)
    strings.push(`Ooooh, I haven't heard ${progress.current_artist} in ages!`)
  if (progress.current_artist && progress.current_file)
    strings.push(`${progress.current_artist}'s "${progress.current_file}"? Killer song.`)
  if (progress.current_artist)
    strings.push(`I'd sell my left kidney to see ${progress.current_artist} live…`)
  if (progress.current_artist)
    strings.push(
      `Nobody will say it out loud, but ${progress.current_artist} is totally underrated.`
    )
  return strings
}

// Vinyl proportions derived from the splash screen record (r=86 baseline).
// All values below are for R=300.
const R = 300
const CX = R // SVG width = 2*R, center at (R, R)
const VW = R * 2
const VH = R // viewBox height = R so only the top half is visible
const GROOVES = [272, 244, 216, 188, 161, 133, 112] // scaled groove ring radii
const LABEL_R = 91 // center label radius
const INNER_R = 79 // inner dashed ring radius
const SPINDLE_R = 12
// Arc path split into two 90° segments to avoid SVG antipodal point ambiguity.
// Traces the visible top semicircle counter-clockwise: left → top → right.
const ARC_PATH = `M ${-R} 0 A ${R} ${R} 0 0 1 0 ${-R} A ${R} ${R} 0 0 1 ${R} 0`
const ARC_LENGTH = Math.PI * R

interface Props {
  onComplete: () => void
  onTitleChange: (title: string) => void
}

export function OnboardingScreen({ onComplete, onTitleChange }: Props): React.JSX.Element {
  const scanStatus = useStore((s) => s.scanStatus)
  const scanProgress = useStore((s) => s.scanProgress)
  const setLibraryPath = useStore((s) => s.setLibraryPath)
  const scanLibrary = useStore((s) => s.scanLibrary)
  const setWatchFolderPath = useStore((s) => s.setWatchFolderPath)
  const configuredLibraryPath = useStore((s) => s.configuredLibraryPath)
  const loadConfig = useStore((s) => s.loadConfig)

  const [step, setStep] = useState<OnboardingStep>('welcome')
  const [vinylPhase, setVinylPhase] = useState<VinylPhase>('rising')
  const [cardError, setCardError] = useState<string | null>(null)
  const [bandcampBusy, setBandcampBusy] = useState(false)
  const [bandcampDownloadAll, setBandcampDownloadAll] = useState(false)
  const [lastfmUsername, setLastfmUsername] = useState('')
  const [lastfmPassword, setLastfmPassword] = useState('')
  const [lastfmBusy, setLastfmBusy] = useState(false)
  const [currentString, setCurrentString] = useState(STATIC_STRINGS[0])
  const [stringVisible, setStringVisible] = useState(true)
  const [showAllSet, setShowAllSet] = useState(false)
  // Fade transition state for content changes
  const [contentVisible, setContentVisible] = useState(true)

  // Stable ref for onComplete so the scan-done effect never captures a stale closure.
  const onCompleteRef = useRef(onComplete)
  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  // Tracks whether the scan finished so card-step handlers know to skip 'almost-done'.
  const scanDoneRef = useRef(false)

  // Fade-transition helper: fades out content, changes step, fades back in.
  function changeStep(next: OnboardingStep): void {
    setContentVisible(false)
    setTimeout(() => {
      setStep(next)
      setContentVisible(true)
    }, 220)
  }

  useEffect(() => {
    onTitleChange(STEP_TITLES[step])
  }, [step, onTitleChange])

  // Welcome: auto-advance after 3s with a fade transition.
  useEffect(() => {
    if (step !== 'welcome') return
    const t = setTimeout(() => changeStep('library'), 3000)
    return () => clearTimeout(t)
  }, [step])

  // When scan completes: sink the vinyl; if past all cards, finish onboarding.
  // onComplete is accessed via ref so this effect never needs it as a dep,
  // preventing re-renders from recreating the closure and cancelling the timeout.
  useEffect(() => {
    if (scanStatus !== 'done' || scanDoneRef.current) return
    scanDoneRef.current = true
    if (step === 'almost-done') {
      // Snap rotating strings to "All set!" immediately (deferred to avoid sync setState in effect).
      setTimeout(() => setShowAllSet(true), 0)
      // After the 500ms hold, sink the vinyl; then wait for sink + art-preload buffer.
      setTimeout(() => {
        setVinylPhase('sinking')
        // ~600ms CSS sink + ~1s buffer for the browser to begin fetching album art.
        setTimeout(() => onCompleteRef.current(), 1600)
      }, 500)
    } else {
      setTimeout(() => setVinylPhase('sinking'), 0)
    }
  }, [scanStatus, step])

  // Keep refs so the rotation interval can read the latest values without
  // needing them as dependencies (which would restart the interval on every poll tick).
  const dynamicStringsRef = useRef<string[]>([])
  const stringPosRef = useRef(0)
  useEffect(() => {
    dynamicStringsRef.current = buildDynamicStrings(scanProgress)
  }, [scanProgress])

  // Rotate the 'Almost done' strings every 4s with a fade.
  // The next string is snapshotted during the fade-out so live scanProgress
  // updates between rotations never mutate the currently visible string.
  useEffect(() => {
    if (step !== 'almost-done') return
    const t = setInterval(() => {
      setStringVisible(false)
      setTimeout(() => {
        const pool = [...STATIC_STRINGS, ...dynamicStringsRef.current]
        stringPosRef.current = (stringPosRef.current + 1) % pool.length
        setCurrentString(pool[stringPosRef.current])
        setStringVisible(true)
      }, 350)
    }, 3000)
    return () => clearInterval(t)
  }, [step])

  // Scan progress: show a minimum sliver so the arc is visible during active scans
  // where the total count arrives late.
  const progress = (() => {
    if (!scanProgress) return 0
    if (scanProgress.total > 0) return scanProgress.current / scanProgress.total
    if (scanProgress.active) return 0.04
    return 0
  })()
  const drawn = progress * ARC_LENGTH

  async function handleChooseLibrary(): Promise<void> {
    setCardError(null)
    const dir = await window.api.openDirectory()
    if (dir === null) return
    try {
      await setLibraryPath(dir)
    } catch {
      setCardError('Could not set library path.')
      return
    }
    scanLibrary() // fire-and-forget; track completion via scanStatus
    setVinylPhase('spinning')
    changeStep('watch-folder')
  }

  async function handleChooseWatchFolder(): Promise<void> {
    setCardError(null)
    const dir = await window.api.openDirectory()
    if (dir === null) return
    if (dir === configuredLibraryPath) {
      setCardError("Your watch folder can't be the same as your library folder.")
      return
    }
    try {
      await setWatchFolderPath(dir)
    } catch {
      setCardError('Could not set watch folder.')
      return
    }
    changeStep('bandcamp')
  }

  async function handleBandcampLogin(): Promise<void> {
    setBandcampBusy(true)
    setCardError(null)
    try {
      const result = await window.api.bandcamp.beginLogin()
      if (result.ok) {
        await loadConfig()
        if (bandcampDownloadAll) {
          window.api.bandcamp.triggerSyncAll().catch(console.error)
        }
        changeStep('lastfm')
      } else {
        setCardError(result.error ?? 'Login cancelled.')
      }
    } catch {
      setCardError('Login failed.')
    } finally {
      setBandcampBusy(false)
    }
  }

  function advancePastCards(): void {
    if (scanDoneRef.current) {
      onCompleteRef.current()
    } else {
      changeStep('almost-done')
    }
  }

  async function handleLastfmLogin(): Promise<void> {
    setLastfmBusy(true)
    setCardError(null)
    try {
      const result = await connectLastfm(lastfmUsername, lastfmPassword)
      if (result.ok) {
        advancePastCards()
      } else {
        setCardError('Login failed.')
      }
    } catch {
      setCardError('Login failed.')
    } finally {
      setLastfmBusy(false)
    }
  }

  return (
    <div className="onboarding-screen">
      {/* Step content */}
      <div
        className="onboarding-content"
        style={{ opacity: contentVisible ? 1 : 0, transition: 'opacity 0.22s ease' }}
      >
        {step === 'welcome' && (
          <div className="onboarding-welcome-text">
            Welcome to <strong>kamp</strong>!
          </div>
        )}

        {step === 'library' && (
          <div className="onboarding-library">
            <div className="onboarding-heading">Let&apos;s set up your library</div>
            <button className="onboarding-primary-btn" onClick={handleChooseLibrary}>
              Choose Library Folder
            </button>
            {cardError && <div className="onboarding-error">{cardError}</div>}
          </div>
        )}

        {(step === 'watch-folder' || step === 'bandcamp' || step === 'lastfm') && (
          <div className="onboarding-card">
            <div className="onboarding-card-heading">While we&apos;re waiting&hellip;</div>

            {step === 'watch-folder' && (
              <>
                <button className="onboarding-primary-btn" onClick={handleChooseWatchFolder}>
                  Choose Watch Folder
                </button>
                <p className="onboarding-card-body">
                  <strong>kamp</strong> will keep an eye on your watch folder to auto-tag and add
                  files to your library.
                  <br />
                  This is also where Kamp will download new files, if you sign into Bandcamp.
                </p>
                {cardError && <div className="onboarding-error">{cardError}</div>}
              </>
            )}

            {step === 'bandcamp' && (
              <>
                <button
                  className="onboarding-primary-btn"
                  onClick={handleBandcampLogin}
                  disabled={bandcampBusy}
                >
                  {bandcampBusy ? 'Logging in…' : 'Log in to Bandcamp'}
                </button>
                <p className="onboarding-card-body">
                  If you have a Bandcamp account, <strong>kamp</strong> can download your purchases
                  and put them into your library
                </p>
                <label className="onboarding-toggle">
                  <input
                    type="checkbox"
                    checked={bandcampDownloadAll}
                    onChange={(e) => setBandcampDownloadAll(e.target.checked)}
                  />
                  Download all my existing purchases
                </label>
                {cardError && <div className="onboarding-error">{cardError}</div>}
                <button className="onboarding-skip-btn" onClick={() => changeStep('lastfm')}>
                  Skip
                </button>
              </>
            )}

            {step === 'lastfm' && (
              <>
                <input
                  className="prefs-input"
                  type="text"
                  placeholder="Last.fm username"
                  value={lastfmUsername}
                  autoComplete="username"
                  onChange={(e) => setLastfmUsername(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void handleLastfmLogin()}
                />
                <input
                  className="prefs-input"
                  type="password"
                  placeholder="Password"
                  value={lastfmPassword}
                  autoComplete="current-password"
                  onChange={(e) => setLastfmPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void handleLastfmLogin()}
                />
                <button
                  className="onboarding-primary-btn"
                  onClick={handleLastfmLogin}
                  disabled={lastfmBusy}
                >
                  {lastfmBusy ? 'Connecting…' : 'Connect Last.fm'}
                </button>
                <p className="onboarding-card-body">
                  Connect your <strong>Last.fm</strong> account to save your listening history.
                </p>
                {cardError && <div className="onboarding-error">{cardError}</div>}
                <button className="onboarding-skip-btn" onClick={advancePastCards}>
                  Skip
                </button>
              </>
            )}
          </div>
        )}

        {step === 'almost-done' && (
          <div
            className="onboarding-almost-done-text"
            style={{ opacity: showAllSet || stringVisible ? 1 : 0 }}
          >
            {showAllSet ? 'All set!' : currentString}
          </div>
        )}
      </div>

      {/* Spacer: same height as the fixed vinyl so content doesn't overlap the record. */}
      <div className="onboarding-vinyl-spacer" aria-hidden="true" />

      {/* Vinyl record — matches the splash screen design (no tonearm / sparkles) */}
      <div className={`onboarding-vinyl-wrap onboarding-vinyl-wrap--${vinylPhase}`}>
        <svg
          viewBox={`0 0 ${VW} ${VH}`}
          className="onboarding-vinyl-svg"
          overflow="visible"
          aria-hidden="true"
        >
          <g transform={`translate(${CX}, ${R})`}>
            {/* Spinning group: body + grooves + label — does NOT include progress arc */}
            <g className={vinylPhase === 'spinning' ? 'onboarding-disc--spinning' : ''}>
              {/* Record body */}
              <circle cx="0" cy="0" r={R} fill="#1c1a16" stroke="#c4aa78" strokeWidth="5" />
              {/* Pressed grooves */}
              {GROOVES.map((gr) => (
                <circle
                  key={gr}
                  cx="0"
                  cy="0"
                  r={gr}
                  fill="none"
                  stroke="#2a2620"
                  strokeWidth="2.8"
                />
              ))}
              {/* Center label */}
              <circle cx="0" cy="0" r={LABEL_R} fill="#bf7a20" />
              <circle
                cx="0"
                cy="0"
                r={INNER_R}
                fill="none"
                stroke="#8a5515"
                strokeWidth="2"
                strokeDasharray="9 7"
              />
              <circle cx="0" cy="0" r={LABEL_R} fill="none" stroke="#8a5515" strokeWidth="3.5" />
              {/* Label text */}
              <text
                x="0"
                y="-10"
                dx="4"
                textAnchor="middle"
                fill="#1c1a16"
                fontSize="31"
                fontWeight="700"
                letterSpacing="8"
                fontFamily="'DM Sans', sans-serif"
              >
                KAMP
              </text>
              <text
                x="0"
                y="18"
                dx="2.5"
                textAnchor="middle"
                fill="#1c1a16"
                fontSize="17"
                letterSpacing="5"
                fontFamily="'DM Sans', sans-serif"
              >
                HI · FI
              </text>
              {/* Spindle */}
              <circle cx="0" cy="0" r={SPINDLE_R} fill="#141414" />
            </g>

            {/* Progress arc — top semicircle left→right, NOT part of spinning group */}
            {vinylPhase === 'spinning' && (
              <path
                d={ARC_PATH}
                fill="none"
                stroke="var(--accent)"
                strokeWidth="6"
                strokeLinecap="round"
                strokeDasharray={`${drawn} ${ARC_LENGTH}`}
              />
            )}
          </g>
        </svg>
      </div>
    </div>
  )
}
