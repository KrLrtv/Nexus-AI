#!/usr/bin/env python3
"""Pin the public-source preview without touching previous tags or main."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TAG = 'v2.0.7-alpha'
PREVIOUS = 'v2.0.6-alpha'


def replace_assignment(relative, pattern, replacement, allowed):
    path = ROOT / relative
    text = path.read_text(encoding='utf-8')
    matches = re.findall(pattern, text, re.M)
    if len(matches) != 1 or matches[0] not in allowed:
        raise RuntimeError('Unexpected version in ' + relative)
    text, count = re.subn(pattern, lambda _: replacement, text, flags=re.M)
    assert count == 1
    path.write_text(text, encoding='utf-8')


def main():
    replace_assignment('install.sh', r"^TAG='([^']+)'$", "TAG='" + TAG + "'", {PREVIOUS, TAG})
    replace_assignment('scripts/termux-install.sh', r"^VERSION='([^']+)'$", "VERSION='" + TAG[1:] + "'", {PREVIOUS[1:], TAG[1:]})
    for filename in ('channel.json', 'preview.json'):
        target = ROOT / 'releases' / filename
        data = json.loads(target.read_text(encoding='utf-8'))
        if data.get('repository') != 'KrLrtv/Nexus-AI':
            raise RuntimeError('Unexpected repository')
        data.update(tag=TAG, channel='preview', description='NEXUS 2.0.7: autonomous research and validated public-source programs; JSON, RSS, CSV and text; no subject-specific adapters')
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    notes = (ROOT / 'docs/NEXUS_PUBLIC_SOURCES_RELEASE.md').read_text(encoding='utf-8')
    if notes.splitlines()[0] != '# NEXUS AI ' + TAG:
        raise RuntimeError('Unexpected release notes')
    for name in ('RELEASE_NOTES.md', 'V2_RELEASE_NOTES.md'):
        (ROOT / name).write_text(notes, encoding='utf-8')
    readme = ROOT / 'README.md'
    text = readme.read_text(encoding='utf-8').replace(PREVIOUS, TAG)
    marker = '<!-- NEXUS_PUBLIC_SOURCE_PREVIEW -->'
    if marker not in text:
        text = (marker + '\n# NEXUS AI ' + TAG + ' — Public Source Learning\n\n'
                'Предварительное серверное обновление: самостоятельное изучение публичных источников, '
                'проверка реальных ответов и сохранение сценариев. Не универсальное самопрограммирование. '
                'См. RELEASE_NOTES.md и docs/NEXUS_PUBLIC_SOURCES.md. Перепрошивка M5 не нужна.\n\n' + text)
    readme.write_text(text, encoding='utf-8')
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    checksums = []
    for relative in sorted(filter(None, files)):
        path = ROOT / relative
        if relative == 'SHA256SUMS.txt':
            continue
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Unexpected tracked path: ' + relative)
        checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + relative)
    (ROOT / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    print('Prepared', TAG, 'with', len(checksums), 'source checksums.')


if __name__ == '__main__':
    main()
