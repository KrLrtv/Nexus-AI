"""NEXUS R8.6: authenticated PTT PCM streaming with bounded, 10-second local STT.

M5 sends HTTP chunked transfer with an application frame header per PCM packet:
    uint16le payload_size (2..2048, even), payload_size bytes PCM16LE
    uint16le 0  -> clean button-release/record-end sentinel
Only a clean sentinel followed by a completed HTTP body can trigger processing.
A disconnected/truncated upload is never processed (important for actions).
"""
from __future__ import annotations

import io
import logging
import re
import struct
import tempfile
import threading
import time
import wave

AUDIO_RATE = 16000
PCM_BYTES_SEC = AUDIO_RATE * 2
PTT_MAX_SECONDS = 120
PTT_MAX_BYTES = PTT_MAX_SECONDS * PCM_BYTES_SEC  # 3,840,000 B on phone
STT_WINDOW_BYTES = 10 * PCM_BYTES_SEC  # 320,000 B, <= existing R6 350k WAV cap
OVERLAP_BYTES = 8000  # 0.25 s overlap at a hard recognition boundary
MIN_PCM_BYTES = 6400  # 0.2 s minimum; worker accepts 0.1s+
MAX_FRAME_BYTES = 2048
MAX_UPLINK_SECONDS = PTT_MAX_SECONDS + 30
LOG = logging.getLogger('nexus.ptt.r86')


class PTTUploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _read_exact(stream, length: int) -> bytes:
    """Read exactly length bytes or reject; no implicit short/partial commands."""
    chunks = []
    remaining = length
    while remaining:
        data = stream.read(remaining)
        if not data:
            raise PTTUploadError('Аудиопередача оборвалась', 400)
        chunks.append(data)
        remaining -= len(data)
    return b''.join(chunks)


def collect_framed_pcm(stream, elapsed_limit: float = MAX_UPLINK_SECONDS):
    """Spool bounded raw PCM in RAM/disk; caller owns/close returned file.

    Never process until the explicit end marker and HTTP body EOF are received.
    Recording can be any length from 0.2s to 120s, but no unbounded growth.
    """
    spool = tempfile.SpooledTemporaryFile(max_size=STT_WINDOW_BYTES, mode='w+b')
    total = 0
    started = time.monotonic()
    try:
        while True:
            if time.monotonic() - started > elapsed_limit:
                raise PTTUploadError('Превышено время записи', 408)
            header = _read_exact(stream, 2)
            payload_bytes = struct.unpack('<H', header)[0]
            if payload_bytes == 0:
                # End marker is required, and it must be the final application frame.
                if stream.read(1):
                    raise PTTUploadError('Лишние данные после окончания записи', 400)
                if total < MIN_PCM_BYTES:
                    raise PTTUploadError('Слишком короткая запись', 400)
                spool.seek(0)
                return spool, total
            if payload_bytes > MAX_FRAME_BYTES or payload_bytes & 1:
                raise PTTUploadError('Неверный размер PCM-пакета', 400)
            if total + payload_bytes > PTT_MAX_BYTES:
                raise PTTUploadError('Достигнут лимит записи 120 секунд', 413)
            spool.write(_read_exact(stream, payload_bytes))
            total += payload_bytes
    except Exception:
        spool.close()
        raise


def make_wav(pcm: bytes) -> bytes:
    """Standard little-endian PCM16 mono/16000 WAV; fits sherpa worker cap."""
    if not pcm or len(pcm) > STT_WINDOW_BYTES or len(pcm) & 1:
        raise ValueError('Неверная длина PCM-сегмента')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(AUDIO_RATE)
        writer.writeframes(pcm)
    return buf.getvalue()


def iter_recognition_segments(spool, total_bytes: int):
    """Yield <=10-second, 0.25s-overlapped WAV blocks, without deleting raw audio.

    Exact 10s segments are safe as long as the total is <=10s. For longer
    recordings, overlapping 250ms mitigates words split at a hard boundary.
    Overlap is for ASR only, not duplicate upload/storage.
    """
    position = 0
    while position < total_bytes:
        end = min(total_bytes, position + STT_WINDOW_BYTES)
        spool.seek(position)
        pcm = spool.read(end - position)
        if len(pcm) != end - position:
            raise PTTUploadError('Временная запись повреждена', 500)
        yield make_wav(pcm)
        if end == total_bytes:
            return
        position = end - OVERLAP_BYTES  # progress >= 9.75s; each chunk <=10s


def join_transcripts(parts: list[str]) -> str:
    """Remove only obvious repeated whole words caused by boundary overlap."""
    result = ''
    for piece in parts:
        piece = piece.strip()
        if not piece:
            continue
        if not result:
            result = piece
            continue
        a = result.split()
        b = piece.split()
        overlap_words = 0
        for size in range(min(5, len(a), len(b)), 0, -1):
            a_tail = [re.sub(r'\W+', '', w.casefold()) for w in a[-size:]]
            b_head = [re.sub(r'\W+', '', w.casefold()) for w in b[:size]]
            if a_tail == b_head and all(a_tail):
                overlap_words = size
                break
        if overlap_words:
            b = b[overlap_words:]
        if b:
            result = result.rstrip() + ' ' + ' '.join(b)
    return result.strip()


def install_routes(app, authorized, publish, process_text, legacy_voice_process,
                   approval_current, approval_consume, voice_session=None):
    """Called only at module import/startup; does not alter old /voice routes."""
    from flask import jsonify, request
    from ai_provider import ProviderError
    import nexus_local_stt

    gate = threading.Lock()

    def background(spool, pcm_bytes):
        try:
            with app.app_context():
                publish('Распознаю голос...', 'THINKING', 'voice-ptt')
                if nexus_local_stt._mode() != 'sherpa':
                    if pcm_bytes > STT_WINDOW_BYTES:
                        publish('Для записи длиннее 10 секунд включи локальное STT', 'ERROR', 'voice-ptt')
                        return
                    wav = next(iter_recognition_segments(spool, pcm_bytes))
                    # Existing Gemini STT and permission/approval route, unchanged.
                    legacy_voice_process(wav)
                    return
                if voice_session is not None:
                    voice_session.consume()
                transcripts = []
                for i, wav in enumerate(iter_recognition_segments(spool, pcm_bytes), 1):
                    try:
                        piece = nexus_local_stt.transcribe_local(wav)
                    except ProviderError as error:
                        # If one quiet segment was unintelligible, retain other chunks.
                        if 'речь не распознана' in str(error).casefold():
                            LOG.warning('PTT ASR: empty segment %d', i)
                            continue
                        raise
                    transcripts.append(piece)
                transcript = join_transcripts(transcripts).strip()
                if not transcript:
                    publish('Речь не распознана', 'ERROR', 'voice-ptt')
                    return
                LOG.warning('NEXUS R86 PTT: %d bytes, %d STT segments, %d chars',
                            pcm_bytes, i, len(transcript))
                publish('Обрабатываю запрос...', 'THINKING', 'voice-ptt')
                if approval_current():
                    auth = approval_consume(transcript, 'device')
                    if auth['state'] in ('executed', 'denied', 'await_button', 'wrong_phrase'):
                        msg = {'denied': 'Операция отменена',
                               'await_button': 'Голос принят. Удерживай B три секунды.',
                               'wrong_phrase': 'Скажи РАЗРЕШАЮ и четыре цифры с экрана.'}.get(
                            auth['state'], str(auth.get('result', 'Выполнено')))
                        publish(msg, 'CONFIRM' if auth['state'] == 'await_button' else 'ACTION', 'voice-auth')
                        return
                # All commands pass through existing settings/permission/agent gates.
                process_text(transcript, 'voice')
        except ProviderError as error:
            app.logger.warning('NEXUS R86 PTT recognizer/provider error: %s', error)
            publish(str(error)[:180], 'ERROR', 'voice-ptt')
        except Exception:
            app.logger.exception('NEXUS R86 PTT failed')
            publish('Ошибка обработки голосового запроса', 'ERROR', 'voice-ptt')
        finally:
            spool.close()
            gate.release()

    @app.post('/voice/ptt-stream')
    def nexus_ptt_stream_r86():
        if not authorized():
            return jsonify(error='unauthorized'), 401
        if request.mimetype != 'application/octet-stream':
            return jsonify(error='PCM16 required'), 415
        if not request.environ.get('wsgi.input_terminated') and request.content_length is None:
            return jsonify(error='chunked upload unsupported'), 501
        if not gate.acquire(blocking=False):
            return jsonify(error='Предыдущий голосовой запрос ещё обрабатывается'), 409
        handed_off = False
        spool = None
        try:
            spool, pcm_bytes = collect_framed_pcm(request.stream)
            publish('Распознаю голос...', 'THINKING', 'voice-ptt')
            thread = threading.Thread(target=background, args=(spool, pcm_bytes),
                                      name='nexus-r86-ptt', daemon=True)
            thread.start()
            handed_off = True
            return jsonify(accepted=True, seconds=round(pcm_bytes / PCM_BYTES_SEC, 2)), 202
        except PTTUploadError as error:
            return jsonify(error=str(error)), error.status
        except (OSError, ValueError, TimeoutError):
            app.logger.warning('NEXUS R86 PTT: incomplete/broken stream')
            return jsonify(error='Аудиопередача оборвалась, команда отменена'), 400
        finally:
            if not handed_off:
                if spool is not None:
                    spool.close()
                gate.release()
