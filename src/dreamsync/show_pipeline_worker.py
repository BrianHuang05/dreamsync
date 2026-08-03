"""Background worker: analyze + compile MP3s as they're captured.

Ready tracks are pushed to a queue.Queue for the playback consumer.
"""

from __future__ import annotations

import queue
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from dreamsync.capture.eligibility import CaptureEligibilityValidator, SpotifyCaptureCandidate
from dreamsync.spotify.learned_track import LearnedTrackStore, Mp3RetentionPolicy


class ShowPipelineWorker:
    """Analyze + compile MP3s in a thread pool as they arrive from capture.

    Plug ``on_segment_saved`` into ``CaptureOrchestrator`` as the callback.
    Compiled (mp3_path, timeline) pairs are pushed to *ready_queue* for
    the ``ShowPlaybackConsumer``.
    """

    def __init__(
        self,
        cache,
        profile=None,
        sample_rate: int = 44100,
        max_workers: int = 2,
        ready_queue: queue.Queue | None = None,
        debug: bool = False,
        state_callback=None,
        learned_live: bool = False,
        retention_policy: Mp3RetentionPolicy | str = Mp3RetentionPolicy.KEEP_RECENT,
        retained_mp3_limit: int = 10,
        eligibility_validator: CaptureEligibilityValidator | None = None,
    ) -> None:
        self._cache = cache
        self._profile = profile
        self._sample_rate = sample_rate
        self._debug = debug
        self._ready_queue = ready_queue or queue.Queue()
        self._state_callback = state_callback
        self._learned_live = bool(learned_live)
        self._retention_policy = Mp3RetentionPolicy(retention_policy)
        self._retained_mp3_limit = max(0, int(retained_mp3_limit))
        self._eligibility_validator = eligibility_validator or CaptureEligibilityValidator()
        self._learned_store = LearnedTrackStore(cache) if self._learned_live else None

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="show-pipeline"
        )
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._processed: int = 0
        self._errors: list[tuple[Path, str]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_segment_saved(self, mp3_path: str, metadata: dict) -> None:
        """CaptureOrchestrator callback — submits analyze+compile to pool."""
        future = self._executor.submit(self._process, Path(mp3_path), metadata)
        with self._lock:
            self._futures.append(future)

    @property
    def ready_queue(self) -> queue.Queue:
        return self._ready_queue

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures if not f.done())

    def stats(self) -> dict:
        with self._lock:
            return {
                "processed": self._processed,
                "errors": len(self._errors),
                "pending": sum(1 for f in self._futures if not f.done()),
            }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process(self, mp3_path: Path, metadata: dict) -> None:
        from .analyzer.analyze import analyze_song
        from .cache import cached_compile_show, path_based_track_id, spotify_track_cache_id

        try:
            candidate = self._candidate_from_metadata(mp3_path, metadata)
            if self._learned_live:
                eligibility = self._eligibility_validator.validate(candidate)
                if not eligibility.eligible:
                    self._emit_state(
                        mp3_path, "not_learned",
                        error=ValueError(",".join(eligibility.reasons)),
                    )
                    self._delete_ineligible_source(mp3_path)
                    return
                assert self._learned_store is not None
                if self._learned_store.is_complete(candidate.track_id, self._profile):
                    timeline = self._learned_store.lookup(candidate.track_id, self._profile)
                    self._emit_state(mp3_path, "learned", timeline=timeline)
                    return
            self._emit_state(mp3_path, "analyzing")
            structure = analyze_song(mp3_path, sample_rate=self._sample_rate)
            if self._learned_live:
                assert self._learned_store is not None
                self._learned_store.write_analysis(candidate.track_id, structure)
            self._emit_state(mp3_path, "compiling")
            track_id = (
                spotify_track_cache_id(candidate.track_id)
                if self._learned_live else path_based_track_id(mp3_path)
            )
            timeline, _from_cache = cached_compile_show(
                structure, self._profile, cache=self._cache, track_id=track_id
            )
            if self._cache.get(track_id, self._profile) is None:
                raise RuntimeError("Compiled show failed reload validation")
            if self._learned_live:
                assert self._learned_store is not None
                self._learned_store.publish_manifest(candidate, self._profile)
                self._apply_retention(mp3_path)
            self._ready_queue.put((mp3_path, timeline))
            self._emit_state(
                mp3_path, "learned" if self._learned_live else "ready", timeline=timeline
            )
            with self._lock:
                self._processed += 1
            if self._debug:
                print(
                    f"[pipeline] Ready: {mp3_path.name} "
                    f"({len(timeline.cues)} cues)"
                )
        except Exception as exc:
            with self._lock:
                self._errors.append((mp3_path, str(exc)))
            self._emit_state(mp3_path, "failed", error=exc)
            if self._debug:
                print(f"[pipeline] Failed: {mp3_path.name}: {exc}")

    def _emit_state(self, mp3_path: Path, state: str, *, timeline=None, error: Exception | None = None) -> None:
        if self._state_callback is None:
            return
        self._state_callback(mp3_path, state, timeline=timeline, error=error)

    @staticmethod
    def _candidate_from_metadata(mp3_path: Path, metadata: dict) -> SpotifyCaptureCandidate:
        expected = float(metadata.get("expected_duration_seconds") or 0.0)
        captured = metadata.get("segment_duration_seconds")
        if captured is None:
            sample_rate = float(metadata.get("sample_rate") or 1.0)
            captured = (
                float(metadata.get("end_frame", 0)) - float(metadata.get("start_frame", 0))
            ) / sample_rate
        gaps = metadata.get("gaps") or ()
        return SpotifyCaptureCandidate(
            track_id=str(metadata.get("spotify_track_id") or ""),
            uri=str(metadata.get("spotify_uri") or ""),
            title=str(metadata.get("song_title") or ""),
            artist=str(metadata.get("artist") or ""),
            album=str(metadata.get("album") or ""),
            expected_duration_seconds=expected,
            observed_start_progress_seconds=float(metadata.get("observed_start_progress_seconds") or 0.0),
            observed_end_progress_seconds=float(
                metadata.get("observed_end_progress_seconds") or expected
            ),
            captured_duration_seconds=float(captured or 0.0),
            seek_detected=bool(metadata.get("seek_detected", False)),
            paused=bool(metadata.get("paused", False)),
            skipped=bool(metadata.get("skipped", False)),
            capture_gaps=len(gaps),
            capture_restarts=int(metadata.get("capture_restarts") or 0),
            audio_readable=mp3_path.is_file(),
            nontrivial_audio=mp3_path.is_file() and mp3_path.stat().st_size > 1024,
        )

    def _delete_ineligible_source(self, mp3_path: Path) -> None:
        if self._retention_policy is Mp3RetentionPolicy.DELETE_AFTER_VERIFIED_COMPILE:
            mp3_path.unlink(missing_ok=True)
            mp3_path.with_suffix(".json").unlink(missing_ok=True)

    def _apply_retention(self, mp3_path: Path) -> None:
        if self._retention_policy is Mp3RetentionPolicy.KEEP_ALL:
            return
        if self._retention_policy is Mp3RetentionPolicy.DELETE_AFTER_VERIFIED_COMPILE:
            targets = (mp3_path,)
        else:
            assert self._learned_store is not None
            mp3s = sorted(
                (
                    path for path in mp3_path.parent.glob("*.mp3")
                    if self._verified_learned_source(path)
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            targets = tuple(mp3s[self._retained_mp3_limit:])
        for target in targets:
            target.unlink(missing_ok=True)
            target.with_suffix(".json").unlink(missing_ok=True)

    def _verified_learned_source(self, mp3_path: Path) -> bool:
        if self._learned_store is None:
            return False
        sidecar = mp3_path.with_suffix(".json")
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        track_id = str(data.get("spotifyTrackId") or data.get("spotify_track_id") or "")
        return bool(track_id and self._learned_store.is_complete(track_id, self._profile))
