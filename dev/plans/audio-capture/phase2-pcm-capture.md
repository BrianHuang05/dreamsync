# Phase 2: PCM Capture Engine

## Goal

Build the core capture subsystem: discover the virtual audio device via FFmpeg, spawn the capture process, read raw PCM from its stdout, and maintain accurate frame counting with configurable buffering.

## Prerequisites

- Phase 1 complete (audio routing verified)
- FFmpeg installed and accessible on PATH
- Python 3.10+ (or chosen implementation language) available

## Deliverables

| ID  | Deliverable               | Plan                                                |
|-----|---------------------------|-----------------------------------------------------|
| 2.1 | FFmpeg Device Discovery   | [p2.1-ffmpeg-device-discovery.md](phase2/p2.1-ffmpeg-device-discovery.md) |
| 2.2 | Capture Process Manager   | [p2.2-capture-process-manager.md](phase2/p2.2-capture-process-manager.md) |
| 2.3 | PCM Stream Reader         | [p2.3-pcm-stream-reader.md](phase2/p2.3-pcm-stream-reader.md) |
| 2.4 | Frame Counter & Buffer    | [p2.4-frame-counter-buffer.md](phase2/p2.4-frame-counter-buffer.md) |

## Deliverable Dependencies

```
2.1 Device Discovery
  |
  v
2.2 Capture Process Manager
  |
  v
2.3 PCM Stream Reader
  |
  v
2.4 Frame Counter & Buffer
```

## Tests

- [ ] After 2.1: Device discovery returns the correct device name string for CABLE Output
- [ ] After 2.2: Capture process spawns, runs, and can be cleanly terminated
- [ ] After 2.3: Reader produces correctly sized PCM byte chunks from capture stdout
- [ ] After 2.4: Frame counter matches expected value after N seconds of capture (N * 48000 frames)
- [ ] Integration: 10-second capture produces exactly 480,000 frames (1,920,000 bytes)

## Completion Criteria

1. FFmpeg capture process spawns targeting CABLE Output device
2. Raw PCM streams continuously to handler via stdout pipe
3. PCM is read in configurable chunk sizes (default: 100ms = 19,200 bytes)
4. Global frame counter increments by `chunk_bytes / 4` per chunk
5. Buffer layer allows downstream consumers to read independently of capture rate
6. Clean shutdown: capture process terminates gracefully on handler exit
