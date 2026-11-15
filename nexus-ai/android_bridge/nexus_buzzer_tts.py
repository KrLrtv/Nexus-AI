"""NEXUS_R88_VOICE_CLARITY — same Russian eSpeak robot voice, cleaner 8 kHz PCM.

Default 'clear': automatic peak headroom, no hard clipping of consonant transients.
Optional 'original': bit-for-bit equivalent to the old gain/clamp conversion.
Hardware piezo still limits the audible frequency range.
"""
from __future__ import annotations

from nexus_speech_clean import clean_speech

from array import array
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import sys
import wave

RATE = 8000
MAX_TEXT_CHARS = 180
MAX_SAMPLES = 15 * RATE
_MODE_FILE = Path.home() / ".config" / "nexus" / "voice_clarity_mode"


class BuzzerTTSError(Exception):
    pass


def _mode() -> str:
    try:
        value = _MODE_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        value = "clear"
    return "original" if value == "original" else "clear"


def synthesize(text: str) -> bytes:
    """Same public signature used by existing /tts/pcm endpoint.

    Include mode in cache key so mode changes take effect without losing
    responsiveness to repeated requests. For an already running server, the
    module itself must be loaded once after installation.
    """
    text = clean_speech(text, MAX_TEXT_CHARS)
    if not text:
        raise BuzzerTTSError("Empty text")
    return _synthesize_cached(text, _mode())


@lru_cache(maxsize=8)
def _synthesize_cached(text: str, mode: str) -> bytes:
    program = shutil.which("espeak") or shutil.which("espeak-ng")
    if not program:
        raise BuzzerTTSError("eSpeak missing. Run: pkg install espeak")
    try:
        proc = subprocess.run(
            [program, "-v", "ru", "-s", "180", "-a", "180", "--stdout", text],
            capture_output=True,
            check=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise BuzzerTTSError("Russian eSpeak synthesis failed") from exc
    if len(proc.stdout) > 2_000_000:
        raise BuzzerTTSError("Generated audio is too long")
    try:
        with wave.open(BytesIO(proc.stdout), "rb") as wav:
            if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
                raise BuzzerTTSError("Expected 16-bit PCM WAV")
            if wav.getnchannels() not in (1, 2):
                raise BuzzerTTSError("Unsupported number of channels")
            channels = wav.getnchannels()
            src_rate = wav.getframerate()
            raw = wav.readframes(min(wav.getnframes(), src_rate * 18))
    except (wave.Error, EOFError) as exc:
        raise BuzzerTTSError("Invalid eSpeak WAV") from exc
    if not (8000 <= src_rate <= 48000):
        raise BuzzerTTSError("Unexpected sample rate")
    pcm = array("h")
    pcm.frombytes(raw)
    if sys.byteorder != "little":
        pcm.byteswap()
    frames = len(pcm) // channels
    nout = min(MAX_SAMPLES, int(frames * RATE / src_rate))
    if nout < 64:
        raise BuzzerTTSError("Audio too short")

    # Keep the old linear resampler, playback rate, voice and timing unchanged.
    # First calculate actual resampled peaks; then prevent clipping without
    # reshaping short plosive bursts or introducing compressor artifacts.
    samples = array("f")
    peak = 0.0
    for i in range(nout):
        pos = i * src_rate / RATE
        at = min(int(pos), frames - 1)
        at2 = min(at + 1, frames - 1)
        frac = pos - at
        a = pcm[at * channels]
        b = pcm[at2 * channels]
        sample = a + (b - a) * frac
        samples.append(sample)
        if abs(sample) > peak:
            peak = abs(sample)

    # 120 of the 127 possible signed U8 steps gives ~0.5 dB headroom.
    # Gain is never increased beyond the original 1.3 multiplier.
    gain = 1.3 if mode == "original" else min(1.3, 120.0 * 256.0 / max(peak, 1.0))
    out = bytearray(nout)
    for i, sample in enumerate(samples):
        v = 128 + int(sample * gain / 256)
        out[i] = 0 if v < 0 else 255 if v > 255 else v
    return bytes(out)
