#!/usr/bin/env python3
"""
RAG-style layout checker for Telegram text formatting.

This module is intentionally lightweight (stdlib only) so it can run in GitHub Actions
without extra dependencies.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import requests

EXAMPLES_PATH = Path(
    os.environ.get("FORMAT_EXAMPLES_PATH", "format_model/examples.jsonl")
).resolve()
TOP_K = max(1, int(os.environ.get("FORMAT_RAG_TOP_K", "4")))
MAX_EXAMPLE_TEXT = int(os.environ.get("FORMAT_RAG_MAX_EXAMPLE_TEXT", "1200"))
REQUEST_TIMEOUT = int(os.environ.get("FORMAT_RAG_TIMEOUT", "18"))


def _tokenize(text: str) -> set[str]:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.casefold()
    tokens = re.findall(r"[\wА-Яа-яЁёA-Za-zÀ-ÿ-]{3,}", text, flags=re.UNICODE)
    return set(tokens)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_examples(path: Path | None = None) -> list[dict]:
    path = path or EXAMPLES_PATH
    if not path.exists():
        return []

    examples: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            bad = str(row.get("bad") or "").strip()
            good = str(row.get("good") or "").strip()
            if not bad or not good:
                continue
            examples.append(
                {
                    "bad": bad,
                    "good": good,
                    "tag": str(row.get("tag") or "").strip(),
                    "notes": str(row.get("notes") or "").strip(),
                    "tokens": _tokenize(bad),
                }
            )
    return examples


def select_similar_examples(text: str, examples: list[dict], top_k: int = TOP_K) -> list[dict]:
    query_tokens = _tokenize(text)
    scored = []
    for ex in examples:
        score = _jaccard(query_tokens, ex.get("tokens", set()))
        if score <= 0:
            continue
        scored.append((score, ex))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [ex for _, ex in scored[:top_k]]


def _trim_example_text(value: str) -> str:
    value = str(value or "").strip()
    if len(value) <= MAX_EXAMPLE_TEXT:
        return value
    return value[:MAX_EXAMPLE_TEXT].rstrip() + " ..."


def build_layout_prompt(candidate_text: str, selected_examples: list[dict]) -> str:
    rules = (
        "Ты валидатор верстки Telegram parse_mode=HTML.\n"
        "Исправляй ТОЛЬКО форматирование: переносы строк, абзацы, расположение эмодзи, HTML-теги.\n"
        "Не меняй факты, имена, числа, смысл и язык.\n"
        "Разрешенные теги: <b>, </b>, <blockquote>, </blockquote>.\n"
        "Нельзя добавлять ссылки, markdown и другие теги.\n"
        "Верни строго JSON: "
        "{\"ok\":true|false,\"issues\":[\"...\"],\"fixed_text\":\"...\"}.\n"
        "Если всё ок, fixed_text должен быть равен исходному тексту (или косметически эквивалентен).\n"
    )

    few_shots = []
    for idx, ex in enumerate(selected_examples, start=1):
        label = ex.get("tag") or f"example_{idx}"
        notes = ex.get("notes") or "-"
        few_shots.append(
            f"Пример {idx} ({label}; notes: {notes})\n"
            f"BAD:\n{_trim_example_text(ex['bad'])}\n\n"
            f"GOOD:\n{_trim_example_text(ex['good'])}\n"
        )

    examples_block = "\n\n".join(few_shots) if few_shots else "Нет похожих примеров."
    return (
        f"{rules}\n"
        f"Похожие эталонные примеры:\n{examples_block}\n\n"
        f"ТЕКСТ ДЛЯ ПРОВЕРКИ:\n{candidate_text.strip()}\n"
    )


def parse_checker_json(content: str) -> dict | None:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    try:
        raw_obj = json.loads(text)
    except Exception:
        try:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                return None
            raw_obj = json.loads(match.group(0))
        except Exception:
            return None

    if not isinstance(raw_obj, dict):
        return None
    return {
        "ok": bool(raw_obj.get("ok", False)),
        "issues": raw_obj.get("issues") if isinstance(raw_obj.get("issues"), list) else [],
        "fixed_text": str(raw_obj.get("fixed_text") or "").strip(),
    }


def call_openrouter_layout_checker(prompt: str, api_key: str, model: str) -> dict | None:
    if not api_key or not model:
        return None
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return parse_checker_json(content)
    except Exception:
        return None
