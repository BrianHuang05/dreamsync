"""Tests for dreamsync.capture.boundary_queue — mutable boundary queue + safety margins."""

import threading

import pytest

from dreamsync.capture.boundary_queue import BoundaryEntry, BoundaryQueue


def _entry(fp, idx=0, meta=None):
    return BoundaryEntry(frame_position=fp, segment_index=idx, metadata=meta)


class TestBasicOperations:
    def test_add_maintains_sorted_order(self):
        q = BoundaryQueue()
        q.add(_entry(300))
        q.add(_entry(100))
        q.add(_entry(200))
        entries = q.entries()
        assert [e.frame_position for e in entries] == [100, 200, 300]

    def test_peek_next(self):
        q = BoundaryQueue()
        q.add(_entry(50))
        q.add(_entry(10))
        assert q.peek_next().frame_position == 10

    def test_peek_empty(self):
        q = BoundaryQueue()
        assert q.peek_next() is None

    def test_pop_next(self):
        q = BoundaryQueue()
        q.add(_entry(10))
        q.add(_entry(20))
        e = q.pop_next()
        assert e.frame_position == 10
        assert len(q) == 1

    def test_pop_empty(self):
        q = BoundaryQueue()
        assert q.pop_next() is None

    def test_len(self):
        q = BoundaryQueue()
        assert len(q) == 0
        q.add(_entry(100))
        assert len(q) == 1


class TestUpdate:
    def test_update_unlocked(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(500))
        ok = q.update(0, 600, current_frame=0)
        assert ok
        assert q.peek_next().frame_position == 600

    def test_update_locked_rejected(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(50))
        ok = q.update(0, 200, current_frame=0)
        assert not ok
        assert q.peek_next().frame_position == 50

    def test_update_invalid_index(self):
        q = BoundaryQueue()
        assert not q.update(5, 100, current_frame=0)


class TestRemove:
    def test_remove_unlocked(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(500))
        ok = q.remove(0, current_frame=0)
        assert ok
        assert len(q) == 0

    def test_remove_locked_rejected(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(50))
        ok = q.remove(0, current_frame=0)
        assert not ok
        assert len(q) == 1

    def test_remove_invalid_index(self):
        q = BoundaryQueue()
        assert not q.remove(0, current_frame=0)


class TestSafetyMargin:
    def test_boundary_within_margin_is_locked(self):
        q = BoundaryQueue(safety_margin_frames=24000)
        q.add(_entry(20000))  # within 24000 of frame 0
        assert not q.update(0, 50000, current_frame=0)

    def test_boundary_beyond_margin_is_unlocked(self):
        q = BoundaryQueue(safety_margin_frames=24000)
        q.add(_entry(50000))  # beyond 24000 of frame 0
        assert q.update(0, 60000, current_frame=0)

    def test_safety_margin_configurable(self):
        q = BoundaryQueue(safety_margin_frames=48000)  # 1.0s
        q.add(_entry(40000))
        assert not q.update(0, 60000, current_frame=0)

    def test_update_locks(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(50))
        q.add(_entry(200))
        q.update_locks(current_frame=0)
        entries = q.entries()
        assert entries[0].locked
        assert not entries[1].locked


class TestReplaceFuture:
    def test_replaces_unlocked(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(50))   # will be locked (within 100 of frame 0)
        q.add(_entry(500))  # unlocked, should be replaced

        new = [_entry(600), _entry(700)]
        q.replace_future(new, current_frame=0)

        entries = q.entries()
        positions = [e.frame_position for e in entries]
        assert 50 in positions   # locked, preserved
        assert 500 not in positions  # replaced
        assert 600 in positions
        assert 700 in positions

    def test_preserves_locked_entries(self):
        q = BoundaryQueue(safety_margin_frames=1000)
        q.add(_entry(100))
        q.add(_entry(500))
        q.add(_entry(5000))

        q.replace_future([], current_frame=0)
        entries = q.entries()
        # 100 and 500 are within margin (1000), should be preserved
        positions = [e.frame_position for e in entries]
        assert 100 in positions
        assert 500 in positions
        assert 5000 not in positions

    def test_replace_with_empty_keeps_locked(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(_entry(50))
        q.replace_future([], current_frame=0)
        assert len(q) == 1


class TestThreadSafety:
    def test_concurrent_add_and_pop(self):
        q = BoundaryQueue()
        n = 100

        def adder():
            for i in range(n):
                q.add(_entry(i * 100, idx=i))

        def popper(results):
            for _ in range(n):
                while True:
                    e = q.pop_next()
                    if e is not None:
                        results.append(e)
                        break

        results = []
        t1 = threading.Thread(target=adder)
        t2 = threading.Thread(target=popper, args=(results,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(results) == n
