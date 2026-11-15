"""Warm offline Russian speech recognizer, bound to 127.0.0.1 only.

Run inside Ubuntu PRoot via the venv installed by setup_sherpa_termux.sh.
Input is the short, unmodified WAV from the M5StickC. No cloud ASR.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from pathlib import Path
import struct
import threading
from time import monotonic
import wave

MAX_WAV = 350_000
LOG = logging.getLogger("nexus.stt")


class InvalidAudio(ValueError):
    pass


def pcm_from_wav(raw: bytes):
    """Return (sample rate, normalized float32 numpy array) from mono PCM16."""
    if not (44 <= len(raw) <= MAX_WAV and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"):
        raise InvalidAudio("Некорректный RIFF/WAV")
    try:
        with wave.open(io.BytesIO(raw), 'rb') as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            frames = w.getnframes()
            if channels != 1 or width != 2 or rate != 16000:
                raise InvalidAudio("Нужен WAV PCM16 mono 16000 Hz")
            if not 1600 <= frames <= 16000 * 10:
                raise InvalidAudio("Слишком короткая или длинная запись")
            pcm = w.readframes(frames)
    except (wave.Error, EOFError, struct.error) as exc:
        raise InvalidAudio("Повреждённый WAV") from exc
    if len(pcm) != frames * 2:
        raise InvalidAudio("Неполный WAV")
    import numpy as np
    return rate, np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0


class SherpaRecognize:
    def __init__(self, model_dir: Path):
        import sherpa_onnx
        required = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
        for name in required:
            if not (model_dir / name).is_file():
                raise FileNotFoundError(f"Нет файла модели {model_dir / name}")
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(model_dir / "encoder.int8.onnx"),
            decoder=str(model_dir / "decoder.int8.onnx"),
            joiner=str(model_dir / "joiner.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=2,
            decoding_method="greedy_search",
            debug=False,
        )
        self._lock = threading.Lock()

    def decode(self, wav: bytes):
        rate, audio = pcm_from_wav(wav)
        with self._lock:
            start = monotonic()
            stream = self._recognizer.create_stream()
            stream.accept_waveform(rate, audio)
            self._recognizer.decode_stream(stream)
            result = str(stream.result.text).strip()
            seconds = monotonic() - start
        return result, seconds


def build_handler(recognizer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NEXUS-local-STT/1.0"
        def log_message(self, fmt, *args):
            LOG.info(fmt, *args)

        def respond(self, code, data):
            blob = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(blob)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self):
            if self.path == '/health':
                return self.respond(200, {"ok": True, "engine": "sherpa", "language": "ru"})
            self.respond(404, {"error": "not found"})

        def do_POST(self):
            if self.path == '/stop':
                self.respond(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path != '/asr':
                return self.respond(404, {"error": "not found"})
            try:
                n = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                n = -1
            if n < 44 or n > MAX_WAV:
                return self.respond(413, {"error": "Размер аудио вне диапазона"})
            raw = self.rfile.read(n)
            if len(raw) != n:
                return self.respond(400, {"error": "Неполный запрос"})
            try:
                text, elapsed = recognizer.decode(raw)
                self.respond(200, {"text": text, "decode_seconds": round(elapsed, 3)})
                LOG.info("STT %.3fs", elapsed)
            except InvalidAudio as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                LOG.exception("Model failure")
                self.respond(503, {"error": "Ошибка локальной модели"})
    return Handler


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--model', required=True)
    cli.add_argument('--port', type=int, default=8766)
    args = cli.parse_args()
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
    print('Loading sherpa model...', flush=True)
    recognizer = SherpaRecognize(Path(args.model))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), build_handler(recognizer))
    print(f'NEXUS local STT ready at 127.0.0.1:{args.port}', flush=True)
    server.serve_forever(poll_interval=0.3)
    server.server_close()


if __name__ == '__main__':
    main()
