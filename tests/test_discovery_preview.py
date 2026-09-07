"""Crate preview playback tests (KAMP-651).

The whole feature is a promise about what it does *not* touch, so most of these
assert absences: the queue, the scrobbler, track_stats, the persisted session and
the main VU meter must all be untouched, and the main player must come back
exactly as it was left.

No real mpv anywhere — the engine is a fake with the same surface.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

from kamp_core.library import LibraryIndex
from kamp_core.playback import PlaybackState
from kamp_daemon.discovery import Candidate, PreviewStream
from kamp_daemon.discovery_preview import (
    IDLE,
    MIN_PREVIEW_SECS,
    PAUSED,
    PLAYING,
    PreviewPlayer,
)


@pytest.fixture
def index(tmp_path: Path) -> Iterator[LibraryIndex]:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


class FakeEngine:
    """Only the surface PreviewPlayer uses."""

    def __init__(self) -> None:
        self.state = PlaybackState()
        self.on_track_end: Any = None
        self.on_file_loaded: Any = None
        self.on_play_state_changed: Any = None
        self.on_audio_level: Any = None
        self.played: list[str] = []
        self.calls: list[str] = []
        self.shutdown_count = 0
        # The reason mpv gave for the last file ending, exactly as the real engine
        # records it. Without this the fake could not tell a track that finished
        # from one that failed — which is why nothing caught KAMP-673: the two are
        # the same callback and the fake never fired it at all.
        self.last_end_reason: str | None = None

    def play(self, path: str) -> None:
        self.played.append(str(path))
        self.calls.append("play")
        self.state.playing = True

    def finish(self) -> None:
        """The file played to its end — mpv's `end-file reason=eof`."""
        self._end("eof")

    def fail(self) -> None:
        """The file would not open or buffer — a dead CDN URL, a 403, a 410.

        mpv reports this as `end-file reason=error`, and the engine funnels it
        into the SAME on_track_end callback as a clean finish (playback.py). That
        conflation is the bug: a preview whose signed URL expired mid-listen looks
        exactly like a record that ended.
        """
        self._end("error")

    def _end(self, reason: str) -> None:
        self.state.playing = False
        self.last_end_reason = reason
        if self.on_track_end is not None:
            self.on_track_end(False)

    def pause(self) -> None:
        self.calls.append("pause")
        # Deliberately does NOT flip state.playing yet: the real pause() only
        # sends a script-message, and kamp_fade.lua applies the real pause about
        # 0.4s later. state.playing follows mpv's observed property, so it lags.
        self._pending_pause = True

    def settle(self) -> None:
        """Land a scheduled fade — what the real engine does ~0.4s after pause()."""
        if getattr(self, "_pending_pause", False):
            self._pending_pause = False
            self.state.playing = False

    def resume(self) -> None:
        self.calls.append("resume")
        self._pending_pause = False
        self.state.playing = True

    def unload(self) -> None:
        self.calls.append("unload")
        self.state.playing = False

    def seek(self, position: float) -> None:
        # Deliberately does NOT move state.position, and neither does play()
        # above. mpv reports position asynchronously on the IPC reader thread, so
        # a real engine is stale for a full round trip after either call.
        #
        # Keeping the fake hostile is what gives the tests below their teeth
        # (KAMP-686). A fake that helpfully wrote the target would make them pass
        # against a daemon that still re-pulled the stale value — they would be
        # asserting the fake, not the product. This is the same shape as the fake
        # that never fired on_track_end and so could not see KAMP-673.
        self.calls.append(f"seek:{position}")

    def shutdown(self) -> None:
        self.shutdown_count += 1

    @property
    def volume(self) -> int:
        return self.state.volume

    @volume.setter
    def volume(self, value: int) -> None:
        self.state.volume = value

    @property
    def muted(self) -> bool:
        return self.state.muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self.state.muted = value


class FakeSource:
    def __init__(self, tracks: list[PreviewStream] | None = None, error: Any = None):
        self.tracks = tracks if tracks is not None else [_stream(1), _stream(2)]
        self.error = error
        self.calls = 0

    def preview_tracks(self, candidate: Candidate) -> list[PreviewStream]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.tracks)


class RateLimitedError(RuntimeError):
    """Name-matched by PreviewPlayer without importing the source module."""


def _stream(num: int, expires_at: float = 4_000_000_000.0) -> PreviewStream:
    return PreviewStream(
        url=f"https://cdn/{num}.mp3",
        track_num=num,
        title=f"Track {num}",
        duration=100.0,
        expires_at=expires_at,
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _item(index: LibraryIndex, item_id: str = "1") -> int:
    row = index.add_discovery_candidate(
        provider="bandcamp",
        provider_item_id=item_id,
        item_url="https://a.bandcamp.com/album/x",
        artist="Band",
        title="Album",
    )
    # Next free slot — two items cannot share one (the partial unique index).
    position = index._conn.execute(
        "SELECT COUNT(*) AS c FROM discovery_items WHERE crate_no = 1"
    ).fetchone()["c"]
    index.place_in_crate(row, 1, position)
    return row


class Harness:
    def __init__(
        self,
        index: LibraryIndex,
        source: FakeSource | None = None,
        main_playing: bool = False,
        check_url: Any = None,
        fade_secs: float = 0.01,
    ) -> None:
        self.index = index
        self.main = FakeEngine()
        self.main.state.playing = main_playing
        self.engines: list[FakeEngine] = []
        self.events: list[dict[str, Any]] = []
        self.source = source if source is not None else FakeSource()
        self.clock = _Clock()

        def _factory() -> FakeEngine:
            # Real construction blocks in _ipc.open(timeout=5.0) while mpv comes
            # up. Modelling that is what makes the concurrency test meaningful:
            # an instant factory never lets the GIL switch inside the
            # check-then-build, so the test would pass with no lock at all.
            import time

            time.sleep(0.02)
            engine = FakeEngine()
            self.engines.append(engine)
            return engine

        self.player = PreviewPlayer(
            index,
            main_engine=self.main,  # type: ignore[arg-type]
            engine_factory=_factory,  # type: ignore[arg-type]
            source_factory=lambda: self.source,  # type: ignore[arg-type,return-value]
            notify=self.events.append,
            idle_timeout=0.05,
            now=self.clock,
            # Default None, so every pre-existing test keeps its exact behaviour
            # and no test reaches the network (KAMP-673).
            check_url=check_url,
            # How long a fading stop waits before unloading (KAMP-693). Short by
            # default so the suite does not sleep through real fades; a test that
            # needs the fade still to be in flight sets it long instead.
            fade_secs=fade_secs,
        )

    @property
    def engine(self) -> FakeEngine:
        return self.engines[-1]


# ---------------------------------------------------------------------------
# Isolation — the whole point of the story
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_the_preview_engine_gets_no_main_callbacks(
        self, index: LibraryIndex
    ) -> None:
        """on_play_state_changed drives the main player's WS state and the OS
        widget; on_audio_level drives the main VU meter. Neither should ever
        hear from the preview engine."""
        h = Harness(index)
        h.player.play(_item(index))
        assert h.engine.on_play_state_changed is None
        assert h.engine.on_audio_level is None
        # These two are preview-local and deliberately wired.
        assert h.engine.on_track_end is not None
        assert h.engine.on_file_loaded is not None

    def test_preview_never_touches_track_stats_or_the_queue(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        item = _item(index)
        h.player.play(item)
        h.clock.t += 60
        h.player.stop()

        assert (
            index._conn.execute("SELECT COUNT(*) AS c FROM track_stats").fetchone()["c"]
            == 0
        )
        assert not index.load_queue_state()

    def test_the_main_engine_is_never_asked_to_play(self, index: LibraryIndex) -> None:
        """Preview audio must come out of the second engine, always."""
        h = Harness(index, main_playing=True)
        h.player.play(_item(index))
        assert h.main.played == []
        assert h.engine.played == ["https://cdn/1.mp3"]


# ---------------------------------------------------------------------------
# The handoff
# ---------------------------------------------------------------------------


class TestHandoff:
    def test_a_playing_queue_is_paused_and_resumed(self, index: LibraryIndex) -> None:
        h = Harness(index, main_playing=True)
        h.player.play(_item(index))
        assert "pause" in h.main.calls

        h.player.stop()
        assert "resume" in h.main.calls

    def test_a_paused_queue_is_left_alone(self, index: LibraryIndex) -> None:
        """Resuming a queue the user had already paused would be kamp starting
        playback nobody asked for."""
        h = Harness(index, main_playing=False)
        h.player.play(_item(index))
        h.player.stop()
        assert h.main.calls == []

    def test_the_flag_is_captured_before_the_fade(self, index: LibraryIndex) -> None:
        """pause() only sends a script-message; the Lua applies the real pause
        ~0.4s later, so state.playing still reads True right after the call.
        Reading it back instead of capturing it first would strand main paused."""
        h = Harness(index, main_playing=True)
        h.player.play(_item(index))
        # Immediately after the call the fade has not landed yet.
        assert h.main.state.playing is True
        # ...and now it has, which is the state any real stop() sees.
        h.main.settle()
        assert h.main.state.playing is False

        h.player.stop()
        assert h.main.calls.count("resume") == 1

    def test_switching_records_does_not_double_pause(self, index: LibraryIndex) -> None:
        h = Harness(index, main_playing=True)
        first, second = _item(index, "1"), _item(index, "2")
        h.player.play(first)
        h.player.play(second)
        assert h.main.calls.count("pause") == 1

    def test_the_main_transport_wins_and_is_not_resumed(
        self, index: LibraryIndex
    ) -> None:
        """release_for_main must not resume: main is about to do whatever the
        user just pressed, and resuming first would fight it."""
        h = Harness(index, main_playing=True)
        h.player.play(_item(index))
        h.main.calls.clear()

        h.player.release_for_main()
        assert "resume" not in h.main.calls
        assert h.player.snapshot()["state"] == IDLE


# ---------------------------------------------------------------------------
# Parking (KAMP-678)
# ---------------------------------------------------------------------------


class TestParking:
    """Pressing play on your own queue cues the crate record rather than
    shelving it. Carried as metadata on IDLE rather than as a fifth state, so
    every guard that reads `state` to ask "is the engine busy" keeps working."""

    def test_the_cued_record_stays_readable(self, index: LibraryIndex) -> None:
        h = Harness(index, main_playing=True)
        item = _item(index)
        h.player.play(item)

        h.player.release_for_main()
        snap = h.player.snapshot()
        # Idle to every engine guard...
        assert snap["state"] == IDLE
        assert snap["item_id"] is None
        # ...but the deck can still name what is on it.
        assert snap["parked_item_id"] == item

    def test_the_engine_is_still_reaped_while_a_record_is_cued(
        self, index: LibraryIndex
    ) -> None:
        """The whole reason parking is metadata and not a state. _idle_kill
        returns on any non-IDLE state AND clears its timer without re-arming, so
        a fifth state would pin an mpv process and the audio device open for the
        rest of the session — and the existing teardown test drives stop(), so
        it would not have noticed."""
        h = Harness(index)
        h.player.play(_item(index))

        h.player.release_for_main()
        assert _wait_for(lambda: h.engines[0].shutdown_count == 1), "engine not reaped"
        # And the record is still on the deck after the process is gone.
        assert h.player.snapshot()["parked_item_id"] is not None

    def test_a_second_transport_press_does_not_clobber_the_cued_record(
        self, index: LibraryIndex
    ) -> None:
        """The middleware fires on every non-volume player POST, so this runs
        once per button press for as long as the record stays cued."""
        h = Harness(index, main_playing=True)
        item = _item(index)
        h.player.play(item)

        h.player.release_for_main()
        h.player.release_for_main()
        assert h.player.snapshot()["parked_item_id"] == item

    def test_resume_replays_a_cued_record_and_retakes_the_floor(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index, main_playing=True)
        item = _item(index)
        h.player.play(item)
        h.player.release_for_main()
        # The user's queue has the floor now.
        h.main.settle()
        h.main.state.playing = True
        h.main.calls.clear()

        snap = h.player.resume()
        assert snap["state"] == PLAYING
        assert snap["item_id"] == item
        assert snap["parked_item_id"] is None
        assert "pause" in h.main.calls

    def test_stop_takes_the_record_off_the_deck(self, index: LibraryIndex) -> None:
        """Stop is the deliberate take-it-off gesture; parking is not."""
        h = Harness(index)
        h.player.play(_item(index))
        h.player.release_for_main()

        h.player.stop()
        assert h.player.snapshot()["parked_item_id"] is None

    def test_a_failed_play_does_not_strand_the_cued_record(
        self, index: LibraryIndex
    ) -> None:
        """Every failure below the PREPARING publish returns IDLE. Without
        clearing the cued id up front, the PREVIOUS record would sit on the deck
        as though the one that just failed had never been asked for."""
        h = Harness(index, main_playing=True)
        h.player.play(_item(index, "1"))
        h.player.release_for_main()
        assert h.player.snapshot()["parked_item_id"] is not None

        h.source.tracks = []
        snap = h.player.play(_item(index, "2"))
        assert snap["error"] == "unavailable"
        assert snap["parked_item_id"] is None

    def test_releasing_with_nothing_live_does_nothing(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        h.player.release_for_main()
        assert h.player.snapshot()["parked_item_id"] is None
        assert h.events == []


# ---------------------------------------------------------------------------
# Audio settings
# ---------------------------------------------------------------------------


class TestAudioSettings:
    def test_volume_is_mirrored_at_spawn(self, index: LibraryIndex) -> None:
        """A fresh engine defaults to 100 with nothing on the command line, so
        without this someone listening at 30 gets a preview at full blast."""
        h = Harness(index)
        h.main.state.volume = 30
        h.player.play(_item(index))
        assert h.engine.volume == 30

    def test_mute_is_mirrored_at_spawn(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.main.state.muted = True
        h.player.play(_item(index))
        assert h.engine.muted is True

    def test_a_volume_change_mid_preview_follows(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        h.player.set_volume(12)
        assert h.engine.volume == 12

    def test_volume_changes_are_harmless_with_no_engine(
        self, index: LibraryIndex
    ) -> None:
        Harness(index).player.set_volume(50)  # must not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_the_engine_is_not_spawned_until_the_first_preview(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        assert h.engines == []
        h.player.play(_item(index))
        assert len(h.engines) == 1

    def test_concurrent_first_previews_build_exactly_one_engine(
        self, index: LibraryIndex
    ) -> None:
        """Construction spawns mpv synchronously, so a race would leave an
        orphan process nothing holds a reference to — and on POSIX there is no
        Job Object to reap it."""
        h = Harness(index)
        item = _item(index)
        barrier = threading.Barrier(4)

        def _go() -> None:
            barrier.wait()
            h.player.play(item)

        threads = [threading.Thread(target=_go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(h.engines) == 1

    def test_idle_kill_tears_down_and_a_later_play_respawns(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        item = _item(index)
        h.player.play(item)
        h.player.stop()

        deadline = _wait_for(lambda: h.engines[0].shutdown_count == 1)
        assert deadline, "the idle timer never fired"

        h.player.play(item)
        assert len(h.engines) == 2

    def test_a_playing_preview_is_not_idle_killed(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        import time

        time.sleep(0.15)
        assert h.engines[0].shutdown_count == 0

    def test_shutdown_is_idempotent(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        h.player.shutdown()
        h.player.shutdown()
        assert h.engines[0].shutdown_count == 1


def _wait_for(pred: Any, timeout: float = 2.0) -> bool:
    import time

    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Transport and track stepping
# ---------------------------------------------------------------------------


class TestTransport:
    def test_the_track_list_is_published_for_the_card(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        state = h.player.play(_item(index))
        assert [t["track_num"] for t in state["tracks"]] == [1, 2]
        assert state["title"] == "Track 1"

    def test_stepping_costs_no_extra_fetch(self, index: LibraryIndex) -> None:
        """One album-page fetch buys the whole record; next/prev must not each
        cost another request against the class that rate-limits hardest."""
        h = Harness(index)
        item = _item(index)
        h.player.play(item)
        h.player.step(1)
        h.player.step(-1)
        assert h.source.calls == 1

    def test_stepping_past_the_end_stops(self, index: LibraryIndex) -> None:
        """A preview is a listen through one record, not a loop."""
        h = Harness(index)
        h.player.play(_item(index), track_num=2)
        assert h.player.step(1)["state"] == IDLE

    def test_pause_and_resume(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        assert h.player.pause()["state"] == PAUSED
        assert h.player.resume()["state"] == PLAYING

    def test_stop_is_a_hard_cut_by_default(self, index: LibraryIndex) -> None:
        """Escape and the deck's own stop stay immediate: they are answers to "get
        off", and a fade there is a delay, not a courtesy."""
        h = Harness(index)
        h.player.play(_item(index))
        assert h.player.stop()["state"] == IDLE
        assert "unload" in h.engine.calls

    def test_a_fading_stop_rides_the_pause_fade_out(self, index: LibraryIndex) -> None:
        """KAMP-693 wants the record faded, not cut.

        unload() is mpv's raw `stop` — it drops the audio on the frame it
        arrives. The only per-sample fade in the engine is the Lua one behind
        pause(), so a faded stop is that fade followed by the unload, rather than
        a second ramp written next to it.
        """
        h = Harness(index)
        h.player.play(_item(index))
        h.player.stop(fade=True)
        assert "pause" in h.engine.calls
        assert "unload" not in h.engine.calls

    def test_a_fading_stop_still_unloads_once_the_fade_is_over(
        self, index: LibraryIndex
    ) -> None:
        """The fade only silences it. Without the unload behind it the engine
        keeps the file loaded and the audio device open, which is the pinned-mpv
        bug _idle_kill exists to prevent."""
        h = Harness(index, fade_secs=0.01)
        h.player.play(_item(index))
        h.player.stop(fade=True)
        assert _wait_for(lambda: "unload" in h.engine.calls)

    def test_a_fading_stop_reports_idle_at_once(self, index: LibraryIndex) -> None:
        """The UI must not wait on the fade. Holding the snapshot back would read
        as a hang on the one gesture that is supposed to feel like letting go —
        and the deck clearing while the last moment fades out IS the fade."""
        h = Harness(index, fade_secs=30.0)
        h.player.play(_item(index))
        assert h.player.stop(fade=True)["state"] == IDLE

    def test_a_record_played_after_a_fading_stop_is_not_cut_off_by_it(
        self, index: LibraryIndex
    ) -> None:
        """The deferred unload is the hazard this whole path introduces: it fires
        on a timer, and by then the user may have put something else on. Unloading
        then would kill a record they had just started, seconds after an action
        they had forgotten about."""
        h = Harness(index, fade_secs=0.01)
        h.player.play(_item(index))
        h.player.stop(fade=True)
        h.player.play(_item(index, "2"))
        # Long enough for the deferred unload to have fired if it were going to.
        assert not _wait_for(lambda: "unload" in h.engine.calls, timeout=0.2)
        assert h.engine.state.playing is True

    def test_an_expired_cache_is_refetched(self, index: LibraryIndex) -> None:
        """Signed URLs die after about a day; a stale one would fail silently at
        the point of pressing play."""
        h = Harness(index, source=FakeSource([_stream(1, expires_at=1.0)]))
        item = _item(index)
        h.player.play(item)
        h.player.play(item)
        assert h.source.calls == 2

    def test_a_live_cache_is_reused(self, index: LibraryIndex) -> None:
        h = Harness(index)
        item = _item(index)
        h.player.play(item)
        h.player.play(item)
        assert h.source.calls == 1


class TestThePublishedPosition:
    """KAMP-686: the deck's bar was always one seek behind.

    snapshot() re-pulls position from the engine, which reports asynchronously —
    so every publish carried the value from BEFORE whatever just happened. The
    main player hides this by rebuilding on a 4 Hz ping; the preview publishes on
    transitions only and the strip interpolates from the anchor it was given, so
    a wrong anchor is permanent.

    Every test here holds the engine at a stale position on purpose. That is the
    real engine's behaviour, not a contrivance.
    """

    def test_seeking_publishes_the_target_not_the_engines_stale_position(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        h.engine.state.position = 5.0
        assert h.player.seek(90.0)["position"] == 90.0

    def test_seeking_a_paused_preview_publishes_the_target_and_stays_paused(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        h.player.pause()
        h.engine.settle()
        h.engine.state.position = 5.0
        state = h.player.seek(90.0)
        assert state["position"] == 90.0
        assert state["state"] == PAUSED

    def test_a_published_seek_carries_a_fresh_anchor(self, index: LibraryIndex) -> None:
        """The strip extrapolates `now - position_updated_at` from the last
        sample, so the anchor has to travel with the position it anchors. Left
        at the previous publish's stamp, a seek would read as the target PLUS
        however long the record had been playing."""
        h = Harness(index)
        h.player.play(_item(index))
        h.clock.t += 45  # plays on, publishing nothing — transitions only
        assert h.player.seek(90.0)["position_updated_at"] == h.clock.t

    def test_a_negative_seek_publishes_the_start(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        h.engine.state.position = 5.0
        assert h.player.seek(-10.0)["position"] == 0.0

    def test_starting_a_track_publishes_zero_not_the_last_position(
        self, index: LibraryIndex
    ) -> None:
        """The same bug at the other end of the record. Stepping to the next
        track published the position the previous one had reached, so the bar
        opened part-way through and interpolated on from there."""
        h = Harness(index)
        h.player.play(_item(index))
        h.engine.state.position = 60.0
        assert h.player.step(1)["position"] == 0.0

    def test_pausing_still_reports_the_engines_position(
        self, index: LibraryIndex
    ) -> None:
        """The rule is narrow on purpose: only a caller that SUPPLIES a position
        overrides the pull. pause names no position, so the engine stays the
        authority — as it must, since nothing else knows how far the record got.
        """
        h = Harness(index)
        h.player.play(_item(index))
        h.engine.state.position = 42.0
        assert h.player.pause()["position"] == 42.0

    def test_the_plain_snapshot_still_reports_the_engines_position(
        self, index: LibraryIndex
    ) -> None:
        """The GET route reads through snapshot() with no publish in sight; it
        must keep pulling, or a reconnecting client would be told 0:00."""
        h = Harness(index)
        h.player.play(_item(index))
        h.engine.state.position = 42.0
        assert h.player.snapshot()["position"] == 42.0


class TestAStaleStream:
    """KAMP-673: a signed URL that died while the record sat on the deck.

    Nothing here could be written before FakeEngine could end a file — it never
    fired on_track_end at all, which is why a playback failure being read as a
    clean finish went unnoticed through six tickets in this module.
    """

    def test_a_track_that_finishes_moves_on(self, index: LibraryIndex) -> None:
        """The behaviour that must NOT change. An album plays through."""
        h = Harness(index)
        h.player.play(_item(index), track_num=1)
        h.engine.finish()
        assert h.player.snapshot()["track_num"] == 2

    def test_a_track_that_fails_is_retried_not_skipped(
        self, index: LibraryIndex
    ) -> None:
        """The bug. A dead URL mid-album looked exactly like a record ending, so
        the deck silently jumped a track the user had asked to hear."""
        h = Harness(index)
        h.player.play(_item(index), track_num=1)
        h.engine.fail()
        assert h.player.snapshot()["track_num"] == 1, "skipped instead of retrying"
        assert h.source.calls == 2, "retried without re-signing the URL"

    def test_a_failure_that_repeats_gives_up_and_says_so(
        self, index: LibraryIndex
    ) -> None:
        """Bounded, because an unbounded retry's natural terminator is a 429 on
        album pages — which is account-wide and cascades into the download queue.
        One retry, then an honest answer."""
        h = Harness(index)
        h.player.play(_item(index), track_num=1)
        h.engine.fail()
        h.engine.fail()
        assert h.player.snapshot()["error"] == "expired"
        assert h.source.calls == 2, "kept refetching after giving up"

    def test_the_error_survives_the_last_track(self, index: LibraryIndex) -> None:
        """stop() publishes error=None, so a failure that routed through it would
        have its own message wiped in the same call that set it — which is the
        silent vanish the report describes."""
        h = Harness(index, source=FakeSource([_stream(1)]))
        h.player.play(_item(index), track_num=1)
        h.engine.fail()
        h.engine.fail()
        assert h.player.snapshot()["error"] == "expired"

    def test_a_dead_link_is_caught_before_mpv_sees_it(
        self, index: LibraryIndex
    ) -> None:
        """expires_at is a guess, and the library path already learned it is not
        enough — a signed token can be invalidated early when Bandcamp rotates a
        session key, so a URL is dead while is_expired still says fine."""
        seen: list[str] = []

        def check(url: str) -> int:
            seen.append(url)
            return 410 if len(seen) == 1 else 200

        h = Harness(index, check_url=check)
        h.player.play(_item(index))
        assert h.source.calls == 2, "trusted the clock over the CDN"
        assert h.engine.played, "gave up instead of re-signing"

    def test_a_live_link_is_played_without_a_refetch(self, index: LibraryIndex) -> None:
        """The check must not cost a fetch when it passes."""
        h = Harness(index, check_url=lambda _url: 200)
        h.player.play(_item(index))
        assert h.source.calls == 1

    def test_an_unreachable_check_leaves_the_record_alone(
        self, index: LibraryIndex
    ) -> None:
        """check_stream_url returns 0 on a network failure, and only a 4xx is a
        verdict. A blocked or slow check must not drop a playable record."""
        h = Harness(index, check_url=lambda _url: 0)
        h.player.play(_item(index))
        assert h.source.calls == 1
        assert h.engine.played

    def test_resuming_a_stale_pause_replays_it(self, index: LibraryIndex) -> None:
        """The reported repro. Nothing reaps a paused preview — the idle timer
        only fires from stop() — so mpv holds the socket all night and the signed
        URL behind it dies. Every other route back into playback goes through
        play() and re-signs; this one told mpv to carry on regardless."""
        h = Harness(index, source=FakeSource([_stream(1, expires_at=1.0)]))
        h.player.play(_item(index))
        h.player.pause()
        h.engine.settle()
        assert h.player.resume()["state"] == PLAYING
        assert h.source.calls == 2, "resumed a dead socket instead of re-signing"
        assert h.engine.calls.count("play") == 2, "resumed rather than replayed"

    def test_resuming_a_live_pause_just_resumes(self, index: LibraryIndex) -> None:
        """The common case must not pay for the rare one: a pause of a few
        seconds resumes where it was, with no album-page fetch and no restart."""
        h = Harness(index)
        h.player.play(_item(index))
        h.player.pause()
        h.engine.settle()
        assert h.player.resume()["state"] == PLAYING
        assert h.source.calls == 1
        assert "resume" in h.engine.calls

    def test_a_recovered_track_may_fail_again_later(self, index: LibraryIndex) -> None:
        """The retry budget is per attempt, not per session. Clearing it on a
        confirmed load is what stops one bad afternoon disabling the retry for
        every record after it."""
        h = Harness(index)
        item = _item(index)
        h.player.play(item, track_num=1)
        h.engine.fail()
        h.engine.on_file_loaded()  # mpv confirms the retry actually opened
        h.engine.fail()
        assert h.player.snapshot()["track_num"] == 1
        assert h.player.snapshot()["error"] is None


class TestFailures:
    def test_an_unknown_item_reports_not_found(self, index: LibraryIndex) -> None:
        assert Harness(index).player.play(9999)["error"] == "not_found"

    def test_an_album_with_no_audio_reports_unavailable(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index, source=FakeSource(tracks=[]))
        assert h.player.play(_item(index))["error"] == "unavailable"

    def test_a_rate_limit_is_distinguishable(self, index: LibraryIndex) -> None:
        """ "Bandcamp asked us to slow down" and "this album will not play" want
        different words on screen."""
        h = Harness(index, source=FakeSource(error=RateLimitedError("429")))
        assert h.player.play(_item(index))["error"] == "rate_limited"

    def test_a_broken_source_does_not_raise(self, index: LibraryIndex) -> None:
        h = Harness(index, source=FakeSource(error=ValueError("boom")))
        assert h.player.play(_item(index))["error"] == "unavailable"


# ---------------------------------------------------------------------------
# Engagement events
# ---------------------------------------------------------------------------


class TestPreviewedEvents:
    def _events(self, index: LibraryIndex) -> list[Any]:
        return index._conn.execute(
            "SELECT kind, detail FROM discovery_events WHERE kind = 'previewed'"
        ).fetchall()

    def test_a_real_listen_is_recorded_once_with_its_seconds(
        self, index: LibraryIndex
    ) -> None:
        h = Harness(index)
        item = _item(index)
        h.player.play(item)
        h.clock.t += 45
        h.player.stop()

        rows = self._events(index)
        assert len(rows) == 1
        assert json.loads(rows[0]["detail"])["seconds"] == pytest.approx(45.0)

    def test_a_misclick_records_nothing(self, index: LibraryIndex) -> None:
        """The first 'previewed' event flips state off 'fresh' irreversibly, so
        a half-second slip must not relabel the card forever."""
        h = Harness(index)
        h.player.play(_item(index))
        h.clock.t += MIN_PREVIEW_SECS / 2
        h.player.stop()
        assert self._events(index) == []
        assert index.crate_items(1)[0]["state"] == "fresh"

    def test_a_multi_track_listen_is_one_event(self, index: LibraryIndex) -> None:
        """Per-track events would make KAMP-655 count one album as five."""
        h = Harness(index)
        item = _item(index)
        h.player.play(item)
        h.clock.t += 30
        h.player.step(1)
        h.clock.t += 30
        h.player.stop()
        assert len(self._events(index)) == 1

    def test_paused_time_is_not_counted(self, index: LibraryIndex) -> None:
        h = Harness(index)
        h.player.play(_item(index))
        h.clock.t += 20
        h.player.pause()
        h.clock.t += 600
        h.player.stop()
        assert json.loads(self._events(index)[0]["detail"])["seconds"] == pytest.approx(
            20.0
        )
