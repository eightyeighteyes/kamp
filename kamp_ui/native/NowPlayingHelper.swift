/**
 * NowPlayingHelper — macOS Now Playing + media key bridge for Kamp.
 *
 * This process runs as a hidden AppKit application so macOS routes media key
 * events to it via MPRemoteCommandCenter.  Plain command-line tools (without
 * NSApplication) are not recognized by the Now Playing routing system and
 * never receive MPRemoteCommandCenter callbacks — that is why the Python
 * server's attempts failed and why RunLoop.main.run() alone is not enough.
 *
 * The process claims Now Playing ownership immediately on start (with a
 * minimal placeholder) so media keys route here before any track data arrives
 * from Electron.  Electron then sends real track state via stdin and the
 * widget updates accordingly.
 *
 * Protocol
 * --------
 * stdin  ← newline-delimited JSON sent by Electron:
 *   {"cmd":"update","title":"…","artist":"…","album":"…","position":12.3,"duration":240.0,"playing":true}
 *   {"cmd":"stop"}
 *
 * stdout → newline-delimited JSON read by Electron:
 *   {"event":"next"}
 *   {"event":"prev"}
 *   {"event":"play"}
 *   {"event":"pause"}
 *   {"event":"togglePlayPause"}
 */

import AppKit
import MediaPlayer

// ---------------------------------------------------------------------------
// Stdout protocol — Electron reads these lines and routes to Python REST API.
// ---------------------------------------------------------------------------

func emit(_ event: String) {
    print(#"{"event":"\#(event)"}"#)
    fflush(stdout)
}

// ---------------------------------------------------------------------------
// MPRemoteCommandCenter — fires on the main queue (NSApp.run() pumps it).
// ---------------------------------------------------------------------------

func registerCommands() {
    let cc = MPRemoteCommandCenter.shared()

    _ = cc.nextTrackCommand.addTarget { _ in emit("next"); return .success }
    _ = cc.previousTrackCommand.addTarget { _ in emit("prev"); return .success }
    _ = cc.playCommand.addTarget { _ in emit("play"); return .success }
    _ = cc.pauseCommand.addTarget { _ in emit("pause"); return .success }
    // togglePlayPause fires when the user presses the physical play/pause key.
    _ = cc.togglePlayPauseCommand.addTarget { _ in emit("togglePlayPause"); return .success }
}

// ---------------------------------------------------------------------------
// MPNowPlayingInfoCenter — updated from JSON received on stdin.
// ---------------------------------------------------------------------------

func claimNowPlaying() {
    // Set a minimal placeholder immediately so macOS routes media key events
    // to this process before Electron sends real track state via stdin.
    //
    // playbackRate: 1.0 is intentional — macOS routes media keys to whichever
    // app most recently had rate > 0. Using 0.0 ("paused") loses the routing
    // competition to apps like Spotify or Music.app that were playing earlier.
    // Real state (including the actual rate) arrives quickly via stdin.
    var info: [String: Any] = [
        MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
        MPNowPlayingInfoPropertyIsLiveStream: false,
        MPNowPlayingInfoPropertyPlaybackRate: 1.0,
    ]
    info[MPMediaItemPropertyTitle] = "Kamp"
    MPNowPlayingInfoCenter.default().nowPlayingInfo = info
}

func applyUpdate(_ json: [String: Any]) {
    guard let cmd = json["cmd"] as? String else { return }

    if cmd == "stop" {
        // Revert to the minimal placeholder rather than clearing entirely —
        // clearing nowPlayingInfo relinquishes Now Playing ownership, which
        // would re-enable mpv's HID tap and break next/prev routing.
        claimNowPlaying()
        return
    }

    guard cmd == "update" else { return }

    var info: [String: Any] = [
        MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
        MPNowPlayingInfoPropertyIsLiveStream: false,
    ]
    if let title    = json["title"]    as? String { info[MPMediaItemPropertyTitle]                   = title    }
    if let artist   = json["artist"]   as? String { info[MPMediaItemPropertyArtist]                  = artist   }
    if let album    = json["album"]    as? String { info[MPMediaItemPropertyAlbumTitle]               = album    }
    if let duration = json["duration"] as? Double { info[MPMediaItemPropertyPlaybackDuration]         = duration }
    if let position = json["position"] as? Double { info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = position }
    if let playing  = json["playing"]  as? Bool   { info[MPNowPlayingInfoPropertyPlaybackRate]        = playing ? 1.0 : 0.0 }

    // Artwork: Electron sends base64-encoded image bytes when the track has
    // embedded art.  MPMediaItemArtwork(boundsSize:requestHandler:) is the
    // supported API on macOS 10.13+ for supplying artwork to the Now Playing
    // widget.  The request handler is called with the size the system needs;
    // we return the same NSImage at any requested size.
    if let artworkBase64 = json["artworkBase64"] as? String,
       let artworkData   = Data(base64Encoded: artworkBase64),
       let artworkImage  = NSImage(data: artworkData) {
        let artwork = MPMediaItemArtwork(boundsSize: artworkImage.size) { _ in artworkImage }
        info[MPMediaItemPropertyArtwork] = artwork
    }

    MPNowPlayingInfoCenter.default().nowPlayingInfo = info
}

// ---------------------------------------------------------------------------
// Stdin reader — background queue; updates dispatched to main queue.
// ---------------------------------------------------------------------------

func startStdinReader() {
    DispatchQueue.global(qos: .utility).async {
        while let line = readLine(strippingNewline: true) {
            guard !line.isEmpty,
                  let data = line.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { continue }
            DispatchQueue.main.async { applyUpdate(json) }
        }
        // stdin closed (Electron exited) — clear widget and quit cleanly.
        DispatchQueue.main.async {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            NSApp.terminate(nil)
        }
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

// Running as a hidden AppKit application is required for MPRemoteCommandCenter
// to receive media key callbacks.  A plain CLI tool (RunLoop.main.run() only)
// is not recognized by the Now Playing routing system.
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)   // no Dock icon, no menu bar

registerCommands()
claimNowPlaying()      // own Now Playing immediately; real metadata follows via stdin
startStdinReader()
NSApp.run()            // AppKit event loop; also pumps CFRunLoop and main GCD queue
