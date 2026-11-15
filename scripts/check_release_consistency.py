#!/usr/bin/env python3
"""Prevent accidentally publishing a mismatched NEXUS installer version."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def capture(path, pattern, what):
    content = (ROOT / path).read_text(encoding="utf-8")
    match = re.search(pattern, content, re.MULTILINE)
    if not match:
        raise SystemExit("Missing " + what + " in " + path)
    return match.group(1)


bootstrap_tag = capture("install.sh", r"^TAG='(v[0-9]+\.[0-9]+\.[0-9]+-[a-z0-9]+)'$", "bootstrap tag")
installer_version = capture(
    "scripts/termux-install.sh",
    r"^VERSION='([0-9]+\.[0-9]+\.[0-9]+-[a-z0-9]+)'$",
    "Termux installer version",
)
workflow_tag = capture(
    ".github/workflows/publish-alpha.yml",
    r'^[ \t]*TAG="(v[0-9]+\.[0-9]+\.[0-9]+-[a-z0-9]+)"$',
    "GitHub release workflow tag",
)
channel = json.loads((ROOT / "releases/channel.json").read_text(encoding="utf-8"))
release_note = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8").splitlines()[0]
if channel.get("repository") != "KrLrtv/Nexus-AI":
    raise SystemExit("Wrong update repository in channel.json")
checks = {
    "install.sh": bootstrap_tag,
    "scripts/termux-install.sh": "v" + installer_version,
    "release workflow": workflow_tag,
    "releases/channel.json": channel.get("tag", ""),
    "RELEASE_NOTES.md": release_note.replace("# NEXUS AI ", ""),
}
bad = [f"{name}: {tag}" for name, tag in checks.items() if tag != bootstrap_tag]
if bad:
    raise SystemExit("Mismatched versions: " + "; ".join(bad))
print("Release versions consistent:", bootstrap_tag)
