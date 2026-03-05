# Windows 11 Lossless Capture → Dynamic Splits → MP3 Files

## Goal
Create a pipeline where computer audio is routed through a virtual speaker, captured losslessly as PCM, and split into MP3 files based on externally supplied song timing data.

Pipeline:

Computer Audio → VB‑Audio Virtual Cable → FFmpeg (capture PCM) → Handler Program (split logic) → FFmpeg (encode MP3 per segment)

---

## 1. Install and Configure Virtual Audio Device

### Install VB‑Audio Virtual Cable
Download and install from:
https://vb-audio.com/Cable/

After installation and reboot, two devices appear:

Playback device:

CABLE Input (VB‑Audio Virtual Cable)

Recording device:

CABLE Output (VB‑Audio Virtual Cable)

### Route System or App Audio

Set the output of the application that produces audio to:

CABLE Input

Windows Settings → System → Sound → Volume Mixer → choose app → Output Device → CABLE Input

### Verify Audio Signal

Open Sound settings → Recording devices.

Confirm that **CABLE Output** shows activity while audio plays.

---

## 2. Standardize Capture Format

Choose a single PCM format for the pipeline.

Recommended configuration:

Sample rate: 48000 Hz  
Channels: 2 (stereo)  
Format: s16le (16‑bit little‑endian)

Frame size:

bytesPerFrame = channels × bytesPerSample
bytesPerFrame = 2 × 2 = 4 bytes

Throughput:

bytesPerSecond = sampleRate × bytesPerFrame
bytesPerSecond = 48000 × 4 = 192000 bytes

---

## 3. Continuous Capture Process (FFmpeg)

### List available devices

```
ffmpeg -list_devices true -f dshow -i dummy
```

Locate:

CABLE Output (VB‑Audio Virtual Cable)

### Start capture process

This FFmpeg process runs continuously and writes raw PCM to stdout.

```
ffmpeg -hide_banner -loglevel warning ^
  -thread_queue_size 1024 ^
  -f dshow -i audio="CABLE Output (VB-Audio Virtual Cable)" ^
  -ac 2 -ar 48000 ^
  -f s16le -acodec pcm_s16le ^
  pipe:1
```

The handler program spawns this process and reads from stdout.

No encoding occurs here. The stream remains lossless PCM.

---

## 4. Handler Program Responsibilities

The handler acts as the controller for:

• reading the PCM stream
• calculating split boundaries
• spawning MP3 encoders
• routing audio samples
• writing metadata

### Core responsibilities

1. Spawn capture FFmpeg process
2. Maintain global frame counter
3. Maintain queue of segment boundaries
4. Pipe audio samples into encoder processes
5. Rotate files exactly at boundaries

---

## 5. Work With Frames Instead of Time

Splits must occur on sample boundaries.

Frame definition:

1 frame = one sample per channel

For stereo 16‑bit:

1 frame = 4 bytes

Conversion formulas:

frames = seconds × sampleRate

bytes = frames × bytesPerFrame

Example:

30 seconds

frames = 30 × 48000 = 1,440,000
bytes = 1,440,000 × 4 = 5,760,000

---

## 6. Computing Segment Boundaries

Input provided by external program:

currentPlaybackTime
songDurations = [d0, d1, d2, d3, d4]

Where d0 is the currently playing song.

Remaining time in current song:

r0 = max(d0 − currentPlaybackTime, 0)

Segment durations from "now":

[r0, d1, d2, d3, d4]

Convert durations to frames:

segFrames[i] = round(segSeconds[i] × sampleRate)

Compute cumulative boundaries:

cut1 = segFrames[0]
cut2 = segFrames[0] + segFrames[1]
cut3 = cut2 + segFrames[2]
cut4 = cut3 + segFrames[3]

These represent frame counts relative to plan start.

---

## 7. Rolling Window Updates

Song duration information updates periodically.

Strategy:

• Maintain a boundary queue covering the next several songs
• Refresh plan every few seconds or on track change
• Only modify boundaries sufficiently far in the future

Safety margin recommendation:

0.5 to 1.0 seconds

Frames equivalent:

0.5 seconds → 24,000 frames

Boundaries closer than this should not be modified.

---

## 8. Segment Encoding Process

Each output file is produced by a separate FFmpeg encoder.

Encoder command:

```
ffmpeg -hide_banner -loglevel warning ^
  -f s16le -ac 2 -ar 48000 -i pipe:0 ^
  -c:a libmp3lame -b:a 192k ^
  "OUTPUT_FILE.mp3"
```

The handler writes PCM bytes to encoder stdin.

When a segment ends:

1. Close encoder stdin
2. Wait for process to exit
3. Start next encoder

---

## 9. Buffering Strategy

Recommended read chunk size:

20–100 ms of audio

Example 100 ms:

192000 bytes/sec × 0.1 = 19200 bytes

Use a ring buffer or chunk queue between capture and encoder.

---

## 10. Split Logic Algorithm

State variables:

sampleRate = 48000
bytesPerFrame = 4

currentFrame
segmentIndex
segmentFramesWritten
segmentTargetFrames

Loop:

1. Read PCM chunk from capture
2. Convert chunkBytes → chunkFrames
3. While chunkFrames remain:

If chunkFrames ≤ framesRemaining:

write entire chunk

Else:

write portion needed to complete segment

close encoder

start next encoder

continue processing remaining frames

This guarantees:

• no dropped samples
• no duplicated samples
• exact segment boundaries

---

## 11. File Naming Strategy

Use deterministic filenames.

Recommended pattern:

YYYY-MM-DD_HH-MM-SS_song_000123.mp3

Or include song metadata if available.

---

## 12. Metadata Sidecar Files

For each MP3 file create a JSON file:

example.json

Fields:

startFrame
endFrame
segmentDurationFrames
plannedStartTime
plannedEndTime
songTitle
artist
sourceTimingData

This enables later debugging and verification.

---

## 13. Drift and Resynchronization

Capture timing is deterministic but timing metadata may drift.

Mitigation strategies:

• refresh split plan periodically
• trust track‑change events
• adjust only future boundaries

Optional advanced strategy:

silence detection to confirm track transitions.

---

## 14. Failure Handling

### Capture FFmpeg exits

Handler should:

1. detect EOF
2. restart capture
3. log event

### Encoder failure

Policy options:

• retry encoding
• write raw PCM fallback
• skip file and continue

---

## 15. Performance Expectations

Raw capture bandwidth:

192 KB/s

MP3 output at 192 kbps:

~24 KB/s

CPU requirements are minimal.

---

## 16. Testing Procedure

### Unit tests

• seconds → frames conversion
• chunk boundary logic
• split correctness

### Integration tests

Use synthetic PCM stream to verify exact splits.

### Manual verification

1. Play test audio
2. Create known split schedule
3. Confirm file durations

---

## 17. Implementation Milestones

Milestone A

VB cable installed and audio routed.

Milestone B

Handler reads PCM from capture FFmpeg.

Milestone C

Static segment splitting works.

Milestone D

Rolling window updates integrated.

Milestone E

Logging and recovery features added.

---

## 18. Capture Command Reference

```
ffmpeg -hide_banner -loglevel warning ^
  -thread_queue_size 1024 ^
  -f dshow -i audio="CABLE Output (VB-Audio Virtual Cable)" ^
  -ac 2 -ar 48000 ^
  -f s16le -acodec pcm_s16le ^
  pipe:1
```

---

## 19. Encoder Command Reference

```
ffmpeg -hide_banner -loglevel warning ^
  -f s16le -ac 2 -ar 48000 -i pipe:0 ^
  -c:a libmp3lame -b:a 192k ^
  "segment_000.mp3"
```

---

## 20. Configuration Decisions

Select values before implementation:

MP3 bitrate

Stereo vs mono

File naming scheme

Plan refresh interval

Safety margin duration

