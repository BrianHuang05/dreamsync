"""Tests for dreamsync.capture.split_logic — PCM splitting at frame boundaries."""

import pytest

from dreamsync.capture.split_logic import SplitProcessor
from dreamsync.capture.pcm_reader import BYTES_PER_FRAME


class MockEncoder:
    """Collects written bytes and tracks close calls."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, pcm: bytes) -> None:
        self.data.extend(pcm)

    def close(self) -> None:
        self.closed = True


def make_processor(boundaries, encoders=None):
    """Build a SplitProcessor with mock encoders."""
    created_encoders = [] if encoders is None else encoders
    completed_segments = []

    def start_encoder(seg_idx):
        enc = MockEncoder()
        while len(created_encoders) <= seg_idx:
            created_encoders.append(None)
        created_encoders[seg_idx] = enc
        return enc

    def on_complete(seg_idx, start_frame, end_frame):
        completed_segments.append((seg_idx, start_frame, end_frame))

    proc = SplitProcessor(
        boundaries=boundaries,
        start_encoder=start_encoder,
        on_segment_complete=on_complete,
    )
    return proc, created_encoders, completed_segments


class TestExactBoundaries:
    def test_single_segment(self):
        """All data goes to one encoder."""
        boundaries = [100]  # 100 frames
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        chunk = b"\x00" * (100 * BYTES_PER_FRAME)
        proc.process_chunk(chunk)
        proc.finish()

        assert len(encoders[0].data) == 100 * BYTES_PER_FRAME

    def test_two_segments_exact(self):
        """Two chunks landing exactly on boundaries."""
        boundaries = [100, 200]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        proc.process_chunk(b"\x01" * (100 * BYTES_PER_FRAME))
        proc.process_chunk(b"\x02" * (100 * BYTES_PER_FRAME))
        proc.finish()

        assert len(encoders[0].data) == 100 * BYTES_PER_FRAME
        assert len(encoders[1].data) == 100 * BYTES_PER_FRAME
        assert all(b == 0x01 for b in encoders[0].data)
        assert all(b == 0x02 for b in encoders[1].data)


class TestMidChunkSplit:
    def test_split_within_chunk(self):
        """Boundary falls in the middle of a chunk."""
        boundaries = [50, 200]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        # 100 frames — first 50 go to segment 0, last 50 to segment 1
        chunk = b"\xAA" * (50 * BYTES_PER_FRAME) + b"\xBB" * (50 * BYTES_PER_FRAME)
        proc.process_chunk(chunk)
        proc.finish()

        assert len(encoders[0].data) == 50 * BYTES_PER_FRAME
        assert len(encoders[1].data) == 50 * BYTES_PER_FRAME

    def test_multiple_boundaries_one_chunk(self):
        """Multiple boundaries within a single chunk."""
        boundaries = [10, 20, 30]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        chunk = b"\x00" * (30 * BYTES_PER_FRAME)
        proc.process_chunk(chunk)
        proc.finish()

        assert len(encoders[0].data) == 10 * BYTES_PER_FRAME
        assert len(encoders[1].data) == 10 * BYTES_PER_FRAME
        assert len(encoders[2].data) == 10 * BYTES_PER_FRAME


class TestEdgeCases:
    def test_boundary_at_zero(self):
        """First boundary at frame 0 means immediate rotation."""
        boundaries = [0, 100]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        proc.process_chunk(b"\x00" * (100 * BYTES_PER_FRAME))
        proc.finish()

        # Segment 0 is empty (boundary at 0), segment 1 gets everything
        assert len(encoders[0].data) == 0
        assert len(encoders[1].data) == 100 * BYTES_PER_FRAME

    def test_data_past_last_boundary(self):
        """Data arriving after all boundaries goes to last encoder."""
        boundaries = [10]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        proc.process_chunk(b"\x00" * (50 * BYTES_PER_FRAME))
        proc.finish()

        assert len(encoders[0].data) == 10 * BYTES_PER_FRAME
        assert len(encoders[1].data) == 40 * BYTES_PER_FRAME

    def test_empty_boundaries_list(self):
        """No boundaries — everything goes to a single encoder."""
        boundaries = []
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        proc.process_chunk(b"\x00" * (100 * BYTES_PER_FRAME))
        proc.finish()

        assert len(encoders[0].data) == 100 * BYTES_PER_FRAME

    def test_start_required(self):
        boundaries = [100]
        proc, _, _ = make_processor(boundaries)
        with pytest.raises(RuntimeError, match="start"):
            proc.process_chunk(b"\x00" * 4)


class TestZeroLossGuarantee:
    def test_total_bytes_preserved(self):
        """Total bytes across all encoders equals input bytes."""
        boundaries = [100, 300, 600]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        total_input = 0
        for _ in range(10):
            chunk = b"\x00" * (80 * BYTES_PER_FRAME)
            proc.process_chunk(chunk)
            total_input += len(chunk)
        proc.finish()

        total_output = sum(len(e.data) for e in encoders if e is not None)
        assert total_output == total_input

    def test_frame_counts_match_boundaries(self):
        """Each segment has exactly the right number of frames."""
        boundaries = [100, 300, 500]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        proc.process_chunk(b"\x00" * (500 * BYTES_PER_FRAME))
        proc.finish()

        assert len(encoders[0].data) // BYTES_PER_FRAME == 100
        assert len(encoders[1].data) // BYTES_PER_FRAME == 200
        assert len(encoders[2].data) // BYTES_PER_FRAME == 200


class TestCallbacks:
    def test_on_segment_complete_called(self):
        boundaries = [50, 100]
        proc, encoders, segments = make_processor(boundaries)

        proc.start()
        proc.process_chunk(b"\x00" * (100 * BYTES_PER_FRAME))
        proc.finish()

        # 2 rotations at boundaries + finish for the trailing encoder
        # Boundary at 50: closes seg 0 (0→50)
        # Boundary at 100: closes seg 1 (50→100)
        # finish(): closes seg 2 (100→100, empty trailing segment)
        assert segments[0] == (0, 0, 50)
        assert segments[1] == (1, 50, 100)
        assert len(segments) >= 2

    def test_run_convenience(self):
        """run() processes all chunks and finishes."""
        boundaries = [50]
        proc, encoders, segments = make_processor(boundaries)

        chunks = [b"\x00" * (25 * BYTES_PER_FRAME)] * 4  # 100 frames total
        proc.run(iter(chunks))

        assert len(encoders[0].data) == 50 * BYTES_PER_FRAME
        assert len(encoders[1].data) == 50 * BYTES_PER_FRAME
