"""NEXUS R87: bounded M5StickC Plus2 push-to-talk transport for Flask.

R86 device body (after HTTP chunked decoding):
  repeat: uint16_le length + length bytes signed PCM16 mono 16kHz
  finish: uint16_le zero, followed by HTTP end of body

Only completed recordings trigger NEXUS commands. WAV is spooled to disk in
<=10-second segments; local ASR runs incrementally in a separate thread.
"""

from __future__ import annotations

import logging
import queue
import tempfile
import threading
import time
import wave
from pathlib import Path

from flask import copy_current_request_context, jsonify, request

_LOG = logging.getLogger("nexus.ptt.r87")
_RATE = 16_000
_MAX_PCM = 120 * _RATE * 2             # 120 seconds / <=3.84 MB on disk
_SEGMENT_PCM = 10 * _RATE * 2        # 10-second WAV: 320044 bytes
_MIN_PCM = 3_200 * 2                 # 0.2 seconds; same as device
_MAX_FRAME = 2048                    # R86 sends 1024-byte frames
_MAX_WALL_SECONDS = 135


class _ProtocolError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _read_exact(stream, length: int, started: float) -> bytes:
    """Read one application frame part, handling partial WSGI reads."""
    pieces = []
    left = length
    while left:
        if time.monotonic() - started > _MAX_WALL_SECONDS:
            raise _ProtocolError(408, "PTT capture exceeded 135 seconds")
        piece = stream.read(left)
        if not piece:
            raise _ProtocolError(400, "PTT stream ended without release marker")
        pieces.append(piece)
        left -= len(piece)
    return b"".join(pieces)


def register_ptt_stream(app, *, authorized, publish, process_text,
                        approval_current, approval_consume, voice_session):
    """Register endpoint using the existing NEXUS authorization and planner."""
    capture_lock = threading.Lock()

    @app.post("/voice/ptt-stream")
    def nexus_voice_ptt_stream_r87():
        if not authorized():
            return jsonify(error="unauthorized"), 401
        if request.mimetype != "application/octet-stream":
            return jsonify(error="PCM16 required"), 415
        if request.content_length is None and not request.environ.get("wsgi.input_terminated"):
            return jsonify(error="HTTP chunked streaming unsupported by server"), 501
        if request.content_length is not None and request.content_length > _MAX_PCM + 20_000:
            return jsonify(error="PTT too large"), 413
        if not capture_lock.acquire(blocking=False):
            return jsonify(error="PTT already active"), 429

        try:
            spool = tempfile.TemporaryDirectory(prefix="nexus-ptt-r87-")
        except OSError:
            capture_lock.release()
            _LOG.exception("Could not open PTT temporary directory")
            return jsonify(error="PTT temporary storage unavailable"), 507

        # Queue contains only filenames, NOT audio: max ~4 MB on disk and one
        # 320044-byte WAV loaded into RAM by the recognition worker at a time.
        tasks: queue.Queue = queue.Queue(maxsize=16)
        state = {"aborted": False, "released": False}

        @copy_current_request_context
        def recognize_in_background():
            texts = []
            stt_error = None
            try:
                from nexus_local_stt import transcribe_local

                while True:
                    segment = tasks.get()
                    if segment is None:
                        break
                    try:
                        if state["aborted"] or stt_error:
                            continue
                        # Each file is <=10s and under nexus_local_stt.MAX_WAV.
                        with open(segment, "rb") as fd:
                            wav = fd.read(_SEGMENT_PCM + 45)
                        if len(wav) > _SEGMENT_PCM + 44:
                            raise ValueError("PTT WAV segment too long")
                        try:
                            text = transcribe_local(wav)
                        except Exception as exc:
                            # A silent segment must not invalidate other speech.
                            if "речь не распознана" in str(exc).lower():
                                continue
                            stt_error = str(exc)
                            _LOG.warning("NEXUS R87 PTT STT error: %s", stt_error)
                            continue
                        if text.strip():
                            texts.append(text.strip())
                    finally:
                        try:
                            Path(segment).unlink(missing_ok=True)
                        except OSError:
                            pass

                # No action, including confirmation, is permitted before the
                # explicit release marker AND a clean HTTP body terminator.
                if not state["released"] or state["aborted"]:
                    return
                if stt_error:
                    publish(("Ошибка локального распознавания: " + stt_error)[:200],
                            "ERROR", "voice-ptt")
                    return
                transcript = " ".join(texts).strip()
                if not transcript:
                    publish("Речь не распознана. Повтори ближе к микрофону.",
                            "ERROR", "voice-ptt")
                    return
                voice_session.consume()
                _LOG.warning("NEXUS R87 PTT transcription: %d chars, %d chunks",
                             len(transcript), len(texts))

                # Same explicit voice-approval flow as _nexus_voice_process_r7.
                # NEXUS_R893_DIRECT_COMMANDS: PTT always forwards speech to process_text.

                # Reuse NEXUS Brain and its existing gated tool/permission flow.
                # process_text already performs the normal answer publication.
                result, status = process_text(transcript, "voice")
                if status >= 400:
                    message = result.get("error", "Ошибка NEXUS Brain") if isinstance(result, dict) else "Ошибка NEXUS Brain"
                    publish(str(message)[:200], "ERROR", "voice-ptt")
            except Exception:
                _LOG.exception("NEXUS R87 PTT background handler failed")
                if state["released"] and not state["aborted"]:
                    publish("Ошибка обработки голосового запроса", "ERROR", "voice-ptt")
            finally:
                spool.cleanup()
                capture_lock.release()

        # Capture may last 120s. The worker can recognize completed 10s files
        # meanwhile, but cannot execute any command before confirmed release.
        try:
            thread = threading.Thread(
                target=recognize_in_background,
                name="nexus-ptt-r87-asr", daemon=True,
            )
            thread.start()
        except Exception:
            spool.cleanup()
            capture_lock.release()
            _LOG.exception("Could not start PTT worker")
            return jsonify(error="PTT worker unavailable"), 503

        started = time.monotonic()
        total = 0
        segment_size = 0
        segment_id = 0
        writer = None
        writer_path = None

        def close_segment(*, enqueue: bool):
            nonlocal writer, writer_path, segment_size
            if writer is None:
                return
            writer.close()
            if enqueue and segment_size >= _MIN_PCM:
                tasks.put(str(writer_path))
            else:
                writer_path.unlink(missing_ok=True)
            writer = None
            writer_path = None
            segment_size = 0

        try:
            stream = request.stream
            while True:
                size = int.from_bytes(_read_exact(stream, 2, started), "little")
                if size == 0:
                    # R86 sends the application release marker before the
                    # terminating HTTP chunk. Reject any trailing body data.
                    if stream.read(1):
                        raise _ProtocolError(400, "data after PTT release marker")
                    break
                if size > _MAX_FRAME or (size & 1):
                    raise _ProtocolError(400, "invalid PTT frame length")
                if total + size > _MAX_PCM:
                    raise _ProtocolError(413, "PTT exceeds 120 seconds")
                payload = _read_exact(stream, size, started)
                total += size
                offset = 0
                while offset < len(payload):
                    if writer is None:
                        segment_id += 1
                        writer_path = Path(spool.name) / f"segment-{segment_id:03d}.wav"
                        writer = wave.open(str(writer_path), "wb")
                        writer.setnchannels(1)
                        writer.setsampwidth(2)
                        writer.setframerate(_RATE)
                    amount = min(len(payload) - offset, _SEGMENT_PCM - segment_size)
                    writer.writeframesraw(payload[offset:offset + amount])
                    segment_size += amount
                    offset += amount
                    if segment_size == _SEGMENT_PCM:
                        close_segment(enqueue=True)

            if total < _MIN_PCM:
                raise _ProtocolError(400, "PTT recording too short")
            close_segment(enqueue=True)
            state["released"] = True
            publish("Распознаю голос...", "THINKING", "voice-ptt")
            tasks.put(None)
            _LOG.warning("NEXUS R87 PTT accepted: %d PCM bytes, %d segments, %.1fs",
                         total, segment_id, time.monotonic() - started)
            return jsonify(accepted=True, pcm_bytes=total, segments=segment_id), 202
        except _ProtocolError as exc:
            state["aborted"] = True
            _LOG.warning("NEXUS R87 PTT aborted: %s", exc.message)
            try:
                close_segment(enqueue=False)
            finally:
                tasks.put(None)
            return jsonify(error=exc.message), exc.status
        except (OSError, ValueError, TimeoutError) as exc:
            state["aborted"] = True
            _LOG.warning("NEXUS R87 PTT broken stream: %s", exc)
            try:
                close_segment(enqueue=False)
            finally:
                tasks.put(None)
            return jsonify(error="PTT incomplete or corrupted stream"), 400
        except Exception:
            state["aborted"] = True
            _LOG.exception("NEXUS R87 PTT unexpected capture error")
            try:
                close_segment(enqueue=False)
            finally:
                tasks.put(None)
            return jsonify(error="PTT capture failure"), 500
