#!/usr/bin/env bash
# NEXUS AI bootstrap. Do not claim installation if source is incomplete.
set -Eeuo pipefail
REPO="KrLrtv/Nexus-AI"
REF="${NEXUS_REF:-main}"
BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
if [[ "${PREFIX:-}" != "/data/data/com.termux/files/usr" ]]; then
  echo "[NEXUS] Run in Termux, not in Ubuntu/proot." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "[NEXUS] Please run: pkg install -y curl" >&2
  exit 1
fi
echo "[NEXUS] Checking release completeness..."
required=(
  "nexus-ai/nexusctl.py"
  "nexus-ai/android_bridge/app.py"
  "nexus-ai/android_bridge/planner.py"
  "nexus-ai/android_bridge/supervisor.py"
  "nexus-firmware/firmware/platformio.ini"
  "nexus-firmware/firmware/src/main.cpp"
)
for path in "${required[@]}"; do
  if ! curl --fail --silent --show-error --location --output /dev/null "${BASE}/${path}"; then
    echo "[NEXUS] Source missing: ${path}" >&2
    echo "[NEXUS] Release is not published yet; no changes were made." >&2
    exit 2
  fi
done
echo "[NEXUS] Sources detected. Checking installer..."
tmp="$(mktemp "${TMPDIR:-${PREFIX}/tmp}/nexus-install-XXXXXX.sh")"
trap 'rm -f "$tmp"' EXIT
curl --fail --show-error --location --retry 3 --output "$tmp" "${BASE}/scripts/termux-install.sh"
bash "$tmp" install "https://github.com/${REPO}/archive/refs/heads/${REF}.zip"
