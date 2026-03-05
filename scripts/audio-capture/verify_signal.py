"""Verify audio signal flows through VB-Audio Virtual Cable.

Plays a 440 Hz test tone directly to CABLE Input (playback device) and
records from CABLE Output (recording device), then checks for signal.
Uses WASAPI for low-latency, reliable loopback.
"""

import sys
import numpy as np


def find_cable_devices(pa, preferred_api="Windows DirectSound"):
    """Find CABLE Input (playback) and CABLE Output (recording).

    Prefers DirectSound which handles sample rate conversion automatically.
    Falls back to WASAPI, then MME.
    """
    candidates_in = []
    candidates_out = []

    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        api_name = pa.get_host_api_info_by_index(info["hostApi"])["name"]

        if "CABLE Input" in info["name"] and info["maxOutputChannels"] > 0:
            candidates_in.append((i, info, api_name))
        elif "CABLE Output" in info["name"] and info["maxInputChannels"] > 0:
            candidates_out.append((i, info, api_name))

    def pick(candidates):
        for idx, info, api in candidates:
            if api == preferred_api:
                return (idx, info)
        return (candidates[0][0], candidates[0][1]) if candidates else None

    return pick(candidates_in), pick(candidates_out)


def run_verification():
    import pyaudio

    pa = pyaudio.PyAudio()

    try:
        cable_in, cable_out = find_cable_devices(pa)

        if cable_in is None:
            print("FAIL: CABLE Input (WASAPI) playback device not found")
            return False
        if cable_out is None:
            print("FAIL: CABLE Output (WASAPI) recording device not found")
            return False

        in_idx, in_info = cable_in
        out_idx, out_info = cable_out
        print(f"CABLE Input  (playback):  index={in_idx}, rate={in_info['defaultSampleRate']:.0f}")
        print(f"CABLE Output (recording): index={out_idx}, rate={out_info['defaultSampleRate']:.0f}")

        # Use the playback device's native rate for both to avoid resampling issues
        rate = int(in_info["defaultSampleRate"])
        channels = min(int(in_info["maxOutputChannels"]), int(out_info["maxInputChannels"]), 2)
        chunk = 1024
        duration = 2  # seconds
        tone_freq = 440  # Hz

        print(f"Using rate={rate}, channels={channels}")

        # Generate 440 Hz sine wave
        t = np.linspace(0, duration, int(rate * duration), endpoint=False)
        mono_tone = (np.sin(2 * np.pi * tone_freq * t) * 0.5 * 32767).astype(np.int16)

        # Interleave channels if stereo
        if channels > 1:
            tone = np.column_stack([mono_tone] * channels).flatten()
        else:
            tone = mono_tone

        # Open playback stream to CABLE Input
        play_stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            output=True,
            output_device_index=in_idx,
            frames_per_buffer=chunk,
        )

        # Open recording stream from CABLE Output
        rec_stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=out_idx,
            frames_per_buffer=chunk,
        )

        print(f"Playing {tone_freq} Hz tone -> CABLE Input, recording <- CABLE Output ({duration}s)...")

        # Play and record simultaneously
        recorded_frames = []
        tone_bytes = tone.tobytes()
        bytes_per_sample = 2 * channels
        bytes_per_chunk = chunk * bytes_per_sample

        total_chunks = len(tone_bytes) // bytes_per_chunk
        for i in range(total_chunks):
            offset = i * bytes_per_chunk
            play_stream.write(tone_bytes[offset:offset + bytes_per_chunk])
            data = rec_stream.read(chunk, exception_on_overflow=False)
            recorded_frames.append(data)

        play_stream.stop_stream()
        rec_stream.stop_stream()
        play_stream.close()
        rec_stream.close()

        # Analyze recorded audio
        recorded = np.frombuffer(b"".join(recorded_frames), dtype=np.int16)
        rms = np.sqrt(np.mean(recorded.astype(np.float64) ** 2))
        peak = int(np.max(np.abs(recorded)))

        print(f"Recorded {len(recorded)} samples")
        print(f"RMS level: {rms:.1f} (of 32767)")
        print(f"Peak level: {peak} (of 32767)")

        if rms > 100:
            print(f"PASS: Audio signal detected (RMS={rms:.1f})")
            return True
        else:
            print(f"FAIL: No significant audio signal (RMS={rms:.1f}, expected > 100)")
            return False

    finally:
        pa.terminate()


if __name__ == "__main__":
    try:
        import pyaudio
    except ImportError:
        print("Installing pyaudio...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyaudio"])

    success = run_verification()
    sys.exit(0 if success else 1)
