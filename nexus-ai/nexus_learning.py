#!/usr/bin/env python3
"""Private NEXUS 2.0 learning controls from the existing Termux CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The Python source project is installed in ~/nexus-ai.
sys.path.insert(0, str(Path(__file__).resolve().parent / "android_bridge"))
from learning20 import STORE

def show_status() -> None:
    s = STORE.stats()
    print("NEXUS 2.0 · ЛОКАЛЬНОЕ ОБУЧЕНИЕ")
    print("Записей памяти:", s["facts"])
    print("Оценок ответов:", s["feedback"])
    print("Исправлений:", s["corrections"])
    print("Передача воспоминаний в Gemini/OpenRouter:",
          "ВКЛЮЧЕНА" if s["cloud_personalization"] else "ВЫКЛЮЧЕНА")
    print("Память хранится только на телефоне, пока облачная персонализация выключена.")

def print_rows(records) -> None:
    if not records:
        print("Нет подходящих воспоминаний.")
    for r in records:
        print(f'{r["id"]}. [{r["kind"]}] {r["text"]}')

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexus learn",
        description="Самообучение NEXUS: память, обратная связь, локальная персонализация")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="Статистика памяти")
    list_cmd = sub.add_parser("list", help="Показать записи")
    list_cmd.add_argument("--limit", type=int, default=15)
    search = sub.add_parser("search", help="Найти записи по смысловым словам")
    search.add_argument("query")
    remember = sub.add_parser("remember", help="Сохранить явно указанный факт")
    remember.add_argument("text")
    remember.add_argument("--kind", choices=("fact","preference"), default="fact")
    forget = sub.add_parser("forget", help="Удалить запись по ID")
    forget.add_argument("id", type=int)
    share = sub.add_parser("sharing", help="Передача релевантной памяти в облачный ИИ")
    share.add_argument("value", choices=("on","off","status"))
    sub.add_parser("clear", help="Удалить все воспоминания и оценки с подтверждением")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "status"):
            show_status()
        elif args.command == "list":
            print_rows(STORE.list_facts(args.limit))
        elif args.command == "search":
            print_rows(STORE.recall(args.query, 8))
        elif args.command == "remember":
            identifier = STORE.remember(args.text, args.kind)
            print("Сохранена локальная запись:", identifier)
        elif args.command == "forget":
            print("Запись удалена." if STORE.forget(args.id) else "Запись не найдена.")
        elif args.command == "sharing":
            if args.value != "status":
                STORE.set_sharing(args.value == "on")
            print("Облачная персонализация:",
                  "ВКЛЮЧЕНА" if STORE.sharing() else "ВЫКЛЮЧЕНА")
            if STORE.sharing():
                print("Релевантные сохранённые факты могут входить в запросы к Gemini/OpenRouter.")
        elif args.command == "clear":
            if not sys.stdin.isatty():
                print("Очистка требует интерактивного терминала.")
                return 2
            if input("Удалить ВСЮ память NEXUS 2.0 и оценки? Введи УДАЛИТЬ: ").strip() != "УДАЛИТЬ":
                print("Очистка отменена.")
                return 1
            STORE.clear_all()
            print("Память NEXUS 2.0 очищена.")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print("Ошибка локального обучения:", exc, file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
