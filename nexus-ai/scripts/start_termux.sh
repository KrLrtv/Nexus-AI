#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../android_bridge"
if [ -z "${NEXUS_DEVICE_TOKEN:-}" ]; then
  echo 'Set NEXUS_DEVICE_TOKEN before running.' >&2;exit 1
fi
termux-wake-lock || true
trap 'termux-wake-unlock || true' EXIT
python supervisor.py
