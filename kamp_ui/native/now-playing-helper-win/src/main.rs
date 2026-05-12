// now-playing-helper (Windows): SMTC bridge for Kamp.
//
// Owns Windows SystemMediaTransportControls so the OS routes media keys to
// this process and the Action Center Now Playing widget shows current track
// metadata.  Protocol-compatible with the macOS Swift helper
// (kamp_ui/native/NowPlayingHelper.swift) — same stdio JSON contract on both
// platforms so kamp_ui/src/main/index.ts can drive either binary.
//
// stdin  ← newline-delimited JSON from Electron:
//   {"cmd":"update","title":"…","artist":"…","album":"…","position":12.3,
//    "duration":240.0,"playing":true,"artworkBase64":"…"?}
//   {"cmd":"stop"}
//   {"cmd":"ping"}
//
// stdout → newline-delimited JSON read by Electron:
//   {"event":"next" | "prev" | "play" | "pause" | "togglePlayPause"}
//
// stderr → free-form diagnostic logging (Electron forwards via `stdio:'inherit'`
// on the stderr fd).  Useful for confirming SMTC vs mpv keypath after rollout.

use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::OnceLock;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use serde::Deserialize;
use windows::core::{h, Interface, Result, HSTRING, PCWSTR};
use windows::Foundation::TypedEventHandler;
use windows::Media::{
    MediaPlaybackStatus, MediaPlaybackType, SystemMediaTransportControls,
    SystemMediaTransportControlsButton, SystemMediaTransportControlsButtonPressedEventArgs,
    SystemMediaTransportControlsTimelineProperties,
};
use windows::Storage::Streams::{
    DataWriter, IRandomAccessStream, InMemoryRandomAccessStream, RandomAccessStreamReference,
};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::ExitProcess;
use windows::Win32::System::WinRT::{
    ISystemMediaTransportControlsInterop, RoInitialize, RO_INIT_SINGLETHREADED,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, PostMessageW,
    RegisterClassW, TranslateMessage, MSG, WM_APP, WM_QUIT, WNDCLASSW, WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW, WS_POPUP,
};

// ---------------------------------------------------------------------------
// Stdout protocol
// ---------------------------------------------------------------------------

/// Emit one event line to Electron and flush (printer holds a buffer by default).
fn emit(event: &str) {
    let mut stdout = io::stdout().lock();
    let _ = writeln!(stdout, r#"{{"event":"{event}"}}"#);
    let _ = stdout.flush();
    // Diagnostic mirror on stderr — proves routing path during the deferred
    // "is mpv stealing keys too?" investigation.
    eprintln!("[now-playing-helper] button: {event}");
}

// ---------------------------------------------------------------------------
// Stdin protocol
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
#[serde(tag = "cmd", rename_all = "lowercase")]
enum InMessage {
    Update {
        title: Option<String>,
        artist: Option<String>,
        album: Option<String>,
        position: Option<f64>,
        duration: Option<f64>,
        playing: Option<bool>,
        #[serde(rename = "artworkBase64")]
        artwork_base64: Option<String>,
    },
    Stop,
    Ping,
}

/// Heap-allocated payload posted via PostMessageW lparam to the UI thread.
enum UiCommand {
    Update(Box<UpdatePayload>),
    Stop,
}

struct UpdatePayload {
    title: Option<String>,
    artist: Option<String>,
    album: Option<String>,
    position: Option<f64>,
    duration: Option<f64>,
    playing: Option<bool>,
    artwork_base64: Option<String>,
}

const WM_APP_COMMAND: u32 = WM_APP + 1;

// ---------------------------------------------------------------------------
// Heartbeat watchdog
// ---------------------------------------------------------------------------
//
// Defense against a deadlocked message pump.  Electron sends {"cmd":"ping"}
// every 30s; if no command arrives for >90s, we ExitProcess(2) so Electron's
// spawn `on('exit')` fires and the user at least sees a clean death rather
// than silently-broken media keys.

static LAST_INPUT_EPOCH: AtomicI64 = AtomicI64::new(0);
const HEARTBEAT_TIMEOUT_SECS: i64 = 90;

fn now_epoch() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn record_input() {
    LAST_INPUT_EPOCH.store(now_epoch(), Ordering::Relaxed);
}

fn start_heartbeat_watchdog() {
    record_input();
    thread::spawn(|| loop {
        thread::sleep(Duration::from_secs(5));
        let last = LAST_INPUT_EPOCH.load(Ordering::Relaxed);
        if now_epoch() - last > HEARTBEAT_TIMEOUT_SECS {
            eprintln!("[now-playing-helper] heartbeat stale; exiting");
            unsafe { ExitProcess(2) };
        }
    });
}

// ---------------------------------------------------------------------------
// SMTC state (UI thread only)
// ---------------------------------------------------------------------------

struct SmtcState {
    controls: SystemMediaTransportControls,
    /// Holds the artwork stream so its lifetime spans across SMTC.Update()
    /// calls.  SMTC keeps the RandomAccessStreamReference and reads lazily;
    /// dropping the underlying stream before the OS reads it produces a
    /// blank thumbnail.  Replaced (old stream drops) only on the next
    /// artwork delivery.
    _artwork_stream: Option<InMemoryRandomAccessStream>,
}

impl SmtcState {
    fn new(hwnd: HWND) -> Result<Self> {
        // Stage markers in stderr so a future E_INVALIDARG (or other HRESULT)
        // lands on a known phase rather than a bare "parameter is incorrect"
        // with no context.
        eprintln!("[now-playing-helper] init: interop factory");
        let interop: ISystemMediaTransportControlsInterop =
            windows::core::factory::<SystemMediaTransportControls, ISystemMediaTransportControlsInterop>()?;
        eprintln!("[now-playing-helper] init: GetForWindow");
        let controls: SystemMediaTransportControls = unsafe { interop.GetForWindow(hwnd)? };
        eprintln!("[now-playing-helper] init: SMTC owned");

        controls.SetIsEnabled(true)?;
        controls.SetIsPlayEnabled(true)?;
        controls.SetIsPauseEnabled(true)?;
        controls.SetIsNextEnabled(true)?;
        controls.SetIsPreviousEnabled(true)?;
        // Stop is intentionally NOT advertised — the user reported "media
        // stop makes everything stop forever" (KAMP-56), and Kamp has no
        // matching /api/v1/player/stop endpoint contract.  Suppressing the
        // button at SMTC level prevents the OS from routing WM_APPCOMMAND
        // APPCOMMAND_MEDIA_STOP to this process; the system widget hides
        // the stop affordance entirely.

        // Hook button presses.  The handler fires on a thread-pool worker —
        // it does NOT need to be on the UI thread; we only emit stdout from
        // it, which is thread-safe.  Closure args are explicitly typed because
        // windows-rs 0.59's TypedEventHandler::new takes a closure of shape
        // `&<T as Type<T>>::Default` (i.e. `&Option<T>` for WinRT class types),
        // which the compiler can't infer from the surrounding let-binding alone.
        let handler = TypedEventHandler::<
            SystemMediaTransportControls,
            SystemMediaTransportControlsButtonPressedEventArgs,
        >::new(
            |_sender: &Option<SystemMediaTransportControls>,
             args: &Option<SystemMediaTransportControlsButtonPressedEventArgs>|
             -> windows::core::Result<()> {
                if let Some(args) = args.as_ref() {
                    let button = args.Button()?;
                    let event = match button {
                        SystemMediaTransportControlsButton::Play => "play",
                        SystemMediaTransportControlsButton::Pause => "pause",
                        SystemMediaTransportControlsButton::Next => "next",
                        SystemMediaTransportControlsButton::Previous => "prev",
                        // togglePlayPause is not a separate SMTC button —
                        // Windows routes the physical play/pause key as Play
                        // or Pause depending on advertised state.
                        _ => return Ok(()),
                    };
                    emit(event);
                }
                Ok(())
            },
        );
        controls.ButtonPressed(&handler)?;

        // Claim ownership immediately with a placeholder so media keys route
        // here before any track metadata arrives via stdin.  Briefly flag
        // Playing to win arbitration vs apps registered earlier in the
        // session (architectural mirror of the Swift helper's
        // `playbackRate: 1.0` trick).  The Closed state revert happens via
        // the apply_update path once stdin delivers a real update or stop.
        let updater = controls.DisplayUpdater()?;
        updater.SetType(MediaPlaybackType::Music)?;
        let music = updater.MusicProperties()?;
        music.SetTitle(h!("Kamp"))?;
        updater.Update()?;
        controls.SetPlaybackStatus(MediaPlaybackStatus::Playing)?;
        controls.SetPlaybackStatus(MediaPlaybackStatus::Paused)?;

        Ok(Self {
            controls,
            _artwork_stream: None,
        })
    }

    fn apply_update(&mut self, p: &UpdatePayload) -> Result<()> {
        let updater = self.controls.DisplayUpdater()?;
        updater.SetType(MediaPlaybackType::Music)?;
        let music = updater.MusicProperties()?;
        music.SetTitle(&HSTRING::from(p.title.as_deref().unwrap_or("")))?;
        music.SetArtist(&HSTRING::from(p.artist.as_deref().unwrap_or("")))?;
        music.SetAlbumTitle(&HSTRING::from(p.album.as_deref().unwrap_or("")))?;

        // Artwork: decode base64 → InMemoryRandomAccessStream and hand a
        // RandomAccessStreamReference to the DisplayUpdater.  Keep the
        // stream pinned in self._artwork_stream so its lifetime outlives
        // SMTC's lazy reads.
        if let Some(b64) = p.artwork_base64.as_deref() {
            if !b64.is_empty() {
                let stream = build_artwork_stream(b64)?;
                let stream_ref =
                    RandomAccessStreamReference::CreateFromStream(&stream.cast::<IRandomAccessStream>()?)?;
                updater.SetThumbnail(&stream_ref)?;
                self._artwork_stream = Some(stream);
            } else {
                updater.SetThumbnail(None)?;
                self._artwork_stream = None;
            }
        } else {
            updater.SetThumbnail(None)?;
            self._artwork_stream = None;
        }

        updater.Update()?;

        let playing = p.playing.unwrap_or(false);
        self.controls
            .SetPlaybackStatus(if playing {
                MediaPlaybackStatus::Playing
            } else {
                MediaPlaybackStatus::Paused
            })?;

        // Timeline — re-set on every update.  Windows interpolates Position
        // while PlaybackStatus == Playing, but seeks and pause→play are
        // discontinuities the interpolator can't recover from.
        let duration = p.duration.unwrap_or(0.0).max(0.0);
        let position = p.position.unwrap_or(0.0).clamp(0.0, duration);
        let tl = SystemMediaTransportControlsTimelineProperties::new()?;
        tl.SetStartTime(secs_to_timespan(0.0))?;
        tl.SetEndTime(secs_to_timespan(duration))?;
        tl.SetPosition(secs_to_timespan(position))?;
        tl.SetMinSeekTime(secs_to_timespan(0.0))?;
        tl.SetMaxSeekTime(secs_to_timespan(duration))?;
        self.controls.UpdateTimelineProperties(&tl)?;

        Ok(())
    }

    fn apply_stop(&mut self) -> Result<()> {
        // Revert to the minimal placeholder — do NOT clear ownership
        // (clearing IsEnabled=false relinquishes routing; mirrors the Swift
        // helper's claimNowPlaying() rationale).
        let updater = self.controls.DisplayUpdater()?;
        updater.SetType(MediaPlaybackType::Music)?;
        let music = updater.MusicProperties()?;
        music.SetTitle(h!("Kamp"))?;
        music.SetArtist(h!(""))?;
        music.SetAlbumTitle(h!(""))?;
        updater.SetThumbnail(None)?;
        self._artwork_stream = None;
        updater.Update()?;
        self.controls
            .SetPlaybackStatus(MediaPlaybackStatus::Stopped)?;
        Ok(())
    }
}

fn build_artwork_stream(b64: &str) -> Result<InMemoryRandomAccessStream> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64.as_bytes())
        .map_err(|e| windows::core::Error::new(windows::core::HRESULT(-1), e.to_string()))?;
    let stream = InMemoryRandomAccessStream::new()?;
    let writer = DataWriter::CreateDataWriter(&stream)?;
    writer.WriteBytes(&bytes)?;
    let _ = writer.StoreAsync()?.get()?;
    let _ = writer.FlushAsync()?.get()?;
    writer.DetachStream()?; // returns the stream we already hold; ignore
    stream.Seek(0)?;
    Ok(stream)
}

fn secs_to_timespan(secs: f64) -> windows::Foundation::TimeSpan {
    // TimeSpan duration is in 100-nanosecond units (10_000_000 per second).
    let ticks = (secs.max(0.0) * 10_000_000.0) as i64;
    windows::Foundation::TimeSpan { Duration: ticks }
}

// ---------------------------------------------------------------------------
// Window + message pump
// ---------------------------------------------------------------------------

static UI_HWND: OnceLock<isize> = OnceLock::new();

/// Static window proc.  Pulls boxed UI commands posted via PostMessageW
/// LPARAM and dispatches them through the SmtcState owned by GWLP_USERDATA.
///
/// SAFETY: SmtcState* is stashed in GWLP_USERDATA at create time and lives
/// for the lifetime of the message pump (we only return from GetMessageW
/// once, on WM_QUIT, and free the state before exit).
unsafe extern "system" fn wnd_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if msg == WM_APP_COMMAND {
        // LPARAM carries a Box<UiCommand>.  Take ownership so we drop it
        // after dispatch — no leak even on the error path.
        let raw = lparam.0 as *mut UiCommand;
        if raw.is_null() {
            return LRESULT(0);
        }
        let cmd = Box::from_raw(raw);

        // GWLP_USERDATA holds *mut SmtcState.  Set after create via
        // SetWindowLongPtrW below; safe to dereference once UI_HWND is set.
        let state_ptr = windows::Win32::UI::WindowsAndMessaging::GetWindowLongPtrW(
            hwnd,
            windows::Win32::UI::WindowsAndMessaging::GWLP_USERDATA,
        ) as *mut SmtcState;
        if state_ptr.is_null() {
            return LRESULT(0);
        }
        let state = &mut *state_ptr;

        let result = match *cmd {
            UiCommand::Update(payload) => state.apply_update(&payload),
            UiCommand::Stop => state.apply_stop(),
        };
        if let Err(e) = result {
            eprintln!("[now-playing-helper] dispatch failed: {e}");
        }
        return LRESULT(0);
    }

    DefWindowProcW(hwnd, msg, wparam, lparam)
}

fn create_helper_window() -> Result<HWND> {
    unsafe {
        let hinstance = GetModuleHandleW(PCWSTR::null())?.into();
        let class_name = HSTRING::from("KampNowPlayingHelper");
        let wc = WNDCLASSW {
            lpfnWndProc: Some(wnd_proc),
            hInstance: hinstance,
            lpszClassName: PCWSTR(class_name.as_ptr()),
            ..Default::default()
        };
        let atom = RegisterClassW(&wc);
        if atom == 0 {
            return Err(windows::core::Error::from_win32());
        }
        // SMTC's ISystemMediaTransportControlsInterop::GetForWindow rejects
        // message-only windows (HWND_MESSAGE parent) with E_INVALIDARG and
        // requires a real top-level window per Microsoft's docs. The window
        // doesn't need to be visible — invoking ShowWindow is unnecessary —
        // but it must be top-level (parent = None).
        //
        // Style choices keep it out of the user's way:
        //   WS_POPUP         — no border, no title bar, no system menu
        //   WS_EX_TOOLWINDOW — excluded from Alt+Tab and the taskbar
        //   WS_EX_NOACTIVATE — never steals focus
        let title = HSTRING::from("Kamp Now Playing");
        let hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            PCWSTR(class_name.as_ptr()),
            PCWSTR(title.as_ptr()),
            WS_POPUP,
            0,
            0,
            0,
            0,
            None,
            None,
            Some(hinstance),
            None,
        )?;
        Ok(hwnd)
    }
}

fn run_message_pump() {
    unsafe {
        let mut msg = MSG::default();
        loop {
            // GetMessageW returns >0 on success, 0 on WM_QUIT, -1 on error.
            // BOOL::as_bool() treats -1 (error) as true; explicit i32 check
            // breaks out on both quit and error.
            let r = GetMessageW(&mut msg, None, 0, 0).0;
            if r <= 0 {
                break;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}

// ---------------------------------------------------------------------------
// Stdin reader thread
// ---------------------------------------------------------------------------

fn start_stdin_reader() {
    thread::spawn(|| {
        let stdin = io::stdin();
        let reader = stdin.lock();
        for line in reader.lines() {
            let Ok(line) = line else { break };
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            record_input();
            match serde_json::from_str::<InMessage>(trimmed) {
                Ok(InMessage::Update {
                    title,
                    artist,
                    album,
                    position,
                    duration,
                    playing,
                    artwork_base64,
                }) => {
                    let payload = Box::new(UpdatePayload {
                        title,
                        artist,
                        album,
                        position,
                        duration,
                        playing,
                        artwork_base64,
                    });
                    post_ui_command(UiCommand::Update(payload));
                }
                Ok(InMessage::Stop) => {
                    post_ui_command(UiCommand::Stop);
                }
                Ok(InMessage::Ping) => {
                    // record_input() already called above.
                }
                Err(e) => {
                    eprintln!("[now-playing-helper] bad stdin line: {e}");
                }
            }
        }
        // stdin closed — Electron exited.  Tell the UI thread to shut down
        // cleanly so we clear SMTC ownership before process exit.
        if let Some(&hwnd_isize) = UI_HWND.get() {
            unsafe {
                let _ = PostMessageW(
                    Some(HWND(hwnd_isize as *mut _)),
                    WM_QUIT,
                    WPARAM(0),
                    LPARAM(0),
                );
            }
        }
    });
}

fn post_ui_command(cmd: UiCommand) {
    let Some(&hwnd_isize) = UI_HWND.get() else {
        return;
    };
    let boxed = Box::new(cmd);
    let lparam = LPARAM(Box::into_raw(boxed) as isize);
    unsafe {
        if PostMessageW(
            Some(HWND(hwnd_isize as *mut _)),
            WM_APP_COMMAND,
            WPARAM(0),
            lparam,
        )
        .is_err()
        {
            // Reclaim the box to avoid leak if the post fails (e.g. window
            // already destroyed during shutdown race).
            let _ = Box::from_raw(lparam.0 as *mut UiCommand);
        }
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn main() -> Result<()> {
    // Single-threaded apartment matches the UI-thread model: SMTC controls
    // are consumed from one thread (the message pump), and the WinRT button
    // callback marshals back here via PostMessage rather than running cross-
    // apartment.
    unsafe {
        RoInitialize(RO_INIT_SINGLETHREADED)?;
    }

    let hwnd = create_helper_window()?;
    UI_HWND
        .set(hwnd.0 as isize)
        .expect("UI_HWND set twice");

    let state = Box::new(SmtcState::new(hwnd)?);
    let state_ptr = Box::into_raw(state);
    unsafe {
        windows::Win32::UI::WindowsAndMessaging::SetWindowLongPtrW(
            hwnd,
            windows::Win32::UI::WindowsAndMessaging::GWLP_USERDATA,
            state_ptr as isize,
        );
    }

    start_heartbeat_watchdog();
    start_stdin_reader();

    eprintln!("[now-playing-helper] ready");
    run_message_pump();

    // Clean up SMTC ownership and drop the state.
    unsafe {
        let raw = windows::Win32::UI::WindowsAndMessaging::GetWindowLongPtrW(
            hwnd,
            windows::Win32::UI::WindowsAndMessaging::GWLP_USERDATA,
        ) as *mut SmtcState;
        if !raw.is_null() {
            let state = Box::from_raw(raw);
            let _ = state.controls.SetIsEnabled(false);
        }
    }

    Ok(())
}
