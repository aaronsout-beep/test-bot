#!/usr/bin/env python3
"""
review_candidates.py — интерактивный ревью кандидатов для examples.jsonl.

Запуск:
    python review_candidates.py

Управление:
    a  — approve: перенести в examples.jsonl
    s  — skip: оставить в candidates.jsonl до следующего ревью
    d  — delete: удалить кандидата без переноса
    t  — tag: изменить тег перед approve
    q  — quit: выйти, сохранив прогресс

Результат:
    Одобренные записи → format_model/examples.jsonl
    Пропущенные       → остаются в format_model/candidates.jsonl
    Удалённые         → удаляются без следа

После прогона candidates.jsonl содержит только пропущенные записи.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

CANDIDATES_PATH = Path(
    os.environ.get("FORMAT_CANDIDATES_PATH", "format_model/candidates.jsonl")
)
EXAMPLES_PATH = Path(
    os.environ.get("FORMAT_EXAMPLES_PATH", "format_model/examples.jsonl")
)

# Ширина столбца при выводе diff
WRAP = 72

KNOWN_TAGS = [
    "interview_qa", "speaker_quote", "stats_list", "linebreakes",
    "extralinebreakes", "extraemoji", "extrabold", "extraquote",
    "extrahashtags", "source_brackets",
]

# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции вывода
# ─────────────────────────────────────────────────────────────────────────────

def _hr(char: str = "─", width: int = WRAP) -> str:
    return char * width


def _wrap(text: str, prefix: str = "  ") -> str:
    lines = text.splitlines()
    result = []
    for line in lines:
        if len(line) <= WRAP:
            result.append(prefix + line)
        else:
            result.extend(
                textwrap.wrap(line, width=WRAP, initial_indent=prefix, subsequent_indent=prefix)
            )
    return "\n".join(result)


def _print_candidate(idx: int, total: int, rec: dict) -> None:
    source = rec.get("source", "?")
    tag    = rec.get("tag", "auto")
    ts     = rec.get("ts", "")[:16].replace("T", " ")

    print()
    print(_hr("═"))
    print(f"  Кандидат {idx}/{total}  |  источник: {source}  |  тег: {tag}  |  {ts}")
    print(_hr())
    print("  ── BAD (до исправления) ──")
    print(_wrap(rec.get("bad", ""), "  │ "))
    print()
    print("  ── GOOD (после исправления) ──")
    print(_wrap(rec.get("good", ""), "  │ "))
    print(_hr())
    print("  [a] одобрить → examples.jsonl   [s] пропустить   [d] удалить   [t] изменить тег   [q] выйти")


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "q"


# ─────────────────────────────────────────────────────────────────────────────
# Работа с файлами
# ─────────────────────────────────────────────────────────────────────────────

def _load_candidates() -> list[dict]:
    if not CANDIDATES_PATH.exists():
        return []
    records = []
    with open(CANDIDATES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _save_candidates(records: list[dict]) -> None:
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _already_in_examples(bad: str) -> bool:
    key = bad.strip()[:120]
    if not EXAMPLES_PATH.exists():
        return False
    try:
        with open(EXAMPLES_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    if str(row.get("bad", ""))[:120] == key:
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _append_to_examples(rec: dict, tag: str) -> None:
    EXAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "bad":   rec["bad"].strip(),
        "good":  rec["good"].strip(),
        "tag":   tag,
        "notes": rec.get("notes", f"auto-approved from {rec.get('source','?')}"),
    }
    with open(EXAMPLES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Ревью
# ─────────────────────────────────────────────────────────────────────────────

def _pick_tag(current: str) -> str:
    print()
    print(f"  Текущий тег: {current}")
    print("  Доступные теги:")
    for i, t in enumerate(KNOWN_TAGS, 1):
        print(f"    {i:2d}) {t}")
    print("  Введите номер, произвольный тег или Enter для сохранения текущего:")
    raw = _prompt("  > ")
    if not raw:
        return current
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(KNOWN_TAGS):
            return KNOWN_TAGS[idx]
    return raw or current


def main() -> None:
    candidates = _load_candidates()
    if not candidates:
        print("Нет кандидатов для ревью. Запустите бота — кандидаты появятся автоматически.")
        return

    total    = len(candidates)
    approved = 0
    skipped  = 0
    deleted  = 0

    # Индексы, которые нужно оставить в candidates.jsonl (пропущенные)
    keep: list[int] = []

    for i, rec in enumerate(candidates):
        # Проверяем дубль перед показом
        if _already_in_examples(rec.get("bad", "")):
            print(f"\n  [авто-skip] уже есть в examples.jsonl: {rec.get('bad','')[:60]}")
            deleted += 1
            continue

        _print_candidate(i + 1, total, rec)

        while True:
            choice = _prompt("  > ")

            if choice == "q":
                # Сохраняем текущий и все оставшиеся
                keep.extend(range(i, len(candidates)))
                _save_candidates([candidates[j] for j in keep])
                print(f"\n  Прервано. Одобрено: {approved}, пропущено: {skipped}, удалено: {deleted}")
                print(f"  Осталось в очереди: {len(keep)}")
                return

            elif choice == "a":
                tag = rec.get("tag", "auto")
                _append_to_examples(rec, tag)
                approved += 1
                print(f"  ✅ Добавлено в examples.jsonl с тегом «{tag}»")
                break

            elif choice == "t":
                new_tag = _pick_tag(rec.get("tag", "auto"))
                rec["tag"] = new_tag
                _append_to_examples(rec, new_tag)
                approved += 1
                print(f"  ✅ Добавлено в examples.jsonl с тегом «{new_tag}»")
                break

            elif choice == "s":
                keep.append(i)
                skipped += 1
                print("  ⏭  Пропущено")
                break

            elif choice == "d":
                deleted += 1
                print("  🗑  Удалено")
                break

            else:
                print("  Введите a / s / d / t / q")

    _save_candidates([candidates[j] for j in keep])

    print()
    print(_hr("═"))
    print(f"  Ревью завершён.")
    print(f"  Одобрено:  {approved}  → examples.jsonl")
    print(f"  Пропущено: {skipped}   → остались в candidates.jsonl")
    print(f"  Удалено:   {deleted}")
    n = EXAMPLES_PATH
    if n.exists():
        total_ex = sum(1 for _ in open(n, encoding="utf-8") if _.strip())
        print(f"  Всего примеров в examples.jsonl: {total_ex}")
    print(_hr("═"))


if __name__ == "__main__":
    main()
