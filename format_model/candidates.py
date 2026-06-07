#!/usr/bin/env python3
"""
format_model/candidates.py — сбор кандидатов для обучающей выборки RAG.

Логика:
  Когда Gemini или RAG успешно исправляет верстку (bad → good),
  пара сохраняется в candidates.jsonl, а НЕ сразу в examples.jsonl.

  Прямая запись в examples.jsonl без ревью опасна:
  модель иногда ошибается, и плохой пример испортит всю выборку.

  review_candidates.py позволяет просмотреть кандидатов и одобрить (a)
  или отклонить (s) каждый — одобренные переносятся в examples.jsonl.

Формат записи candidates.jsonl:
  {"bad": "...", "good": "...", "tag": "...", "source": "gemini|rag", "ts": "ISO8601"}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CANDIDATES_PATH = Path(
    os.environ.get("FORMAT_CANDIDATES_PATH", "format_model/candidates.jsonl")
).resolve()

EXAMPLES_PATH = Path(
    os.environ.get("FORMAT_EXAMPLES_PATH", "format_model/examples.jsonl")
).resolve()

# Максимум кандидатов в файле — защита от бесконечного роста
MAX_CANDIDATES = int(os.environ.get("FORMAT_MAX_CANDIDATES", "500"))


def _bad_key(bad: str) -> str:
    """Ключ дедупликации — первые 120 символов плохого текста."""
    return bad.strip()[:120]


def _already_known(bad: str, paths: list[Path]) -> bool:
    """True если bad уже есть в candidates.jsonl или examples.jsonl."""
    key = _bad_key(bad)
    for path in paths:
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if _bad_key(str(row.get("bad", ""))) == key:
                            return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


def save_candidate(
    bad: str,
    good: str,
    *,
    source: str = "gemini",
    tag: str = "auto",
) -> bool:
    """
    Сохраняет пару bad/good в candidates.jsonl.

    Возвращает True если запись добавлена, False если пропущена (дубль / лимит).
    Никогда не бросает исключение — не должна прерывать публикацию.
    """
    bad = (bad or "").strip()
    good = (good or "").strip()
    if not bad or not good or bad == good:
        return False

    try:
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Не дублируем: проверяем и в кандидатах, и в готовых примерах
        if _already_known(bad, [CANDIDATES_PATH, EXAMPLES_PATH]):
            return False

        # Не переполняем файл
        if CANDIDATES_PATH.exists():
            line_count = sum(1 for _ in open(CANDIDATES_PATH, encoding="utf-8"))
            if line_count >= MAX_CANDIDATES:
                print(
                    f"  [candidates] лимит {MAX_CANDIDATES} достигнут — "
                    "запустите review_candidates.py для очистки"
                )
                return False

        record = json.dumps(
            {
                "bad": bad,
                "good": good,
                "tag": tag,
                "source": source,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        with open(CANDIDATES_PATH, "a", encoding="utf-8") as f:
            f.write(record + "\n")
        return True

    except Exception as e:
        print(f"  [candidates] ошибка записи: {e}")
        return False


def pending_count() -> int:
    """Количество неразобранных кандидатов."""
    if not CANDIDATES_PATH.exists():
        return 0
    try:
        return sum(1 for line in open(CANDIDATES_PATH, encoding="utf-8") if line.strip())
    except Exception:
        return 0
