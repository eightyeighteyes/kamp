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

    def play(self, path: str) -> None:
        self.played.append(str(path))
        self.calls.append("play")
        self.state.playing = True

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
