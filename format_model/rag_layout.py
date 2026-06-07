#!/usr/bin/env python3
"""
RAG-style layout checker for Telegram text formatting.

Проверка верстки выполняется через Gemini API (не OpenRouter).
Модуль намеренно минималистичен (только stdlib + requests),
чтобы работать в GitHub Actions без лишних зависимостей.

Улучшения:
  - Поиск по структуре текста (сигнатуры), а не только по содержанию (Jaccard).
  - Теги из examples.jsonl участвуют в поиске и влияют на оценку.
  - TOP_K=2, MAX_EXAMPLE_TEXT=600 — короткий prompt, модель не теряет формат.
  - Парсер JSON устойчив к тексту после/до JSON-блока и к {} в fixed_text.
  - Успешные исправления сохраняются в candidates.jsonl для ревью.
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

# TOP_K=2 — двух коротких примеров достаточно; длинный prompt ухудшает качество.
TOP_K = max(1, int(os.environ.get("FORMAT_RAG_TOP_K", "2")))
# 600 символов на пример — хватает для демонстрации шаблона.
MAX_EXAMPLE_TEXT = int(os.environ.get("FORMAT_RAG_MAX_EXAMPLE_TEXT", "600"))
REQUEST_TIMEOUT = int(os.environ.get("FORMAT_RAG_TIMEOUT", "18"))

# Gemini — единственный провайдер для RAG-проверки верстки.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta",
).rstrip("/")
LAYOUT_AI_MODEL = os.environ.get("LAYOUT_AI_MODEL", "gemini-2.5-flash-lite").strip()

# ─────────────────────────────────────────────────────────────────────────────
# СТРУКТУРНЫЕ СИГНАТУРЫ
# Поиск по смыслу (Jaccard слов) плохо работает для верстки: два поста об
# абсолютно разных игроках могут иметь одинаковую проблему оформления.
# Основной вес отдаётся структурным сигнатурам — паттернам разметки.
# ─────────────────────────────────────────────────────────────────────────────

# (имя, паттерн, вес) — вес определяет вклад в итоговую оценку схожести.
_STRUCTURAL_PATTERNS: list[tuple[str, re.Pattern, float]] = [
    # Цитаты / спикеры
    ("has_speaker_emoji",   re.compile(r"[🎙🗣]"),                        0.25),
    ("has_blockquote",      re.compile(r"<blockquote>"),                  0.25),
    ("has_guillemets",      re.compile(r"[«»]"),                          0.15),
    ("has_qa_emoji",        re.compile(r"❓"),                            0.20),
    # Списки / статистика
    ("has_bullet",          re.compile(r"^[•·✅☑️✔]", re.M),              0.20),
    ("has_numbered_list",   re.compile(r"^\d+[.)]\s", re.M),              0.20),
    ("has_stats_emoji",     re.compile(r"[⚽🅰️🔑🎯⚡📊]"),               0.15),
    ("has_flag_emoji",      re.compile(r"[\U0001F1E0-\U0001F1FF]"),       0.20),
    ("has_dash_list",       re.compile(r"(?:—|–)\s*\S"),                  0.15),
    # Источники / хэштеги
    ("has_source_brackets", re.compile(r"\[[\w\s@.-]{2,30}\]"),           0.20),
    ("has_hashtag_body",    re.compile(r"(?<!\n)#\w+"),                   0.20),
    # Форматирование
    ("has_bold",            re.compile(r"<b>"),                           0.10),
    ("has_colon_heading",   re.compile(r":\s*$", re.M),                   0.15),
    ("has_alert_emoji",     re.compile(r"[🚨❗‼️]"),                      0.10),
    # Перенос внутри числа/времени (типичный баг: «18:\n30»)
    ("has_broken_number",   re.compile(r"\d:\s*\n\s*\d"),                 0.30),
]

# Теги из examples.jsonl → сигнатуры, которые они усиливают при совпадении.
_TAG_TO_SIGNATURES: dict[str, set[str]] = {
    "interview_qa":     {"has_speaker_emoji", "has_qa_emoji", "has_blockquote", "has_guillemets"},
    "speaker_quote":    {"has_speaker_emoji", "has_blockquote", "has_guillemets", "has_colon_heading"},
    "stats_list":       {"has_stats_emoji", "has_bullet", "has_numbered_list", "has_bold"},
    "linebreakes":      {"has_numbered_list", "has_bullet", "has_flag_emoji", "has_dash_list",
                         "has_broken_number", "has_stats_emoji"},
    "extralinebreakes": {"has_blockquote", "has_speaker_emoji", "has_alert_emoji", "has_bold"},
    "extraemoji":       {"has_alert_emoji", "has_speaker_emoji"},
    "extrabold":        {"has_bold", "has_broken_number"},
    "extraquote":       {"has_blockquote", "has_guillemets", "has_speaker_emoji"},
    "extrahashtags":    {"has_hashtag_body", "has_guillemets"},
    "source_brackets":  {"has_source_brackets", "has_alert_emoji"},
}


def _structural_signature(text: str) -> dict[str, bool]:
    """Возвращает {имя_признака: True} для всех найденных паттернов."""
    return {name: bool(pat.search(text)) for name, pat, _ in _STRUCTURAL_PATTERNS}


def _structural_score(sig_a: dict[str, bool], sig_b: dict[str, bool]) -> float:
    """Взвешенная схожесть двух сигнатур → [0, 1]."""
    total = sum(w for _, _, w in _STRUCTURAL_PATTERNS)
    match = sum(w for name, _, w in _STRUCTURAL_PATTERNS if sig_a.get(name) and sig_b.get(name))
    return match / total if total else 0.0


def _parse_tags(tag_str: str) -> set[str]:
    return {t.strip().lower() for t in re.split(r"[,;|\"']+", tag_str or "") if t.strip()}


def _tag_bonus(query_sig: dict[str, bool], example_tags: set[str]) -> float:
    """Бонус за совпадение тегов примера со структурой запроса. Максимум +0.30."""
    bonus = 0.0
    for tag in example_tags:
        related = _TAG_TO_SIGNATURES.get(tag, set())
        hits = sum(1 for s in related if query_sig.get(s))
        if related:
            bonus += 0.30 * (hits / len(related))
    return min(bonus, 0.30)


def _tokenize(text: str) -> set[str]:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.casefold()
    return set(re.findall(r"[\wА-Яа-яЁёA-Za-zÀ-ÿ-]{3,}", text, flags=re.UNICODE))


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
            tag_str = str(row.get("tag") or "")
            examples.append({
                "bad":    bad,
                "good":   good,
                "tag":    tag_str,
                "tags":   _parse_tags(tag_str),
                "notes":  str(row.get("notes") or "").strip(),
                "sig":    _structural_signature(bad),
                "tokens": _tokenize(bad),
            })
    return examples


def select_similar_examples(text: str, examples: list[dict], top_k: int = TOP_K) -> list[dict]:
    """
    Комбинированный скоринг: структура (60%) + теги (30%) + контент Jaccard (10%).

    Структурное сходство — главный сигнал: два поста с 🎙 и «» имеют
    одинаковую проблему верстки независимо от имён игроков.
    Jaccard — слабый тай-брейкер при одинаковом структурном счёте.
    """
    query_sig    = _structural_signature(text)
    query_tokens = _tokenize(text)
    seen_tags: set[str] = set()

    scored = []
    for ex in examples:
        struct  = _structural_score(query_sig, ex["sig"])
        tag_b   = _tag_bonus(query_sig, ex["tags"])
        content = _jaccard(query_tokens, ex["tokens"])
        score   = 0.60 * struct + 0.30 * tag_b + 0.10 * content
        if score > 0.05:
            scored.append((score, ex))

    scored.sort(key=lambda item: item[0], reverse=True)

    # Дедупликация по primary-тегу — не брать два одинаковых шаблона в промпт.
    selected: list[dict] = []
    for _, ex in scored:
        primary = next(iter(ex["tags"]), "")
        if primary and primary in seen_tags and len(selected) >= 1:
            continue
        if primary:
            seen_tags.add(primary)
        selected.append(ex)
        if len(selected) >= top_k:
            break

    return selected


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
        'Верни ТОЛЬКО JSON без пояснений и без markdown:\n'
        '{"ok":true,"issues":[],"fixed_text":"текст"}\n'
        'или\n'
        '{"ok":false,"issues":["описание проблемы"],"fixed_text":"исправленный текст"}\n'
        "Если всё уже правильно, fixed_text должен совпадать с исходным текстом.\n"
    )

    few_shots = []
    for idx, ex in enumerate(selected_examples, start=1):
        label = ex.get("tag") or f"example_{idx}"
        notes = ex.get("notes") or "-"
        few_shots.append(
            f"Пример {idx} (тип: {label}; заметки: {notes})\n"
            f"BAD:\n{_trim_example_text(ex['bad'])}\n\n"
            f"GOOD:\n{_trim_example_text(ex['good'])}\n"
        )

    examples_block = "\n\n".join(few_shots) if few_shots else "Нет похожих примеров."
    return (
        f"{rules}\n"
        f"Похожие примеры верстки:\n{examples_block}\n\n"
        f"ТЕКСТ ДЛЯ ПРОВЕРКИ:\n{candidate_text.strip()}\n"
    )


def parse_checker_json(content: str) -> dict | None:
    """
    Парсит JSON из ответа модели.
    Устойчив к тексту до/после JSON-блока и к вложенным {} в fixed_text.
    """
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)

    # Сначала пробуем весь текст целиком.
    try:
        raw_obj = json.loads(text)
        if isinstance(raw_obj, dict):
            return _extract_checker_fields(raw_obj)
    except Exception:
        pass

    # Берём срез от первой '{' до последней '}' — корректно обрабатывает
    # текст после блока и вложенные фигурные скобки внутри fixed_text.
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end > start:
        try:
            raw_obj = json.loads(text[start:end + 1])
            if isinstance(raw_obj, dict):
                return _extract_checker_fields(raw_obj)
        except Exception:
            pass

    return None


def _extract_checker_fields(raw_obj: dict) -> dict:
    return {
        "ok":         bool(raw_obj.get("ok", False)),
        "issues":     raw_obj.get("issues") if isinstance(raw_obj.get("issues"), list) else [],
        "fixed_text": str(raw_obj.get("fixed_text") or "").strip(),
    }


def call_gemini_layout_checker(prompt: str) -> dict | None:
    """
    Вызывает Gemini для RAG-проверки верстки.
    responseMimeType=application/json гарантирует JSON-ответ.
    Логирует HTTP-статус — важно для диагностики 429 (rate limit).
    """
    if not GEMINI_API_KEY or not LAYOUT_AI_MODEL:
        return None
    try:
        r = requests.post(
            f"{GEMINI_BASE_URL}/models/{LAYOUT_AI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 1400,
                    "candidateCount": 1,
                    "responseMimeType": "application/json",
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"].get("parts", [])
        content = "\n".join(p.get("text", "") for p in parts).strip()
        return parse_checker_json(content)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  RAG layout checker HTTP {status}: {e}")
        return None
    except Exception as e:
        print(f"  RAG layout checker ошибка: {e}")
        return None


# Обратная совместимость: старое имя функции принимало OpenRouter-параметры.
# Теперь все вызовы идут через Gemini; api_key и model игнорируются.
def call_openrouter_layout_checker(prompt: str, api_key: str, model: str) -> dict | None:
    return call_gemini_layout_checker(prompt)
