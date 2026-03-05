# Phase 3: Static Segment Splitting

## Goal

Given a fixed list of song durations and a current playback position, compute frame-level segment boundaries, manage per-segment FFmpeg encoder processes, and implement the split logic that routes PCM samples to the correct encoder with zero loss or duplication.

## Prerequisites

- Phase 2 complete (PCM capture engine operational)
- Configuration decisions finalized: MP3 bitrate, channel mode, file naming scheme

## Deliverables

| ID  | Deliverable                  | Plan                                                      |
|-----|------------------------------|-----------------------------------------------------------|
| 3.1 | Segment Boundary Computation | [p3.1-segment-boundary-computation.md](phase3/p3.1-segment-boundary-computation.md) |
| 3.2 | Encoder Process Manager      | [p3.2-encoder-process-manager.md](phase3/p3.2-encoder-process-manager.md) |
| 3.3 | Split Logic Algorithm        | [p3.3-split-logic-algorithm.md](phase3/p3.3-split-logic-algorithm.md) |
| 3.4 | File Naming & Output         | [p3.4-file-naming-output.md](phase3/p3.4-file-naming-output.md) |

## Deliverable Dependencies

```
3.1 Boundary Computation ──┐
                            ├──> 3.3 Split Logic Algorithm
3.2 Encoder Process Manager ┘         |
                                       v
                                  3.4 File Naming & Output
```

3.1 and 3.2 can be built in parallel. 3.3 depends on both. 3.4 integrates naming into the output pipeline.

## Tests

- [ ] After 3.1: `compute_boundaries([30, 210, 180], 15.0, 48000)` returns `[720000, 10800000, 19440000]` cumulative frames
- [ ] After 3.2: Encoder starts, accepts PCM via stdin, produces valid MP3, terminates cleanly on stdin close
- [ ] After 3.3: Synthetic 3-segment PCM stream splits into 3 files with exact frame counts (no gaps, no overlaps)
- [ ] After 3.4: Output filenames match deterministic pattern; files exist in expected output directory
- [ ] Integration: Static 5-song split from live capture produces 5 MP3 files whose durations sum to total capture time

## Completion Criteria

1. `compute_boundaries()` converts `(songDurations[], currentPlaybackTime, sampleRate)` into cumulative frame boundaries
2. Encoder manager spawns/closes FFmpeg encoder processes on demand
3. Split loop processes PCM chunks, routing bytes to the correct encoder and splitting mid-chunk when boundaries fall within a chunk
4. Guarantees: zero dropped samples, zero duplicated samples, exact boundary alignment
5. Output files are named deterministically and written to a configurable output directory
