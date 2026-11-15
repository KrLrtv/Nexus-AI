#!/usr/bin/env bash
# NEXUS AI v0.1.5-alpha bootstrap for F-Droid Termux.
# Usage: bash install.sh (or download this script from the pinned GitHub release tag).
set -Eeuo pipefail
REPO='KrLrtv/Nexus-AI'
TAG='v0.1.5-alpha'
BASE="https://raw.githubusercontent.com/${REPO}/${TAG}"
ARCHIVE="https://codeload.github.com/${REPO}/zip/refs/tags/${TAG}"

if [[ "${PREFIX:-}" != '/data/data/com.termux/files/usr' ]]; then
  echo 'Запусти установку в Termux (не внутри Ubuntu PRoot).' >&2
  exit 1
fi
command -v curl >/dev/null 2>&1 || { echo 'Сначала: pkg install curl' >&2; exit 1; }
command -v bash >/dev/null 2>&1 || exit 1
TMP="$(mktemp "${TMPDIR:-$PREFIX/tmp}/nexus-bootstrap-XXXXXX.sh")"
trap 'rm -f "$TMP"' EXIT
curl -fL --retry 3 --connect-timeout 15 -o "$TMP" "$BASE/scripts/termux-install.sh"
# Uses the source snapshot of the same immutable release tag as this bootstrap.
if [[ -f "$HOME/nexus-ai/nexusctl.py" && -f "$HOME/nexus-firmware/firmware/platformio.ini" ]]; then
  echo '[NEXUS] Обнаружена незавершённая или прежняя установка; сохраняю локальные настройки.'
  bash "$TMP" finish "$ARCHIVE"
else
  bash "$TMP" install "$ARCHIVE"
fi
