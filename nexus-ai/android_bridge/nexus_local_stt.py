"""NEXUS R6: choose local sherpa-onnx STT or legacy Gemini STT per request.

Default is the previous cloud recognizer. Mode is a local file and may be
changed without rewriting firmware or restarting NEXUS. Action processing
remains in process_text(), with all existing permission checks.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from time import monotonic
import urllib.error
import urllib.request

from ai_provider import ProviderError
from audio_ai import ask_audio as _cloud_ask_audio

MODE_PATH = Path.home() / ".config" / "nexus" / "stt_mode"
LOCAL_STT_URL = "http://127.0.0.1:8766/asr"
MAX_WAV = 350_000
LOG = logging.getLogger("nexus.local_stt")


def _mode() -> str:
    try:
        mode = MODE_PATH.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "gemini"
    return mode if mode in ("gemini", "sherpa") else "gemini"


def transcribe_local(wav: bytes) -> str:
    if not 44 <= len(wav) <= MAX_WAV or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ProviderError("Некорректный WAV от устройства")
    req = urllib.request.Request(
        LOCAL_STT_URL, data=wav, method="POST",
        headers={"Content-Type": "audio/wav", "Accept": "application/json"},
    )
    started = monotonic()
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=25) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read(1024).decode("utf-8")).get("error", "")
        except (ValueError, UnicodeDecodeError):
            message = ""
        raise ProviderError(f"Локальное распознавание: HTTP {exc.code} {message[:120]}") from exc
    except (OSError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
        raise ProviderError("Локальное распознавание недоступно. Проверь nexus-stt status") from exc
    transcript = str(result.get("text", "")).strip()[:500]
    LOG.warning("NEXUS LOCAL STT: %.2fs request, %.2fs recognition, %d chars",
                monotonic() - started, float(result.get("decode_seconds", 0)), len(transcript))
    if not transcript:
        raise ProviderError("Локально речь не распознана. Повтори ближе к микрофону")
    return transcript


def ask_audio(wav: bytes, history: list):
    """Same signature as the original; planner will run only once for local STT."""
    if _mode() == "gemini":
        return _cloud_ask_audio(wav, history)
    transcript = transcribe_local(wav)
    # This marker prevents R5 fast-chat shortcut (which expects Gemini to have
    # already answered the question). The existing planner handles all requests.
    return transcript, "ACTION_REQUIRED"


def ask_audio_fast(wav: bytes, history: list):
    """Compatibility with variants of NEXUS Fast Voice that use triple results."""
    if _mode() == "gemini":
        from audio_ai import ask_audio_fast as original
        return original(wav, history)
    return transcribe_local(wav), "ACTION_REQUIRED", "TOOL"
