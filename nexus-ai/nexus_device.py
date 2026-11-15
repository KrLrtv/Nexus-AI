#!/usr/bin/env python3
"""Termux-side M5StickC Plus2 Wi-Fi, build and flash controls.

This file does not touch Android Wi-Fi settings; it writes only the
M5 firmware's LOCAL, gitignored src/config_local.h.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
FIRMWARE = HOME / "nexus-firmware" / "firmware"
CONFIG = FIRMWARE / "src" / "config_local.h"
BINARY = FIRMWARE / ".pio" / "build" / "m5stickc_plus2" / "firmware.bin"
LOCAL = HOME / ".config" / "nexus"
NAME = "M5StickC Plus2"

def _value(key: str) -> str:
    try:
        content = CONFIG.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    match = re.search(r"^#define\s+" + re.escape(key) + r"\s+(\"(?:\\.|[^\"\\])*\")\s*$",
                      content, re.MULTILINE)
    if not match:
        return ""
    try:
        return json.loads(match.group(1))
    except (ValueError, TypeError):
        return ""

def _ready() -> None:
    if not (FIRMWARE / "platformio.ini").is_file() or not (FIRMWARE / "src/main.cpp").is_file():
        raise RuntimeError("Исходники прошивки отсутствуют: проверь ~/nexus-firmware/firmware")

def _device_token() -> str:
    # Use the very same token the Flask bridge uses.
    import nexusctl
    token = nexusctl.config().get("NEXUS_DEVICE_TOKEN", "")
    if not token:
        token = secrets.token_urlsafe(32)
        nexusctl.save(NEXUS_DEVICE_TOKEN=token)
    if len(token) < 16:
        raise RuntimeError("Токен NEXUS слишком короткий. Измени его в локальной конфигурации.")
    return token

def status() -> None:
    ssid = _value("NEXUS_WIFI_SSID")
    from nexusctl import config
    print("Устройство:", NAME)
    print("Wi-Fi:", ssid if ssid else "не настроен")
    print("Токен моста:", "задан" if config().get("NEXUS_DEVICE_TOKEN") else "отсутствует")
    print("Исходники:", "на месте" if (FIRMWARE / "platformio.ini").is_file() else "не найдены")
    print("Прошивка:", "собрана" if BINARY.is_file() else "не собрана")
    if BINARY.is_file():
        print("Дата сборки:", datetime.fromtimestamp(BINARY.stat().st_mtime).strftime("%d.%m.%Y %H:%M"))
    print("Инструмент tpio:", "найден" if shutil.which("tpio") else "не найден")

def wifi() -> bool:
    _ready()
    if not sys.stdin.isatty():
        print("Изменение Wi-Fi требует интерактивного терминала Termux.")
        return False
    old_ssid = _value("NEXUS_WIFI_SSID")
    old_password = _value("NEXUS_WIFI_PASSWORD")
    print("Изменение Wi-Fi M5StickC Plus2 (не Wi-Fi самого телефона).")
    print("Текущая сеть:", old_ssid or "не настроена")
    ssid = input("SSID (Enter = сохранить прежний): ").strip() or old_ssid
    if not ssid:
        print("SSID пустой; изменений нет.")
        return False
    if len(ssid.encode("utf-8")) > 32 or any(ord(c) < 32 for c in ssid):
        print("SSID должен содержать 1–32 байта UTF-8, без управляющих символов.")
        return False
    hint = "Enter = прежний пароль, " if old_password else ""
    password = getpass.getpass("Пароль Wi-Fi (" + hint + "OPEN = открытая сеть): ")
    if password == "OPEN":
        password = ""
    elif not password and old_password:
        password = old_password
    if password and not (8 <= len(password.encode("utf-8")) <= 63):
        print("Пароль WPA2/WPA3 должен содержать от 8 до 63 байт. Для открытой сети введи OPEN.")
        return False
    if any(ord(ch) < 32 for ch in password):
        print("Пароль содержит управляющие символы; изменения отменены.")
        return False

    token = _device_token()
    def quoted(s):
        return json.dumps(s, ensure_ascii=False)
    content = (
        "// Local NEXUS credentials; never publish or commit this file.\n"
        "#pragma once\n\n"
        "#define NEXUS_WIFI_SSID " + quoted(ssid) + "\n"
        "#define NEXUS_WIFI_PASSWORD " + quoted(password) + "\n"
        '#define NEXUS_BRIDGE_URL "AUTO"\n'
        "#define NEXUS_DEVICE_TOKEN " + quoted(token) + "\n"
    )
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL.chmod(0o700)
    if CONFIG.is_symlink():
        raise RuntimeError("config_local.h является ссылкой: отказано для безопасности.")
    if CONFIG.is_file():
        backdir = LOCAL / "firmware-backups"
        backdir.mkdir(exist_ok=True, mode=0o700)
        backdir.chmod(0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dest = backdir / ("config_local-" + stamp + ".h")
        shutil.copyfile(CONFIG, dest)
        dest.chmod(0o600)
    fd, name = tempfile.mkstemp(dir=CONFIG.parent, prefix=".config_local-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print("Wi-Fi сохранён локально. Пароль и токен не показаны.")
    print("Для применения к M5StickC необходимо собрать и прошить новую прошивку.")
    return True

def _build_tool() -> str:
    _ready()
    tool = shutil.which("tpio")
    if not tool:
        raise RuntimeError("Команда tpio не найдена. Проверь установку PlatformIO.")
    return tool

def build() -> bool:
    tool = _build_tool()
    if not CONFIG.is_file() or not _value("NEXUS_WIFI_SSID"):
        print("Сначала настрой Wi-Fi через меню M5StickC.")
        return False
    if not _device_token():
        return False
    print("Сборка прошивки (без записи на M5StickC)...", flush=True)
    result = subprocess.run([tool, "run", "-e", "m5stickc_plus2", "--build-only"], cwd=FIRMWARE, check=False)
    if result.returncode != 0 or not BINARY.is_file():
        print("Ошибка компиляции. Запись на устройство не выполнялась.")
        return False
    if BINARY.stat().st_mtime + 1 < CONFIG.stat().st_mtime:
        print("Не подтверждена свежесть собранного firmware.bin; прошивать нельзя.")
        return False
    print("Сборка завершена. Firmware:", BINARY)
    return True

def flash() -> bool:
    tool = _build_tool()
    if not CONFIG.is_file() or not _value("NEXUS_WIFI_SSID"):
        print("Сначала настрой Wi-Fi и собери прошивку.")
        return False
    if not BINARY.is_file() or BINARY.stat().st_size < 16384:
        print("Нет готовой прошивки. Выполни сборку.")
        return False
    if BINARY.stat().st_mtime + 1 < CONFIG.stat().st_mtime:
        print("После сборки менялись настройки Wi-Fi. Сначала пересобери прошивку.")
        return False
    if not sys.stdin.isatty():
        print("Для прошивки требуется интерактивное подтверждение.")
        return False
    print("Подключи M5StickC Plus2 к Android через USB OTG.")
    print("ВНИМАНИЕ: будет перезаписана флеш-память устройства.")
    choice = input("Для подтверждения введи ПРОШИТЬ: ").strip()
    if choice != "ПРОШИТЬ":
        print("Прошивка отменена.")
        return False
    print("Записываю собранную прошивку через tpio/nrflash...", flush=True)
    result = subprocess.run([tool, "flash"], cwd=FIRMWARE, check=False)
    if result.returncode:
        print("Ошибка прошивки; проверь USB-разрешение Android и кабель.")
        return False
    print("M5StickC Plus2 прошит. Устройство должно перезагрузиться.")
    return True

def build_flash() -> bool:
    return build() and flash()

def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS: настройки и прошивка M5StickC Plus2")
    parser.add_argument("action", choices=["status", "wifi", "build", "flash", "build-flash"])
    args = parser.parse_args()
    try:
        return 0 if globals()[args.action.replace("-", "_")]() is not False else 1
    except (KeyboardInterrupt, EOFError):
        print("\nОтменено")
        return 130
    except (OSError, RuntimeError) as exc:
        print("Ошибка:", exc)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
