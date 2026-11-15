#!/usr/bin/env python3
"""NEXUS source-only release updater for Termux.

Updates the application and firmware *sources*, not the M5 flash chip.
Preserves local secrets and restores the previous source tree on failure.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
REPO = "KrLrtv/Nexus-AI"
MANIFEST = "https://raw.githubusercontent.com/" + REPO + "/main/releases/channel.json"
CACHE = HOME / ".cache" / "nexus"
CFG = HOME / ".config" / "nexus"
APP = HOME / "nexus-ai"
FW = HOME / "nexus-firmware" / "firmware"
VERSION_FILE = CFG / "installed_version"
MAX_ARCHIVE = 80 * 1024 * 1024
MAX_EXPANDED = 320 * 1024 * 1024


def validate_tag(value: str) -> str:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc[0-9]+))?", value):
        raise ValueError("Некорректный тег релиза")
    return value


def installed_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "неизвестна"
    except OSError:
        return "неизвестна"


def http_download(url: str, target: Path, limit: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-Termux-Updater/1"})
    with urllib.request.urlopen(req, timeout=35) as response, target.open("wb") as f:
        if int(response.headers.get("Content-Length", "0")) > limit:
            raise ValueError("Слишком большой файл для обновления")
        total = 0
        while True:
            data = response.read(65536)
            if not data:
                break
            total += len(data)
            if total > limit:
                raise ValueError("Превышен лимит загрузки")
            f.write(data)


def target_release(override: str | None) -> str:
    if override:
        return validate_tag(override)
    request = urllib.request.Request(MANIFEST, headers={"User-Agent": "NEXUS-Termux-Updater/1"})
    with urllib.request.urlopen(request, timeout=20) as resp:
        if int(resp.headers.get("Content-Length", "0")) > 4096:
            raise ValueError("Слишком большой манифест версии")
        info = json.loads(resp.read(4097))
    if info.get("repository") != REPO:
        raise ValueError("Недостоверный адрес репозитория в манифесте")
    return validate_tag(info["tag"])


def extract_sources(archive: Path, dest: Path) -> tuple[Path, Path]:
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if len(members) > 12000:
            raise ValueError("Слишком много файлов в ZIP")
        root = None
        seen = set()
        total = 0
        for item in members:
            name = item.filename.replace("\\", "/")
            parts = name.split("/")
            if (not name or name.startswith("/") or ".." in parts or "." in parts
                    or ":" in name or "\x00" in name):
                raise ValueError("Небезопасный путь внутри архива")
            if root is None:
                root = parts[0]
            if root != parts[0] or name in seen:
                raise ValueError("Неоднозначная структура архива")
            seen.add(name)
            mode = stat.S_IFMT(item.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("Ссылки и специальные файлы в ZIP запрещены")
            if item.file_size > 12 * 1024 * 1024:
                raise ValueError("Слишком большой файл исходников")
            total += item.file_size
            if total > MAX_EXPANDED:
                raise ValueError("Превышен допустимый размер распаковки")
            if len(parts) < 3 or item.is_dir():
                continue
            rel = Path(*parts[1:])
            if not (rel.parts[0] == "nexus-ai"
                    or rel.parts[:2] == ("nexus-firmware", "firmware")):
                continue
            if (rel.name in {"config_local.h", ".env", "secrets.env", "credentials.json"}
                    or rel.suffix.lower() in {".key", ".pem", ".bin", ".uf2"}
                    or ".pio" in rel.parts or "__pycache__" in rel.parts):
                raise ValueError("В исходном релизе обнаружен локальный секрет или сборочный файл")
            to = dest / rel
            to.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item, "r") as src, to.open("xb") as dst:
                shutil.copyfileobj(src, dst, 64 * 1024)
    source_app = dest / "nexus-ai"
    source_fw = dest / "nexus-firmware" / "firmware"
    required = [
        source_app / "nexusctl.py",
        source_app / "nexus_tui.py",
        source_app / "nexus_device.py",
        source_app / "nexus_update.py",
        source_app / "android_bridge" / "app.py",
        source_app / "android_bridge" / "supervisor.py",
        source_fw / "platformio.ini",
        source_fw / "src" / "main.cpp",
    ]
    missing = [str(p.relative_to(dest)) for p in required if not p.is_file()]
    if missing:
        raise ValueError("Неполный релиз, отсутствуют: " + ", ".join(missing))
    for path in source_app.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return source_app, source_fw


def record_version(tag: str) -> None:
    CFG.mkdir(parents=True, exist_ok=True)
    CFG.chmod(0o700)
    tmp = CFG / "installed_version.tmp"
    with tmp.open("w", encoding="utf-8") as f:
        f.write(tag + "\n")
    tmp.chmod(0o600)
    os.replace(tmp, VERSION_FILE)


def update(tag: str, source_archive: Path | None = None) -> bool:
    if not (APP / "nexusctl.py").is_file() or not (FW / "platformio.ini").is_file():
        raise RuntimeError("Исходники NEXUS не установлены. Сначала закончи установку из GitHub.")
    current = installed_version()
    print("NEXUS:", current, "->", tag, flush=True)
    if current == tag:
        print("Установлена актуальная версия.", flush=True)
        return False

    CACHE.mkdir(parents=True, exist_ok=True)
    CACHE.chmod(0o700)
    # All staging paths are on Termux HOME's filesystem so os.replace is atomic.
    with tempfile.TemporaryDirectory(prefix="update-", dir=CACHE) as temp:
        work = Path(temp)
        archive = work / "release.zip"
        url = "https://codeload.github.com/" + REPO + "/zip/refs/tags/" + tag
        if source_archive is not None:
            print("Проверяю локальный архив релиза...", flush=True)
            if source_archive.stat().st_size > MAX_ARCHIVE:
                raise ValueError("Слишком большой архив")
            shutil.copyfile(source_archive, archive)
        else:
            print("Скачиваю релиз", tag, "...", flush=True)
            http_download(url, archive, MAX_ARCHIVE)
        staged_app, staged_fw = extract_sources(archive, work / "extracted")
        # Keep Wi-Fi SSID/password and the shared device token.
        config_local = FW / "src" / "config_local.h"
        if config_local.is_symlink():
            raise RuntimeError("Отказ: локальная конфигурация прошивки является ссылкой")
        if config_local.is_file():
            shutil.copyfile(config_local, staged_fw / "src" / "config_local.h")
            (staged_fw / "src" / "config_local.h").chmod(0o600)

        # Snapshot the existing source tree for manual or automatic rollback.
        backup = CACHE / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup.mkdir(parents=True, mode=0o700)
        backup.chmod(0o700)
        old_app = backup / "nexus-ai"
        old_fw = backup / "firmware"
        from nexusctl import managed_pid, request_status, start, stop
        managed = managed_pid()
        if request_status() and not managed:
            raise RuntimeError("NEXUS работает без управления nexusctl. Останови старый сервер перед обновлением.")
        running = bool(managed)
        if running:
            print("Останавливаю NEXUS...", flush=True)
            stop()
            if managed_pid() or request_status():
                raise RuntimeError("Не удалось остановить сервер. Обновление отменено.")
        moved_app = moved_fw = installed_app = installed_fw = False
        success = False
        try:
            APP.rename(old_app)
            moved_app = True
            FW.rename(old_fw)
            moved_fw = True
            staged_app.rename(APP)
            installed_app = True
            staged_fw.rename(FW)
            installed_fw = True
            record_version(tag)
            success = True
        finally:
            if not success:
                if installed_app and APP.exists():
                    shutil.rmtree(APP)
                if installed_fw and FW.exists():
                    shutil.rmtree(FW)
                if moved_app and old_app.exists():
                    old_app.rename(APP)
                if moved_fw and old_fw.exists():
                    old_fw.rename(FW)
                if running:
                    start()
        print("Код обновлён. Предыдущие исходники сохранены в", backup, flush=True)
        print("Локальные ключи, Wi-Fi и личная память сохранены.", flush=True)
        if running:
            print("Перезапускаю NEXUS...", flush=True)
            start()
            if not request_status():
                print("ВНИМАНИЕ: сервер не подтвердил запуск. Проверь nexus logs.", flush=True)
        print("Для применения новой прошивки M5 зайди в меню: M5StickC -> Собрать и прошить.")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновление исходников NEXUS AI из GitHub Releases")
    parser.add_argument("--tag", default=None, help="Точный тег вместо актуального канала обновлений")
    parser.add_argument("--check", action="store_true", help="Проверить версию без установки")
    parser.add_argument("--archive", type=Path, default=None, help="Локальный исходный ZIP (для восстановления установки)")
    args = parser.parse_args()
    try:
        target = target_release(args.tag)
        if args.check:
            print("Установлена:", installed_version(), "| Доступна:", target)
            return 0
        update(target, args.archive)
        return 0
    except (OSError, RuntimeError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print("Обновление не выполнено:", exc, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
