import threading
import time

from dreamsync.cache import ShowCache, spotify_track_cache_id
from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.spotify.learned_live_session import SpotifyLearnedLiveSession
from dreamsync.spotify.models import PlaybackState, SpotifyTrack


def track(track_id="abc"):
    return SpotifyTrack(track_id, "Song", "Artist", "Album", 10_000, f"spotify:track:{track_id}")


def state(item, progress=0, playing=True):
    return PlaybackState(playing, item, progress, time.monotonic(), "Device", False, "off")


def timeline():
    return ShowTimeline(
        "song.mp3", 10.0, 120.0, 4, (0.0,), (0.0,),
        (ShowCue(0.0, "solid", ("#ffffff",), 1.0, 1.0, {}, "cut", 0),),
        {"track_name": "Song"},
    )


class Watcher:
    def __init__(self, item):
        self.item = item
        self.playback = state(item)
        self.track_callbacks = []
        self.state_callbacks = []

    def snapshot(self):
        return {"current_track": self.item, "playback_state": self.playback}

    def _subscribe(self, callbacks, callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback) if callback in callbacks else None

    def subscribe_track_changed(self, callback):
        return self._subscribe(self.track_callbacks, callback)

    def subscribe_playback_state(self, callback):
        return self._subscribe(self.state_callbacks, callback)

    def change(self, item):
        old = self.item
        self.item = item
        self.playback = state(item)
        for callback in tuple(self.track_callbacks):
            callback(item, old)

    def update(self, playback):
        self.playback = playback
        for callback in tuple(self.state_callbacks):
            callback(playback)


def run_session(session):
    stop = threading.Event()
    thread = threading.Thread(target=session.run, args=(stop,))
    thread.start()
    time.sleep(0.03)
    return stop, thread


def test_cache_hit_uses_compiled_without_reactive_or_capture(tmp_path):
    cache = ShowCache(tmp_path)
    cache.put(spotify_track_cache_id("abc"), timeline())
    calls = []
    session = SpotifyLearnedLiveSession(
        Watcher(track()), cache=cache,
        start_compiled=lambda *_: calls.append("compiled"),
        start_reactive=lambda: calls.append("reactive"),
        stop_output=lambda: calls.append("stop"),
        start_capture=lambda *_: calls.append("capture"),
    )
    stop, thread = run_session(session)
    stop.set(); thread.join()
    assert calls[:1] == ["compiled"]
    assert "reactive" not in calls and "capture" not in calls


def test_cache_miss_is_reactive_and_learning_then_next_play_hits(tmp_path):
    cache = ShowCache(tmp_path)
    watcher = Watcher(track())
    calls = []
    session = SpotifyLearnedLiveSession(
        watcher, cache=cache,
        start_compiled=lambda *_: calls.append("compiled"),
        start_reactive=lambda: calls.append("reactive"),
        stop_output=lambda: None,
        start_capture=lambda *_: calls.append("capture"),
    )
    stop, thread = run_session(session)
    assert calls[:2] == ["reactive", "capture"]
    cache.put(spotify_track_cache_id("abc"), timeline())
    watcher.change(track("other"))
    watcher.change(track("abc"))
    assert calls[-1] == "compiled"
    stop.set(); thread.join()


def test_pause_invalidates_active_learning_capture(tmp_path):
    watcher = Watcher(track())
    invalidations = []
    session = SpotifyLearnedLiveSession(
        watcher, cache=ShowCache(tmp_path),
        start_compiled=lambda *_: None,
        start_reactive=lambda: None,
        stop_output=lambda: None,
        start_capture=lambda *_: None,
        invalidate_capture=invalidations.append,
    )
    stop, thread = run_session(session)
    watcher.update(state(track(), progress=1000, playing=False))
    assert invalidations == ["paused"]
    assert session.snapshot().learning_reason == "paused"
    stop.set(); thread.join()


def test_background_job_state_does_not_replace_active_learning_badge(tmp_path):
    watcher = Watcher(track())
    session = SpotifyLearnedLiveSession(
        watcher, cache=ShowCache(tmp_path),
        start_compiled=lambda *_: None,
        start_reactive=lambda: None,
        stop_output=lambda: None,
        start_capture=lambda *_: None,
    )
    stop, thread = run_session(session)
    session.notify_learning_state("previous", "analyzing")
    snapshot = session.snapshot()
    assert snapshot.active_learning_state == "capturing"
    assert snapshot.background_jobs == (("previous", "analyzing", ""),)
    stop.set(); thread.join()
