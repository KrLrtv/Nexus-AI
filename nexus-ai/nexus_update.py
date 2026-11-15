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



# NEXUS stores at most ONE usable previous source revision. Build caches are
# excluded from snapshots; all other local files remain untouched.
BACKUP_NAME = re.compile(r"^[0-9]{8}T[0-9]{12}Z$")
BACKUP_IGNORE = shutil.ignore_patterns(".pio", ".tpio", "__pycache__", ".git", ".venv")


def backup_dir() -> Path:
    root = CACHE / "backups"
    if root.is_symlink():
        raise RuntimeError("Каталог резервных копий является ссылкой; остановлено.")
    return root


def backup_candidates() -> list[Path]:
    root = backup_dir()
    if not root.is_dir():
        return []
    result = []
    for path in root.iterdir():
        if not BACKUP_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            continue
        if ((path / "nexus-ai" / "nexusctl.py").is_file()
                and (path / "firmware" / "platformio.ini").is_file()):
            result.append(path)
    return sorted(result, key=lambda p: p.name, reverse=True)


def backup_version(path: Path) -> str:
    meta = path / "metadata.json"
    try:
        version = json.loads(meta.read_text(encoding="utf-8")).get("version", "неизвестна")
        return version if (version == "неизвестна" or
                           (isinstance(version, str) and validate_tag(version))) else "неизвестна"
    except (OSError, ValueError, TypeError):
        return "неизвестна"


def write_backup_metadata(path: Path, version: str) -> None:
    meta = path / "metadata.json"
    with meta.open("x", encoding="utf-8") as f:
        json.dump({"version": version, "created_utc": datetime.now(timezone.utc).isoformat()}, f)
        f.write("\n")
    meta.chmod(0o600)


def prune_backups(keep: int = 1) -> None:
    """Delete only older managed source snapshots, never unrelated directories."""
    try:
        snapshots = backup_candidates()
        for path in snapshots[keep:]:
            shutil.rmtree(path)
            print("Удалена старая резервная копия:", path.name)
    except OSError as exc:
        print("Внимание: старые копии не удалось очистить:", exc, file=sys.stderr)


def trim_build_cache(snapshot: Path) -> None:
    # PlatformIO build outputs are reproducible and can consume hundreds of MB.
    build = snapshot / "firmware" / ".pio"
    if build.is_dir() and not build.is_symlink():
        try:
            shutil.rmtree(build)
        except OSError as exc:
            print("Не удалось очистить кэш сборки из копии:", exc, file=sys.stderr)


def preserve_wifi_config(staged_fw: Path) -> None:
    src = FW / "src" / "config_local.h"
    if src.is_symlink():
        raise RuntimeError("Локальный файл Wi-Fi является ссылкой; отказано.")
    if src.is_file():
        dest = staged_fw / "src" / "config_local.h"
        if dest.is_symlink():
            raise RuntimeError("Целевой файл Wi-Fi является ссылкой; отказано.")
        shutil.copyfile(src, dest)
        dest.chmod(0o600)


def activate_sources(staged_app: Path, staged_fw: Path, tag: str, previous: str) -> None:
    """Atomically swap source directories with an undo snapshot on same filesystem."""
    if not (APP / "nexusctl.py").is_file() or not (FW / "platformio.ini").is_file():
        raise RuntimeError("Исходники NEXUS отсутствуют; переключение версии невозможно.")
    backup_root = backup_dir()
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_root.chmod(0o700)
    backup = backup_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup.mkdir(mode=0o700)
    write_backup_metadata(backup, previous)
    old_app, old_fw = backup / "nexus-ai", backup / "firmware"

    from nexusctl import managed_pid, request_status, start, stop
    if request_status() and not managed_pid():
        shutil.rmtree(backup)
        raise RuntimeError("Сервер NEXUS запущен вне меню. Останови его перед сменой версии.")
    running = bool(managed_pid())
    if running:
        stop()
        if managed_pid() or request_status():
            shutil.rmtree(backup)
            raise RuntimeError("Не удалось остановить сервер; версия не изменена.")

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
            try:
                record_version(previous)
            except OSError:
                pass
            if running:
                start()
    trim_build_cache(backup)
    prune_backups(keep=1)
    print("Исходники переключены:", previous, "→", tag)
    print("Версия для отката:", previous, "| сохранена одна копия.")
    if running:
        start()
        if not request_status():
            print("ВНИМАНИЕ: NEXUS не ответил после перезапуска. Проверь nexus logs.")
    print("Пароли, память и Wi-Fi сохранены. M5 не перепрошивался.")


def show_backups() -> None:
    found = backup_candidates()
    if not found:
        print("Резервной копии нет.")
        return
    for snapshot in found:
        print("Откат возможен к", backup_version(snapshot), "|", snapshot.name)
    print("Хранится максимум одна последняя резервная копия после обновления.")


def clear_backups(confirm: bool = False) -> bool:
    snapshots = backup_candidates()
    if not snapshots:
        print("Резервных копий нет.")
        return False
    if not confirm:
        if not sys.stdin.isatty() or input("Удалить резервные копии? Откат станет невозможен [д/Н]: ").strip().lower() not in ("д", "да", "y", "yes"):
            print("Очистка отменена.")
            return False
    for snapshot in snapshots:
        shutil.rmtree(snapshot)
    print("Резервные копии удалены. Рабочая версия NEXUS не изменена.")
    return True


def rollback(approve: bool = False) -> bool:
    """Restore the latest source revision offline and keep current as the next undo."""
    snapshots = backup_candidates()
    if not snapshots:
        print("Откат невозможен: нет сохранённой предыдущей версии.")
        return False
    snapshot = snapshots[0]
    target = backup_version(snapshot)
    previous = installed_version()
    print("Откат NEXUS:", previous, "→", target, flush=True)
    if not approve:
        if not sys.stdin.isatty() or input("Подтверди откат [д/Н]: ").strip().lower() not in ("д", "да", "y", "yes"):
            print("Откат отменён.")
            return False
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rollback-", dir=CACHE) as temporary:
        temp = Path(temporary)
        staged_app = temp / "nexus-ai"
        staged_fw = temp / "firmware"
        shutil.copytree(snapshot / "nexus-ai", staged_app, symlinks=True,
                        ignore=BACKUP_IGNORE)
        shutil.copytree(snapshot / "firmware", staged_fw, symlinks=True,
                        ignore=BACKUP_IGNORE)
        if not (staged_app / "nexusctl.py").is_file() or not (staged_fw / "src/main.cpp").is_file():
            raise RuntimeError("Резервная копия повреждена: откат отменён.")
        for path in staged_app.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        preserve_wifi_config(staged_fw)
        activate_sources(staged_app, staged_fw, target, previous)
    print("Откат завершён. Для обновления интерфейса снова открой nexus.")
    return True


def update(tag: str, source_archive: Path | None = None) -> bool:
    if not (APP / "nexusctl.py").is_file() or not (FW / "platformio.ini").is_file():
        raise RuntimeError("Исходники NEXUS не установлены. Сначала закончи установку из GitHub.")
    current = installed_version()
    print("NEXUS:", current, "->", tag, flush=True)
    if current == tag and source_archive is None:
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
        preserve_wifi_config(staged_fw)
        activate_sources(staged_app, staged_fw, tag, current)
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновление исходников NEXUS AI из GitHub Releases")
    parser.add_argument("--tag", default=None, help="Точный тег вместо актуального канала обновлений")
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--check", action="store_true", help="Проверить версию без установки")
    commands.add_argument("--rollback", action="store_true", help="Восстановить предыдущую версию без интернета")
    commands.add_argument("--backups", action="store_true", help="Показать доступные резервные копии")
    commands.add_argument("--clear-backups", action="store_true", help="Удалить все резервные копии")
    parser.add_argument("--archive", type=Path, default=None, help="Локальный исходный ZIP (для восстановления установки)")
    args = parser.parse_args()
    try:
        if args.rollback:
            return 0 if rollback() else 1
        if args.backups:
            show_backups()
            return 0
        if args.clear_backups:
            return 0 if clear_backups() else 1
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
