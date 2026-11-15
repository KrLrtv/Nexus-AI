#!/usr/bin/env bash
# Pre-release safety guard.
# This is not the full environment installer; replace with tested implementation before release.
set -Eeuo pipefail
echo "[NEXUS] The installer for this alpha source tree is not yet validated."
echo "[NEXUS] Do not delete your existing Termux installation."
echo "[NEXUS] Once source import and full Android installation tests pass, this script will be enabled."
exit 3
