"""Crate preview playback on a second, isolated mpv (KAMP-651).

The epic's hardest promise is that previewing a record does not break the queue
you were already listening to. This keeps that promise by *construction* rather
than by discipline: the preview runs on its own :class:`MpvPlaybackEngine`, which
mints its own IPC socket and so is a genuinely separate mpv process. It is never
handed to ``create_app`` — which unconditionally assigns three callbacks to
whatever engine it is given — and it is never bound to the name the daemon's
state-saver closes over. So the queue, the scrobbler, ``track_stats``, the 5s
session persistence and the OS now-playing widget cannot see it.

The one deviation from "wire no callbacks": this does wire a preview-*local*
``on_track_end`` and ``on_file_loaded``. The reason for the rule is avoiding
contact with the machinery above, and these touch none of it — without them the
preview could not know a track had ended and the UI would show it playing
forever.

Threading: one ``RLock`` covers constructing, tearing down and commanding the
engine. Construction spawns mpv synchronously and can block for seconds or
raise, so two racing first-previews would otherwise build two engines and orphan
one (there is no Job Object on POSIX, and the socket tmpdir is only unlinked by
``shutdown()``). :meth:`snapshot` deliberately takes no lock — it reads a
whole-object reference that is only ever replaced, never mutated in place — so
polling the UI's own state can never stall behind a cold spawn.
"""

from __future__ import annotations

import json
import logging
import threading
import time as _time
from typing import TYPE_CHECKING, Any, Callable

from .discovery import Candidate, PreviewStream

if TYPE_CHECKING:  # pragma: no cover - types only
    from kamp_core.library import LibraryIndex
    from kamp_core.playback import MpvPlaybackEngine

    from .discovery import DiscoverySource

logger = logging.getLogger(__name__)

#: How long the preview engine may sit idle before its mpv is killed. Spawning
#: costs a second or two, so this trades a rare slow first click for not holding
#: an audio device open all session.
IDLE_TIMEOUT_SECS = 300.0

#: Below this many seconds a preview is a misclick, not a listen. It matters
#: because the first 'previewed' event flips discovery_items.state from 'fresh'
#: irreversibly (the rank is monotonic), so a half-second slip would relabel a
#: card forever and inflate KAMP-655's engagement stats.
MIN_PREVIEW_SECS = 10.0

IDLE = "idle"
PREPARING = "preparing"
PLAYING = "playing"
PAUSED = "paused"

_IDLE_STATE: dict[str, Any] = {
    "state": IDLE,
    "item_id": None,
    "track_num": None,
    "title": "",
    "position": 0.0,
    "position_updated_at": 0.0,
    "duration": 0.0,
    "buffering": False,
    "tracks": [],
    "error": None,
    # The record still ON the deck with nothing playing (KAMP-678): the main
    # transport took the floor, but that is not a statement about the record the
    # user was listening to, so it stays cued rather than going back to the
    # crate. Metadata on IDLE rather than a state of its own on purpose -- every
    # guard in this class reads `state` to decide whether the ENGINE is busy, and
    # a cued record's honest answer is no. A fifth state would make _idle_kill
    # refuse to reap the engine, pinning an mpv process and the audio device open
    # for the rest of the session.
    "parked_item_id": None,
    "parked_track_num": None,
}


class PreviewPlayer:
    """Owns the preview engine, the main-engine handoff, and preview state."""

    def __init__(
        self,
        index: "LibraryIndex",
        *,
        main_engine: "MpvPlaybackEngine",
        engine_factory: Callable[[], "MpvPlaybackEngine"],
        source_factory: Callable[[], "DiscoverySource | None"],
        notify: Callable[[dict[str, Any]], None] | None = None,
        idle_timeout: float = IDLE_TIMEOUT_SECS,
        now: Callable[[], float] = _time.time,
    ) -> None:
        self._index = index
        self._main = main_engine
        self._engine_factory = engine_factory
        self._source_factory = source_factory
        self._notify = notify
        self._idle_timeout = idle_timeout
        self._now = now

        self._lock = threading.RLock()
        self._engine: "MpvPlaybackEngine | None" = None
        self._idle_timer: threading.Timer | None = None
        self._state: dict[str, Any] = dict(_IDLE_STATE)

        # Resolved tracks per discovery item. In memory only: PreviewStream's
        # docstring forbids *persisting* a signed URL, and this respects that --
        # but re-fetching the album page for every next/prev would be a request
        # per button press, so the list is kept for as long as its URLs live.
        self._tracks: dict[int, list[PreviewStream]] = {}

        # Captured BEFORE main is paused and never read back: pause() is a
        # ~0.4s fade (a script-message the Lua schedules), so main.state.playing
        # is still True immediately after the call and would lie.
        self._main_was_playing = False

        # Seconds listened for the item currently loaded, and when the current
        # playing stretch began (None while paused).
        self._listened = 0.0
        self._playing_since: float | None = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The current preview state. Lock-free by design — see the module docstring."""
        snap = dict(self._state)
        engine = self._engine
        # Position is pulled rather than pushed: the engine has no position
        # callback, and the UI interpolates between snapshots.
        if engine is not None and snap["state"] in (PLAYING, PAUSED):
            snap["position"] = float(engine.state.position)
            snap["position_updated_at"] = self._now()
        return snap

    def _publish(self, **fields: Any) -> dict[str, Any]:
        """Replace the state wholesale and push it. Never mutates in place, so
        :meth:`snapshot` can read without a lock."""
        state = dict(self._state)
        state.update(fields)
        self._state = state
        snap = self.snapshot()
        if self._notify is not None:
            self._notify(snap)
        return snap

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def _ensure_engine(self) -> "MpvPlaybackEngine":
        """Return the engine, building it if this is the first preview.

        Caller must hold the lock: construction is slow and racing it would
        orphan an mpv that nothing holds a reference to.
        """
        if self._engine is None:
            logger.info("preview: spawning the preview engine")
            engine = self._engine_factory()
            engine.on_track_end = self._on_track_end
            engine.on_file_loaded = self._on_file_loaded
            # Deliberately NOT on_play_state_changed or on_audio_level: the
            # first belongs to the main player's WS state, the second to the
            # main VU meter. Neither should ever hear from this engine.
            self._engine = engine
            self._apply_main_audio_settings()
        return self._engine

    def _apply_main_audio_settings(self) -> None:
        """Match the user's volume and mute on the preview engine.

        A fresh engine starts at volume 100 unmuted regardless of what the user
        set, because nothing is passed on the mpv command line and PlaybackState
        simply defaults. Without this, someone listening at 30 -- or muted --
        gets a preview at full blast.
        """
        engine = self._engine
        if engine is None:
            return
        try:
            engine.volume = int(self._main.state.volume)
            if self._main.state.muted:
                engine.muted = True
        except Exception:  # noqa: BLE001 - a wedged engine must not break preview
            logger.warning(
                "preview: could not mirror main audio settings", exc_info=True
            )

    def set_volume(self, volume: int) -> None:
        """Follow a main-player volume change while a preview is alive."""
        with self._lock:
            if self._engine is not None:
                self._engine.volume = volume

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.muted = muted

    def _arm_idle_timer(self) -> None:
        self._cancel_idle_timer()
        timer = threading.Timer(self._idle_timeout, self._idle_kill)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _idle_kill(self) -> None:
        with self._lock:
            self._idle_timer = None
            if self._engine is None or self._state["state"] != IDLE:
                return
            logger.info("preview: idle, shutting the preview engine down")
            self._teardown_engine()

    def _teardown_engine(self) -> None:
        """Caller must hold the lock."""
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.shutdown()
            except Exception:  # noqa: BLE001 - teardown must not raise
                logger.warning("preview: engine shutdown failed", exc_info=True)

    def shutdown(self) -> None:
        """Tear everything down. Safe to call more than once."""
        with self._lock:
            self._cancel_idle_timer()
            self._record_listened()
            self._teardown_engine()
            self._state = dict(_IDLE_STATE)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self, item_id: int, track_num: int | None = None) -> dict[str, Any]:
        """Start (or switch to) a preview of *item_id*.

        Resolving the track list can take a round trip, so the state goes to
        ``preparing`` first: the cold path is an engine spawn plus an album-page
        fetch plus buffering, and a UI with no state for that looks broken.
        """
        with self._lock:
            self._cancel_idle_timer()
            if self._state["item_id"] != item_id:
                self._record_listened()

            item = self._index.discovery_item(item_id)
            if item is None:
                return self._publish(state=IDLE, error="not_found")

            self._publish(
                state=PREPARING,
                item_id=item_id,
                title="",
                error=None,
                buffering=True,
                position=0.0,
                duration=0.0,
                # This record is live now, so it is no longer merely cued. Cleared
                # here rather than on success: the error returns below all publish
                # IDLE, and a stale cued id would leave the PREVIOUS record on the
                # deck as though the failed one had never been asked for.
                parked_item_id=None,
                parked_track_num=None,
            )

            try:
                tracks = self._resolve(item_id, item)
            except _RateLimited:
                return self._publish(state=IDLE, buffering=False, error="rate_limited")
            if not tracks:
                return self._publish(state=IDLE, buffering=False, error="unavailable")

            track = self._pick(tracks, track_num)
            if track is None:
                return self._publish(state=IDLE, buffering=False, error="unavailable")

            self._take_over_from_main()
            engine = self._ensure_engine()
            engine.play(track.url)

            self._listened = 0.0
            self._playing_since = self._now()
            return self._publish(
                state=PLAYING,
                item_id=item_id,
                track_num=track.track_num,
                title=track.title,
                duration=track.duration,
                buffering=True,
                position=0.0,
                tracks=[
                    {"track_num": t.track_num, "title": t.title, "duration": t.duration}
                    for t in tracks
                ],
            )

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._engine is None or self._state["state"] != PLAYING:
                return self.snapshot()
            self._engine.pause()
            self._accumulate()
            return self._publish(state=PAUSED)

    def resume(self) -> dict[str, Any]:
        with self._lock:
            # A cued record has nothing loaded to resume: release_for_main
            # unloaded it, and the idle timer may since have reaped the process
            # outright. Replaying is the honest equivalent, and branching here
            # rather than at the call sites means Space, the deck's play button
            # and the API's resume route all get it without special-casing
            # (KAMP-678). The lock is an RLock, so re-entering play() is safe.
            parked = self._state["parked_item_id"]
            if parked is not None and self._state["state"] == IDLE:
                return self.play(int(parked), self._state["parked_track_num"])
            if self._engine is None or self._state["state"] != PAUSED:
                return self.snapshot()
            self._take_over_from_main()
            self._engine.resume()
            self._playing_since = self._now()
            return self._publish(state=PLAYING)

    def toggle(self) -> dict[str, Any]:
        return self.pause() if self._state["state"] == PLAYING else self.resume()

    def stop(self) -> dict[str, Any]:
        """End the preview and give the main player back."""
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.unload()
                except Exception:  # noqa: BLE001
                    logger.warning("preview: unload failed", exc_info=True)
            self._record_listened()
            self._hand_back_to_main()
            self._arm_idle_timer()
            return self._publish(
                state=IDLE,
                item_id=None,
                track_num=None,
                title="",
                position=0.0,
                duration=0.0,
                buffering=False,
                tracks=[],
                error=None,
                # Stop is the deliberate "take it off the deck" gesture, unlike
                # release_for_main which only cues it (KAMP-678).
                parked_item_id=None,
                parked_track_num=None,
            )

    def seek(self, position: float) -> dict[str, Any]:
        with self._lock:
            if self._engine is None or self._state["state"] == IDLE:
                return self.snapshot()
            self._engine.seek(max(0.0, position))
            return self._publish(position=max(0.0, position))

    def step(self, delta: int) -> dict[str, Any]:
        """Move *delta* tracks within the album currently previewing."""
        with self._lock:
            item_id = self._state["item_id"]
            current = self._state["track_num"]
            if item_id is None or current is None:
                return self.snapshot()
            tracks = self._tracks.get(int(item_id)) or []
            nums = [t.track_num for t in tracks]
            if current not in nums:
                return self.snapshot()
            target = nums.index(current) + delta
            if not 0 <= target < len(nums):
                # Off either end is a stop, not a wrap: a preview is a listen
                # through one record, not a loop.
                return self.stop()
            return self.play(int(item_id), nums[target])

    # ------------------------------------------------------------------
    # Main-engine handoff
    # ------------------------------------------------------------------

    def _take_over_from_main(self) -> None:
        """Pause the main player if it is playing, remembering that it was."""
        # Captured before the call: pause() only sends a script-message and the
        # Lua schedules the real pause ~0.4s later, so reading state.playing
        # afterwards would still say True.
        if self._main_was_playing:
            return  # already holding the floor
        if self._main.state.playing:
            self._main_was_playing = True
            self._main.pause()

    def _hand_back_to_main(self) -> None:
        if self._main_was_playing:
            self._main_was_playing = False
            self._main.resume()

    def release_for_main(self) -> None:
        """Cue the record and hand the floor to the main transport.

        The main transport always wins. Called from the player endpoints, so it
        must not resume main afterwards -- main is about to do whatever the user
        just asked for.

        The record stays ON the deck (KAMP-678) rather than flying back to the
        crate: pressing play on your own queue says nothing about the record you
        were listening to. `title` and `tracks` ride along so the deck can keep
        naming it.
        """
        with self._lock:
            item_id = self._state["item_id"]
            if item_id is None:
                # Nothing live to release -- never previewed, already stopped, or
                # already cued. Without this the middleware fires on EVERY
                # subsequent transport press, each one re-publishing and
                # clobbering the cued record with None.
                return
            if self._engine is not None:
                try:
                    self._engine.unload()
                except Exception:  # noqa: BLE001
                    pass
            self._record_listened()
            self._main_was_playing = False
            self._arm_idle_timer()
            self._publish(
                state=IDLE,
                item_id=None,
                track_num=None,
                parked_item_id=item_id,
                parked_track_num=self._state["track_num"],
                # Zeroed deliberately: the engine is unloaded and the deck's play
                # button replays from the top, so a retained position would paint
                # a playhead partway through a track that is about to restart.
                position=0.0,
                position_updated_at=0.0,
                buffering=False,
                error=None,
            )

    # ------------------------------------------------------------------
    # Engine callbacks — preview-local, touching nothing the main player owns
    # ------------------------------------------------------------------

    def _on_file_loaded(self) -> None:
        self._publish(buffering=False)

    def _on_track_end(self, _had_lookahead: bool) -> None:
        # Advance within the album; stepping past the last track stops.
        try:
            self.step(1)
        except Exception:  # noqa: BLE001 - a callback must never kill the reader thread
            logger.warning("preview: advance failed", exc_info=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, item_id: int, item: dict[str, Any]) -> list[PreviewStream]:
        cached = self._tracks.get(item_id)
        if cached and not any(t.is_expired for t in cached):
            return cached

        source = self._source_factory()
        if source is None:
            return []
        candidate = Candidate(
            provider=str(item.get("provider") or "bandcamp"),
            provider_item_id=str(item.get("provider_item_id") or ""),
            item_url=str(item.get("item_url") or ""),
            artist=str(item.get("artist") or ""),
            title=str(item.get("title") or ""),
        )
        try:
            tracks = source.preview_tracks(candidate)
        except Exception as exc:  # noqa: BLE001
            # A 429 is worth telling the user about; everything else is just
            # "this one will not play".
            if type(exc).__name__ == "RateLimitedError":
                raise _RateLimited from exc
            logger.warning("preview: could not resolve %s", candidate.item_url)
            return []
        if tracks:
            self._tracks[item_id] = tracks
        return tracks

    @staticmethod
    def _pick(
        tracks: list[PreviewStream], track_num: int | None
    ) -> PreviewStream | None:
        if track_num is None:
            return tracks[0]
        for track in tracks:
            if track.track_num == track_num:
                return track
        return tracks[0]

    def _accumulate(self) -> None:
        if self._playing_since is not None:
            self._listened += max(0.0, self._now() - self._playing_since)
            self._playing_since = None

    def _record_listened(self) -> None:
        """Write one 'previewed' event for the item just finished with.

        One event per preview *session*, not per track: a five-track listen is
        one engagement, and KAMP-655 counting it as five would overstate the
        feature's own numbers.
        """
        self._accumulate()
        item_id = self._state.get("item_id")
        listened, self._listened = self._listened, 0.0
        if item_id is None or listened < MIN_PREVIEW_SECS:
            return
        try:
            self._index.record_discovery_event(
                int(item_id),
                "previewed",
                detail=json.dumps({"seconds": round(listened, 1)}),
            )
        except Exception:  # noqa: BLE001 - stats are not worth failing playback over
            logger.warning("preview: could not record the event", exc_info=True)


class _RateLimited(RuntimeError):
    """Internal: the origin rate-limited the album-page fetch."""
