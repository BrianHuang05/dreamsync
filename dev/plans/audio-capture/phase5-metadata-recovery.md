# Phase 5: Metadata, Logging & Recovery

## Goal

Add operational robustness to the pipeline: generate metadata sidecar files for every output MP3, detect and handle timing drift, recover from process failures, and provide structured logging for debugging and monitoring.

## Prerequisites

- Phase 4 complete (dynamic rolling window operational)
- Song metadata source available (titles, artists) for sidecar population

## Deliverables

| ID  | Deliverable                   | Plan                                                        |
|-----|-------------------------------|-------------------------------------------------------------|
| 5.1 | Metadata Sidecar Files        | [p5.1-metadata-sidecar-files.md](phase5/p5.1-metadata-sidecar-files.md) |
| 5.2 | Drift Detection & Resync      | [p5.2-drift-detection-resync.md](phase5/p5.2-drift-detection-resync.md) |
| 5.3 | Failure Handling & Recovery    | [p5.3-failure-handling-recovery.md](phase5/p5.3-failure-handling-recovery.md) |
| 5.4 | Logging System                | [p5.4-logging-system.md](phase5/p5.4-logging-system.md) |

## Deliverable Dependencies

```
5.4 Logging System (build first - used by all others)
  |
  ├──> 5.1 Metadata Sidecar Files
  ├──> 5.2 Drift Detection & Resync
  └──> 5.3 Failure Handling & Recovery
```

5.4 should be built first. 5.1, 5.2, and 5.3 can be built in parallel after 5.4.

## Tests

- [ ] After 5.1: Each MP3 output has a companion `.json` with all required fields populated
- [ ] After 5.1: Sidecar `segmentDurationFrames` matches actual MP3 duration within 1 frame
- [ ] After 5.2: Simulated 2-second drift triggers resync and log entry
- [ ] After 5.3: Killing capture FFmpeg mid-stream triggers automatic restart within 2 seconds
- [ ] After 5.3: Encoder failure triggers retry or fallback and logs the event
- [ ] After 5.4: All pipeline events produce structured JSON log entries
- [ ] Integration: Full pipeline run with injected failures produces complete logs and valid output

## Completion Criteria

1. Every output MP3 has a `.json` sidecar containing: `startFrame`, `endFrame`, `segmentDurationFrames`, `plannedStartTime`, `plannedEndTime`, `songTitle`, `artist`, `sourceTimingData`
2. Drift between planned and actual segment boundaries is measured and logged
3. Drift exceeding a configurable threshold triggers automatic resynchronization
4. Capture FFmpeg crash is detected and triggers automatic restart with gap logging
5. Encoder failures are handled per policy (retry -> raw PCM fallback -> skip)
6. Structured JSON logs cover: process lifecycle, segment transitions, boundary updates, errors, drift measurements
