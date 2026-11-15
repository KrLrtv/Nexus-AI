#!/usr/bin/env python3
"""Prepare only release metadata for the tested native-learning preview.

Run in a clean checkout. Unknown installer/source revisions are refused. No
application code, private settings, release tags, or remote refs are modified.
"""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parent.parent
TAG = 'v2.0.6-alpha'
VERSION = TAG[1:]
EXPECTED = {
    'install.sh': 'bc9b1c82c23687f213b389bde81fc07146d4aa8c',
    'scripts/termux-install.sh': 'c0dfc621f156951b560dcca7ffc6b6c611f38240',
    'README.md': '495a1e64be9e4623347a8ecf3a73510e0746930f',
    'docs/NEXUS_NATIVE_LEARNING.md': '2498064679ee9e506c60575d2435732f049255d3',
}
NOTES = '''# NEXUS AI v2.0.6-alpha

## Native Learning — обучение внутри обычного запроса

Обычная задача может запускать освоение недостающего способа без отдельного
«Научись»: проверка → включение допустимого навыка → ответ на исходную задачу.
Это экспериментальное обновление существующего сервера, не новая APK.

- Планировщик получает learn_capability; исполнитель использует исходную задачу,
  а не подменённые моделью аргументы. Быстрые информационные ответы сохранены.
- Операция отделяется от данных: «Сделай заглавными: привет» и затем
  «Сделай заглавными: другой текст» используют один проверенный навык.
  Совпадение формулировки нормализованное и точное, не семантическое.
- При нехватке данных достаточно числа, текста в кавычках или «данные: …».
  Ожидание сохраняется локально 10 минут; номер навыка не нужен.
- Голосом/текстом: «включи автообучение», «выключи автообучение»,
  «статус автообучения», «останови обучение». По умолчанию включено в 2.0.6;
  ранее сохранённое отключение не сбрасывается.
- Одно обучение одновременно, до 8 новых обучений за скользящие 24 часа,
  до 3 запросов генерации/исправления на обучение; пауза после ошибки.
  Завершённые действия и прерванные задания не повторяются автоматически.

## Обновление через меню Termux

Уже установлена 2.x: NEXUS → «Обновить NEXUS».
Переход с 1.x: «Версии · NEXUS 2.0 · откат» → установка NEXUS 2.0;
подтверди переход вводом 2.0. Новый тег доступен после обновления указателя
releases/preview.json в main; канал 1.x остаётся отдельным.

Ключи, память и локальные настройки Wi-Fi сохраняет существующий обновлятор.
M5 автоматически не перепрошивается; для этой серверной доработки новая
прошивка не требуется. Сохраняется одна предыдущая версия для отката.
Чистый install.sh в этом теге закреплён именно за v2.0.6-alpha.

## Проверки и ограничения

Перед публикацией выполняется полный набор offline-тестов nexus-ai/tests,
проверка синтаксиса, целостности метаданных и известных шаблонов секретов.
Протокол тестов, архив исходников и SHA256SUMS приложены к релизу.

Автоматическое включение ограничено существующим интерпретатором выражений
и пятью адаптерами чтения. Новые системные адаптеры остаются черновиками.
Нет исполнения произвольного Python, установки пакетов, автоматической
прошивки или обхода подтверждения отправки SMS. Это не универсальное
самопрограммирование. Правильность модели не гарантируется набором тестов.

Живой Gemini, Android/Termux, USB и M5 физически не испытывались.
Облачные вызовы в тестах подменены; прошедший CI не заменяет проверку телефона.
Генератор не получает явно отделённые данные, но обычный облачный планировщик
может получить полный текст поручения. Это не полностью локальный режим.
Отмена кооперативная: уже начатый HTTP-запрос или чтение может завершиться.
Подробности: docs/NEXUS_NATIVE_LEARNING.md.
'''


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def prepare() -> None:
    replacements = {}
    for name, expected in EXPECTED.items():
        path = ROOT / name
        data = path.read_bytes()
        if blob_sha(data) != expected:
            raise SystemExit('Refusing unknown source revision: ' + name)
        text = data.decode('utf-8')
        if name == 'scripts/termux-install.sh':
            text = text.replace("VERSION='0.1.5-alpha'", "VERSION='" + VERSION + "'")
        elif name == 'install.sh':
            text = text.replace('v0.1.5-alpha', TAG)
        elif name == 'README.md':
            text = text.replace('v0.1.5-alpha', TAG)
            text = ('> **NEXUS 2.0.6 Native Learning — предварительный релиз.** '
                    'Освоение навыков встроено в обычные запросы. '
                    'Обновление через меню; ограничения и проверка — в '
                    '`RELEASE_NOTES.md`. Это не новая APK и не универсальное '
                    'самопрограммирование.\n\n' + text)
        else:
            text = text.replace('# Native learning preview (on top of v2.0.5-alpha)',
                                '# NEXUS v2.0.6-alpha — Native Learning')
        replacements[name] = text
    replacements['RELEASE_NOTES.md'] = NOTES
    replacements['V2_RELEASE_NOTES.md'] = NOTES
    manifest = {'repository': 'KrLrtv/Nexus-AI', 'channel': 'preview',
                'tag': TAG, 'description': 'Native Learning: automatic bounded skill acquisition in ordinary requests'}
    for name in ('releases/channel.json', 'releases/preview.json'):
        replacements[name] = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    for name, text in replacements.items():
        (ROOT / name).write_text(text, encoding='utf-8')
    names = set(subprocess.check_output(
        ['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    names.update(replacements)
    names.discard('')
    names.discard('SHA256SUMS.txt')
    rows = []
    for name in sorted(names):
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit('Not a regular release source: ' + name)
        rows.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + name)
    (ROOT / 'SHA256SUMS.txt').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    print('Prepared', TAG, 'metadata;', len(rows), 'source checksums.')


if __name__ == '__main__':
    prepare()
