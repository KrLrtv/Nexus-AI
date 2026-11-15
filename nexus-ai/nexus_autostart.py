"""Opt-in Termux shell startup integration for the NEXUS Control Center.

Touches only the current interactive shell's user rc file. No root, no Termux
services, no background daemon, and never modifies another application's data.
"""
from __future__ import annotations
import os
from pathlib import Path

START="# >>> NEXUS CONTROL CENTER AUTOSTART >>>"
END="# <<< NEXUS CONTROL CENTER AUTOSTART <<<"
BLOCK=(
    START + "\n"
    "# Start NEXUS only in an interactive Termux shell. Disable from the NEXUS menu.\n"
    'if [[ $- == *i* ]] && [[ -z "${NEXUS_MENU_AUTO_ACTIVE:-}" ]] '
    '&& command -v nexus >/dev/null 2>&1; then\n'
    '    NEXUS_MENU_AUTO_ACTIVE=1 nexus\n'
    "fi\n"
    + END + "\n"
)

def shell_profile(home: Path | None = None, shell: str | None = None) -> Path:
    root=home or Path.home()
    name=Path(shell or os.environ.get("SHELL","bash")).name
    if name=="zsh":
        return root / ".zshrc"
    if name=="bash":
        return root / ".bashrc"
    raise RuntimeError("Автозапуск поддерживается только для Bash или Zsh.")

def _read(profile: Path) -> str:
    if profile.is_symlink():
        raise RuntimeError("Файл оболочки является ссылкой. Автозапуск не изменён.")
    return profile.read_text(encoding="utf-8") if profile.exists() else ""

def enabled(home: Path | None = None, shell: str | None = None) -> bool:
    profile=shell_profile(home,shell)
    data=_read(profile)
    return START in data and END in data

def set_enabled(value: bool, home: Path | None = None, shell: str | None = None) -> bool:
    profile=shell_profile(home,shell)
    source=_read(profile)
    if source.count(START) > 1 or source.count(END) > 1:
        raise RuntimeError("Повторяющиеся маркеры автозапуска; файл не изменён.")
    if (START in source) != (END in source):
        raise RuntimeError("Повреждён блок автозапуска; файл не изменён.")
    if START in source:
        before, remainder = source.split(START,1)
        _, after = remainder.split(END,1)
        if after.startswith("\n"):
            after=after[1:]
        source = before.rstrip("\n") + ("\n" if before.strip() and after else "") + after
    if value:
        source=source.rstrip("\n") + ("\n\n" if source.strip() else "") + BLOCK
    profile.parent.mkdir(parents=True,exist_ok=True)
    mode=(profile.stat().st_mode & 0o777) if profile.exists() else 0o600
    tmp=profile.with_name(profile.name+".nexus-tmp")
    if tmp.exists() or tmp.is_symlink():
        raise RuntimeError("Временный файл настроек уже существует; операция остановлена.")
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY, mode)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as file:
            file.write(source)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,profile)
    finally:
        if tmp.exists():
            tmp.unlink()
    return enabled(home,shell)
