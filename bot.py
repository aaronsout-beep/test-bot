#!/usr/bin/env python3
"""
NicoPaz Telegram Bot
STEP 1: Twitter/Nitter по ключевым словам  -> DeepL + OpenRouter -> Telegram
STEP 2: Instagram через Apify               -> DeepL -> Telegram
STEP 3: Twitter-аккаунты                   -> DeepL + OpenRouter -> Telegram

Запуск: python bot.py --step 1|2|3|4
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from format_model.rag_layout import (
    build_layout_prompt,
    call_openrouter_layout_checker,
    load_examples,
    select_similar_examples,
)
from format_model.hard_cases import save_hard_case

# ────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
DEEPL_KEY = os.environ["DEEPL_KEY"]
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_MODEL = (
    os.environ.get("OPENROUTER_MODEL")
    or "meta-llama/llama-3.3-70b-instruct:free"
).strip()
GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or ""
).strip()
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta",
).rstrip("/")

# Cross-posting after a successful Telegram post.
VK_ACCESS_TOKEN = os.environ.get("VK_ACCESS_TOKEN", "").strip()
VK_OWNER_ID = os.environ.get("VK_OWNER_ID", "").strip()
VK_API_VERSION = os.environ.get("VK_API_VERSION", "5.199").strip()
VK_FROM_GROUP = os.environ.get("VK_FROM_GROUP", "1").strip()

MAX_TOKEN = os.environ.get("MAX_TOKEN", "").strip()
MAX_CHAT_ID = os.environ.get("MAX_CHAT_ID", "").strip()
MAX_USER_ID = os.environ.get("MAX_USER_ID", "").strip()


def normalize_instagram_hashtag(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lstrip("#"))


def normalize_twitter_account(value: str) -> str:
    account = str(value or "").strip()
    if not account:
        return ""

    account = re.sub(r"^from:", "", account, flags=re.I).strip()
    account = account.split("?")[0].strip().strip("/")
    account = re.sub(
        r"^(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com|nitter\.[^/]+)/",
        "",
        account,
        flags=re.I,
    )
    account = account.strip().lstrip("@").split("/")[0].split()[0].strip()
    match = re.search(r"[A-Za-z0-9_]{1,15}", account)
    return match.group(0) if match else ""

# Шаг 1 (Scweet)
STEP1_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("STEP1_KEYWORDS", "").split(",")
    if k.strip()
]
# Если задан — ищем только в постах этих аккаунтов; если пустой — глобальный поиск
STEP1_ACCOUNTS = [
    account
    for account in (
        normalize_twitter_account(a)
        for a in os.environ.get("STEP1_ACCOUNTS", "").split(",")
    )
    if account
]
SCWEET_AUTH_TOKEN = os.environ.get("SCWEET_AUTH_TOKEN", "").strip()
SCWEET_PROXY = os.environ.get("SCWEET_PROXY", "").strip()  # http://user:pass@host:port
SCWEET_MANIFEST_SCRAPE_ON_INIT = os.environ.get("SCWEET_MANIFEST_SCRAPE_ON_INIT", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
STEP1_LIMIT = int((os.environ.get("STEP1_LIMIT") or "50").strip())
STEP3_LIMIT = int((os.environ.get("STEP3_LIMIT") or "20").strip())

# Шаг 2
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_BASE_URL = (os.environ.get("APIFY_BASE_URL") or "https://api.apify.com/v2").rstrip("/")
APIFY_INSTAGRAM_ACTOR = (
    os.environ.get("APIFY_INSTAGRAM_ACTOR")
    or "apify~instagram-post-scraper"
).strip().replace("/", "~")
APIFY_INSTAGRAM_ACCOUNTS = [
    a.strip()
    for a in (
        os.environ.get("STEP2_INSTAGRAM_ACCOUNTS")
        or os.environ.get("APIFY_INSTAGRAM_ACCOUNTS", "")
    ).split(",")
    if a.strip()
]
APIFY_INSTAGRAM_RESULTS_LIMIT = int((os.environ.get("APIFY_INSTAGRAM_RESULTS_LIMIT") or "10").strip())
APIFY_INSTAGRAM_ONLY_NEWER_THAN = (os.environ.get("APIFY_INSTAGRAM_ONLY_NEWER_THAN") or "2 days").strip()
APIFY_INSTAGRAM_DATA_DETAIL_LEVEL = (os.environ.get("APIFY_INSTAGRAM_DATA_DETAIL_LEVEL") or "detailedData").strip()
APIFY_INSTAGRAM_SKIP_PINNED = (os.environ.get("APIFY_INSTAGRAM_SKIP_PINNED") or "1").strip().lower() not in {"0", "false", "no", "off"}
APIFY_INSTAGRAM_INPUT_JSON = os.environ.get("APIFY_INSTAGRAM_INPUT_JSON", "").strip()
APIFY_RUN_TIMEOUT = int((os.environ.get("APIFY_RUN_TIMEOUT") or "180").strip())
APIFY_MAX_ITEMS = os.environ.get("APIFY_MAX_ITEMS", "").strip()
APIFY_MAX_TOTAL_CHARGE_USD = os.environ.get("APIFY_MAX_TOTAL_CHARGE_USD", "").strip()
INSTAGRAM_PREFIX = " "

# Шаг 3
STEP3_ACCOUNTS = [
    account
    for account in (
        normalize_twitter_account(a)
        for a in os.environ.get("STEP3_ACCOUNTS", "").split(",")
    )
    if account
]

# Шаг 4
STEP4_HASHTAGS = [
    h.strip().lstrip("#")
    for h in os.environ.get("STEP4_HASHTAGS", "").split(",")
    if h.strip()
]
APIFY_HASHTAG_ACTOR = (
    os.environ.get("APIFY_HASHTAG_ACTOR")
    or "apify~instagram-hashtag-scraper"
).strip().replace("/", "~")
APIFY_HASHTAG_RESULTS_LIMIT = int((os.environ.get("APIFY_HASHTAG_RESULTS_LIMIT") or "10").strip())
APIFY_HASHTAG_ONLY_NEWER_THAN = (os.environ.get("APIFY_HASHTAG_ONLY_NEWER_THAN") or "2 days").strip()
STEP4_SIGNATURE = os.environ.get("STEP4_SIGNATURE", "#NicoPaz | ...").strip()
STEP4_PREFIX = os.environ.get("STEP4_PREFIX", "").strip()
# Если задан — публикуем только посты от этих аккаунтов.
# Если пустой — берём посты от всех аккаунтов в Instagram.
STEP4_ALLOWED_ACCOUNTS = {
    a.strip().lstrip("@").lower()
    for a in os.environ.get("STEP4_ALLOWED_ACCOUNTS", "").split(",")
    if a.strip()
}

NITTER_MIRRORS = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]
 
SIGNATURE = '\n\n#LamineYamal | <a href="https://max.ru/join/_4wOSEJG3rg3tNHknHybjckisAha49seSDf2mRbH3jY">Max</a>'
CROSSPOST_SIGNATURE = "\n\n#NicoPaz"
CAPTION_LIMIT = 1024
MSG_LIMIT = 4096
MAX_TEXT_LIMIT = 4000
TELEGRAM_MEDIA_GROUP_LIMIT = 10
MAX_AGE_DAYS = 2
PUBLISHED_FILE = "published_ids.json"
REQUIRE_PUBLISHED_CACHE = os.environ.get("REQUIRE_PUBLISHED_CACHE", "0").strip().lower() not in {
    "", "0", "false", "no", "off"
}
SOURCE_NEWS_FILE = "source_news_cache.json"
SOURCE_NEWS_CACHE_DAYS = 2
SOURCE_NEWS_CACHE_LIMIT = 500
SEMANTIC_CANDIDATE_LIMIT = 3
SEMANTIC_LOCAL_DUPLICATE_THRESHOLD = 0.86
SEMANTIC_AI_PREFILTER_THRESHOLD = 0.24
LAYOUT_AI_MODEL = os.environ.get(
    "LAYOUT_AI_MODEL",
    "gemini-2.5-flash-lite",
).strip()
LAYOUT_AI_MAX_CHARS = int(os.environ.get("LAYOUT_AI_MAX_CHARS", "3200"))
LAYOUT_AI_TIMEOUT = int(os.environ.get("LAYOUT_AI_TIMEOUT", "12"))
FORMAT_RAG_ENABLED = os.environ.get("FORMAT_RAG_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
FORMAT_RAG_MODEL = (
    os.environ.get("FORMAT_RAG_MODEL")
    or OPENROUTER_MODEL
).strip()
MEDIA_DIR = Path("media_tmp")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NicoPazBot/1.0)"}


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def twitter_status_ids_from_text(text: str) -> set:
    if not text:
        return set()
    text = html.unescape(str(text)).strip()
    ids = set()

    if re.fullmatch(r"\d{8,25}", text):
        ids.add(text)

    for match in re.finditer(r"\b(?:id|tweet):(\d{8,25})\b", text, flags=re.I):
        ids.add(match.group(1))
    for match in re.finditer(r"(?:twitter\.com|x\.com|nitter\.[^/\s]+|/status(?:es)?)/[^\s\"'<>]*?(\d{8,25})", text, flags=re.I):
        ids.add(match.group(1))
    for match in re.finditer(r"/status(?:es)?/(\d{8,25})", text, flags=re.I):
        ids.add(match.group(1))

    return ids


def expand_published_keys(ids: set) -> set:
    expanded = set(ids)
    for key in list(ids):
        for status_id in twitter_status_ids_from_text(str(key or "")):
            expanded.add(f"id:{status_id}")
            expanded.add(f"tweet:{status_id}")
    return expanded


def load_published() -> set:
    if Path(PUBLISHED_FILE).exists():
        try:
            with open(PUBLISHED_FILE, encoding="utf-8") as f:
                return expand_published_keys(set(json.load(f)))
        except Exception as e:
            print(f"  Не удалось прочитать {PUBLISHED_FILE}: {e}")
    return set()


def save_published(ids: set):
    # Важно: не обрезаем set через list(ids)[-5000:].
    # У set нет стабильного порядка, из-за этого свежие ключи Instagram могли
    # случайно выпадать из кэша, и шаг 2 снова публиковал тот же пост.
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def ensure_state_files(published: set):
    if not Path(PUBLISHED_FILE).exists():
        save_published(published)
    if not Path(SOURCE_NEWS_FILE).exists():
        save_source_news_cache([])


def canonical_url(url: str) -> str:
    if not url:
        return ""
    url = html.unescape(str(url)).strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower()

    host = parsed.netloc.lower()
    path = unquote(parsed.path or "").rstrip("/")

    if host.startswith("www."):
        host = host[4:]

    if "nitter." in host:
        host = "twitter.com"
    elif host == "x.com":
        host = "twitter.com"

    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in {"fbclid", "igshid", "s", "t"}:
            continue
        query_pairs.append((key_lower, value))

    query = urlencode(sorted(query_pairs))
    return urlunparse(("", host, path, "", query, "")).lower()


def canonical_media_url(url: str) -> str:
    """Stable media URL for duplicate detection.

    Instagram/Facebook CDN media URLs often contain expiring query params.
    Keeping those params makes the same post look new on the next run.
    """
    if not url:
        return ""
    url = html.unescape(str(url)).strip()
    if not url:
        return ""

    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower()

    host = parsed.netloc.lower()
    path = unquote(parsed.path or "").rstrip("/")

    if host.startswith("www."):
        host = host[4:]

    volatile_hosts = (
        "cdninstagram",
        "fbcdn",
        "scontent",
    )
    if any(marker in host for marker in volatile_hosts):
        return urlunparse(("", host, path, "", "", "")).lower()

    return canonical_url(url)


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:20]


def text_fingerprint(text: str) -> str:
    text = strip_html_tags(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if len(text) < 20:
        return ""
    return short_hash(text[:500])


def media_fingerprint(media_items: list) -> str:
    urls = [
        canonical_media_url(item.get("url", ""))
        for item in media_items or []
        if item.get("url")
    ]
    urls = sorted(u for u in urls if u)
    if not urls:
        return ""
    return short_hash("|".join(urls[:10]))


def collect_string_values(value, out: list):
    if isinstance(value, dict):
        for nested in value.values():
            collect_string_values(nested, out)
    elif isinstance(value, list):
        for nested in value:
            collect_string_values(nested, out)
    elif isinstance(value, str):
        out.append(value)


def instagram_shortcode_from_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(str(text))
    patterns = [
        r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
        r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def instagram_shortcodes_from_text(text: str) -> set:
    if not text:
        return set()
    text = html.unescape(str(text))
    shortcodes = set()
    patterns = [
        r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
        r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            shortcode = match.group(1).strip().lower()
            if shortcode:
                shortcodes.add(shortcode)
    return shortcodes


def instagram_shortcodes_from_post(post: dict) -> set:
    candidates = []
    collect_string_values(post, candidates)

    shortcodes = set()
    for candidate in candidates:
        shortcodes.update(instagram_shortcodes_from_text(candidate))
    return shortcodes


def instagram_shortcode_from_post(post: dict) -> str:
    shortcodes = sorted(instagram_shortcodes_from_post(post))
    return shortcodes[0] if shortcodes else ""


def instagram_extra_duplicate_keys(post: dict) -> set:
    """Stable extra keys for Instagram duplicates."""
    keys = set()
    candidates = []
    collect_string_values(post, candidates)

    for field in ("shortCode", "shortcode", "code"):
        shortcode = str(post.get(field) or "").strip().lower()
        if shortcode:
            keys.add(f"ig:{shortcode}")

    for shortcode in instagram_shortcodes_from_post(post):
        keys.add(f"ig:{shortcode}")

    for candidate in candidates:
        text = html.unescape(candidate)
        for match in re.finditer(r"https?://[^\s\"'<>]+", text):
            url = match.group(0).rstrip(").,;]")
            normalized = canonical_url(url)
            if "instagram.com/" in normalized:
                keys.add(f"igurl:{normalized}")

    return keys


def duplicate_keys(
    post_id: str = "",
    url: str = "",
    text: str = "",
    media_items: list | None = None,
    extra_keys: list | set | tuple | None = None,
) -> set:
    keys = set()
    for key in extra_keys or []:
        key = str(key).strip().lower()
        if key:
            keys.add(key)

    if post_id:
        keys.add(f"id:{canonical_url(post_id) or str(post_id).strip().lower()}")
    if url:
        keys.add(f"url:{canonical_url(url)}")

    for source_value in (post_id, url):
        for status_id in twitter_status_ids_from_text(source_value):
            keys.add(f"id:{status_id}")
            keys.add(f"tweet:{status_id}")

    text_key = text_fingerprint(text)
    if text_key:
        keys.add(f"text:{text_key}")

    media_key = media_fingerprint(media_items or [])
    if media_key:
        keys.add(f"media:{media_key}")

    return {key for key in keys if key and not key.endswith(":")}


def find_duplicate(published: set, **kwargs) -> str:
    post_id = str(kwargs.get("post_id") or "").strip()
    url = str(kwargs.get("url") or "").strip()
    if post_id and post_id in published:
        return post_id
    if url and url in published:
        return url
    for key in duplicate_keys(**kwargs):
        if key in published:
            return key
    return ""


def mark_published(published: set, **kwargs):
    published.update(duplicate_keys(**kwargs))


NEWS_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "have", "has", "was",
    "were", "will", "are", "but", "not", "you", "his", "her", "their", "about",
    "como", "para", "con", "que", "por", "del", "las", "los", "una", "uno",
    "это", "как", "что", "или", "для", "при", "про", "его", "её", "они", "она",
    "оно", "уже", "ещё", "после", "перед", "без", "над", "под", "все", "был",
    "была", "были", "будет", "будут", "есть",
}


def parse_cache_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def prune_source_news_cache(records: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SOURCE_NEWS_CACHE_DAYS)
    fresh = []
    for record in records:
        created_at = parse_cache_datetime(str(record.get("created_at", "")))
        if created_at and created_at >= cutoff:
            fresh.append(record)
    return fresh[-SOURCE_NEWS_CACHE_LIMIT:]


def load_source_news_cache() -> list:
    path = Path(SOURCE_NEWS_FILE)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return prune_source_news_cache(data)
    except Exception as e:
        print(f"  Не удалось прочитать {SOURCE_NEWS_FILE}: {e}")
    return []


def save_source_news_cache(records: list):
    with open(SOURCE_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(prune_source_news_cache(records), f, ensure_ascii=False, indent=2)


def normalize_news_for_similarity(text: str) -> str:
    text = strip_html_tags(text or "")
    text = html.unescape(text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[@#][\wА-Яа-яÀ-ÿ_]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"[^\w\sА-Яа-яÀ-ÿ-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def news_tokens(text: str) -> set:
    normalized = normalize_news_for_similarity(text)
    tokens = set()
    for token in re.findall(r"[\wА-Яа-яÀ-ÿ-]{3,}", normalized, flags=re.UNICODE):
        if token not in NEWS_STOPWORDS:
            tokens.add(token)
    return tokens


def token_similarity(left: str, right: str) -> float:
    left_tokens = news_tokens(left)
    right_tokens = news_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0

    common = len(left_tokens & right_tokens)
    overlap = common / min(len(left_tokens), len(right_tokens))
    cosine = common / ((len(left_tokens) * len(right_tokens)) ** 0.5)
    return max(overlap, cosine)


def source_news_key(text: str, post_id: str = "", url: str = "") -> str:
    stable = canonical_url(url) or canonical_url(post_id) or text_fingerprint(text)
    if not stable:
        stable = normalize_news_for_similarity(text)[:500]
    return short_hash(stable)


def top_semantic_candidates(text: str, cache: list, url: str = "") -> list:
    current_url = canonical_url(url)
    candidates = []
    for record in cache:
        record_url = canonical_url(record.get("url", ""))
        if current_url and record_url and current_url == record_url:
            continue

        old_text = record.get("text", "")
        score = token_similarity(text, old_text)
        if score >= SEMANTIC_AI_PREFILTER_THRESHOLD:
            candidates.append((score, record))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[:SEMANTIC_CANDIDATE_LIMIT]


def openrouter_semantic_duplicate(text: str, candidates: list) -> bool:
    if not OPENROUTER_KEY or not candidates:
        return False

    previous = [
        {
            "score": round(score, 3),
            "source": record.get("source", ""),
            "date": record.get("created_at", ""),
            "text": record.get("text", "")[:1200],
        }
        for score, record in candidates
    ]
    prompt = (
        "You are checking football news duplicates before publication.\n"
        "A NEW item is allowed only if at least 80% of its information is new "
        "compared with the PREVIOUS items.\n"
        "Mark duplicate=true only when it is the same event/news with mostly the same facts, "
        "even if it is worded differently or comes from another account.\n"
        "Return only valid JSON: {\"duplicate\": true|false, \"reason\": \"short\"}.\n\n"
        f"NEW:\n{text[:1600]}\n\n"
        f"PREVIOUS:\n{json.dumps(previous, ensure_ascii=False)}"
    )

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=18,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{.*\}", content, flags=re.S)
        data = json.loads(match.group(0) if match else content)
        if data.get("duplicate") is True:
            print(f"  AI-дубль по смыслу: {data.get('reason', '')}")
            return True
        return False
    except Exception as e:
        print(f"  AI-проверка дублей недоступна: {e}")
        return False


def is_semantic_duplicate(text: str, source: str = "", post_id: str = "", url: str = "") -> bool:
    if word_count(text) < 6:
        return False

    cache = load_source_news_cache()
    candidates = top_semantic_candidates(text, cache, url=url)
    if not candidates:
        return False

    best_score, best_record = candidates[0]
    if best_score >= SEMANTIC_LOCAL_DUPLICATE_THRESHOLD:
        print(
            "  Смысловой дубль по локальному кэшу "
            f"({best_score:.2f}, {best_record.get('source', '')}) — пропуск"
        )
        return True

    if openrouter_semantic_duplicate(text, candidates):
        return True

    return False


def record_source_news(text: str, step: int, source: str = "", post_id: str = "", url: str = ""):
    if word_count(text) < 6:
        return

    cache = load_source_news_cache()
    key = source_news_key(text, post_id=post_id, url=url)
    if any(record.get("key") == key for record in cache):
        return

    cache.append(
        {
            "key": key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "source": source,
            "post_id": str(post_id or ""),
            "url": str(url or ""),
            "text": normalize_news_for_similarity(text)[:1800],
        }
    )
    save_source_news_cache(cache)



def is_too_old(date_str: str) -> bool:
    if not date_str:
        return False
    normalized = date_str.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
    except (TypeError, ValueError, IndexError):
        pass

    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
        except ValueError:
            continue
    print(f"  Не удалось разобрать дату: {date_str}")
    return False


def strip_decorative_pipes(text: str) -> str:
    text = re.sub(r"([🚨❗🔥📢❓🎙🗣])\s*\|\s*", r"\1 ", text or "")
    text = re.sub(r"(?<=\S)\s*\|\s*(?=\s|$)", " ", text)
    text = re.sub(r"\s+\|\s*$", "", text, flags=re.MULTILINE)
    return text


def strip_follow_us_tail(text: str) -> str:
    """Remove trailing 'Follow us' artifacts from scraped sources."""
    text = re.sub(r"\s*\|\s*Follow us\s*(?=\n|$)", "", text or "", flags=re.I)
    text = re.sub(r"\s*Follow us\s*(?=\n|$)", "", text or "", flags=re.I)
    text = re.sub(r"\s*\|\s*Подпишись\s*(?=\n|$)", "", text or "", flags=re.I)
    text = re.sub(r"\s*Подпишись\s*(?=\n|$)", "", text or "", flags=re.I)
    return text


def normalize_source_brackets(text: str) -> str:
    def fix(match: re.Match) -> str:
        inner = match.group(1).strip()
        inner = re.sub(r"^[-–—]\s*", "", inner)
        return f"[{inner}]" if inner else "[]"

    return re.sub(r"\[\s*([^\]]+?)\s*\]", fix, text or "")


def clean_text(text: str) -> str:
    text = re.sub(r"https?://nitter\.[^\s]+", "", text)
    text = re.sub(r"https?://t\.co/[^\s]+", "", text)
    text = re.sub(r"https?://twitter\.[^\s]+", "", text)
    text = re.sub(r"https?://instagram\.[^\s]+", "", text)
    text = re.sub(r"\n[^\n]+\(@[^)]+\)\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("@", "-")
    text = strip_decorative_pipes(text)
    paras = text.split("\n\n")
    seen, unique = set(), []
    for p in paras:
        cleaned = preprocess_post_text(p.strip())
        if cleaned.lower() in {"video", "photo", "image", "pic", "видео", "фото", "картинка"}:
            continue
        key = cleaned.lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(cleaned)
    return "\n\n".join(unique).strip()


def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


QUOTE_PAIRS = {
    '"': '"',
    "«": "»",
    "“": "”",
    "„": "“",
    "‚": "‘",
    "‘": "’",
    "‹": "›",
    "「": "」",
    "『": "』",
}


def normalize_angle_quotes(text: str) -> str:
    text = re.sub(r'"([^"\n]+)"', r"«\1»", text or "")
    text = re.sub(r"“([^”\n]+)”", r"«\1»", text)
    text = re.sub(r"„([^“\n]+)“", r"«\1»", text)
    return text


FOOTBALL_CLUB_NAMES = {
    "Manchester United", "Манчестер Юнайтед",
    "Manchester City", "Манчестер Сити",
    "Liverpool FC", "Ливерпуль",
    "Arsenal FC", "Арсенал",
    "Chelsea FC", "Челси",
    "Tottenham Hotspur", "Тоттенхэм Хотспур",
    "Newcastle United FC", "Ньюкасл Юнайтед",
    "Aston Villa FC", "Астон Вилла",
    "West Ham United FC", "Вест Хэм Юнайтед",
    "Real Madrid CF", "Реал Мадрид",
    "FC Barcelona", "Барселона",
    "Atlético Madrid", "Атлетико Мадрид",
    "Sevilla FC", "Севилья",
    "Valencia CF", "Валенсия",
    "Real Betis", "Реал Бетис",
    "Real Sociedad", "Реал Сосьедад",
    "Villarreal CF", "Вильярреал",
    "Athletic Bilbao", "Атлетик Бильбао",
    "FC Bayern Munich", "Бавария",
    "Borussia Dortmund", "Боруссия Дортмунд",
    "RB Leipzig", "РБ Лейпциг",
    "Bayer 04 Leverkusen", "Байер Леверкузен",
    "Eintracht Frankfurt", "Айнтрахт Франкфурт",
    "VfB Stuttgart", "Штутгарт",
    "Borussia Mönchengladbach", "Боруссия Мёнхенгладбах",
    "Juventus FC", "Ювентус",
    "AC Milan", "Милан",
    "Inter Milan", "Интер",
    "SSC Napoli", "Наполи",
    "AS Roma", "Рома",
    "SS Lazio", "Лацио",
    "Atalanta BC", "Аталанта",
    "Fiorentina", "Фиорентина",
    "Paris Saint-Germain FC", "Пари Сен-Жермен",
    "Olympique de Marseille", "Марсель",
    "Olympique Lyonnais", "Лион",
    "AS Monaco FC", "Монако",
    "LOSC Lille", "Лилль",
    "SL Benfica", "Бенфика",
    "FC Porto", "Порту",
    "Sporting CP", "Спортинг",
    "AFC Ajax", "Аякс",
    "PSV Eindhoven", "ПСВ",
    "Feyenoord", "Фейеноорд",
    "Galatasaray SK", "Галатасарай",
    "Fenerbahçe SK", "Фенербахче",
    "Beşiktaş JK", "Бешикташ",
    "Al Nassr FC", "Аль-Наср",
    "Al Hilal SFC", "Аль-Хиляль",
    "Al Ittihad Club", "Аль-Иттихад",
    "Inter Miami CF", "Интер Майами",
    "LA Galaxy", "Лос-Анджелес Гэлакси",
    "Los Angeles FC", "Лос-Анджелес",
    "Boca Juniors", "Бока Хуниорс",
    "River Plate", "Ривер Плейт",
    "Flamengo", "Фламенго",
    "Santos FC", "Сантос",
    "Palmeiras", "Палмейрас",
    "São Paulo FC", "Сан-Паулу",
    "Celtic FC", "Селтик",
    "Rangers FC", "Рейнджерс",
}

FOOTBALL_CLUB_ALIASES = {
    "МЮ", "Юнайтед", "Манчестер Юнайтед", "Манчестер Сити", "Сити",
    "Ливерпуль", "Арсенал", "Челси", "Тоттенхэм", "Ньюкасл",
    "Астон Вилла", "Вест Хэм",
    "Реал", "Реал Мадрид", "Мадрид", "Барселона", "Барса", "Атлетико",
    "Севилья", "Валенсия", "Бетис", "Реал Бетис", "Реал Сосьедад",
    "Сосьедад", "Вильярреал", "Атлетик", "Атлетик Бильбао",
    "Атлетик Клуб", "Athletic Club", "Athletic Club Bilbao", "Athletic Bilbao",
    "Бавария", "Боруссия", "Боруссия Дортмунд", "Дортмунд",
    "Лейпциг", "РБ Лейпциг", "Байер", "Байер Леверкузен",
    "Леверкузен", "Айнтрахт", "Штутгарт",
    "Ювентус", "Милан", "Интер", "Наполи", "Рома", "Лацио",
    "Аталанта", "Фиорентина",
    "ПСЖ", "Пари Сен-Жермен", "Марсель", "Лион", "Монако", "Лилль",
    "Бенфика", "Порту", "Спортинг", "Аякс", "ПСВ", "Фейеноорд",
    "Галатасарай", "Фенербахче", "Бешикташ",
    "Аль-Наср", "Аль-Хиляль", "Аль-Иттихад",
    "Интер Майами", "Лос-Анджелес Гэлакси", "Лос-Анджелес",
    "Бока Хуниорс", "Ривер Плейт", "Фламенго", "Сантос",
    "Палмейрас", "Сан-Паулу", "Селтик", "Рейнджерс",
}


def normalize_club_name(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[-–—]", " ", text)
    text = re.sub(r"[^\w\sА-Яа-яA-Za-zÀ-ÿ]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def has_cyrillic(text: str) -> bool:
    return re.search(r"[А-Яа-яЁё]", text or "") is not None


def decline_russian_word(word: str) -> set:
    word = word.strip()
    if not word or not has_cyrillic(word):
        return {word}

    lower = word.lower()
    forms = {word}
    vowels = "аеёиоуыэюя"
    consonants = "бвгджзклмнпрстфхцчшщ"

    if lower.endswith("а"):
        stem = word[:-1]
        gen = "и" if lower[-2:-1] in {"г", "к", "х", "ж", "ч", "ш", "щ"} else "ы"
        forms.update({stem + gen, stem + "е", stem + "у", stem + "ой", stem + "ою"})
    elif lower.endswith("я"):
        stem = word[:-1]
        forms.update({stem + "и", stem + "е", stem + "ю", stem + "ей", stem + "ею"})
    elif lower.endswith("ь"):
        stem = word[:-1]
        forms.update({stem + "я", stem + "ю", stem + "ем", stem + "е"})
    elif lower.endswith("й"):
        stem = word[:-1]
        forms.update({stem + "я", stem + "ю", stem + "ем", stem + "е"})
    elif lower[-1] in consonants:
        forms.update({word + "а", word + "у", word + "ом", word + "е"})
    elif lower[-1] in vowels:
        forms.add(word)

    return forms


def club_case_forms(name: str) -> set:
    normalized = normalize_club_name(name)
    if not normalized:
        return set()

    forms = {normalized}
    if not has_cyrillic(name):
        return forms

    words = name.split()
    if len(words) == 1:
        forms.update(normalize_club_name(form) for form in decline_russian_word(words[0]))
        return forms

    # В русских текстах составные названия склоняются по-разному:
    # "Арсенал" -> "Арсенала", но "Манчестер Юнайтед" часто -> "Манчестера Юнайтед".
    for index in {0, len(words) - 1}:
        for form in decline_russian_word(words[index]):
            variant = words[:]
            variant[index] = form
            forms.add(normalize_club_name(" ".join(variant)))

    forms.update(normalize_club_name(word) for word in words if len(word) > 3)
    return {form for form in forms if form}


def build_football_club_keys() -> set:
    keys = set()
    for name in FOOTBALL_CLUB_NAMES | FOOTBALL_CLUB_ALIASES:
        keys.update(club_case_forms(name))
    return keys


FOOTBALL_CLUB_KEYS = build_football_club_keys()


def word_count(text: str) -> int:
    return len(re.findall(r"[\wА-Яа-яA-Za-zÀ-ÿ-]+", text, flags=re.UNICODE))


def speaker_name_from_heading(heading: str) -> str:
    text = html.unescape(strip_html_tags(heading or "")).strip()
    text = re.sub(r"^\s*[🗣🎙]️?\s*:?\s*", "", text)
    text = re.sub(r"^\s*(?:спикер|speaker)\s*:?\s*", "", text, flags=re.I)
    return text.strip(" *:")


def looks_like_speaker_heading(heading: str) -> bool:
    plain = html.unescape(strip_html_tags(heading or "")).strip()
    name = speaker_name_from_heading(plain)
    if not name:
        return False
    if "🗣" in plain or "🎙" in plain:
        return True
    if is_football_club_name(name):
        return False
    if re.search(r"\d|https?://", name, flags=re.I):
        return False

    words = re.findall(r"[A-Za-zА-Яа-яЁёÀ-ÿ][\wА-Яа-яЁёÀ-ÿ'-]*", name, flags=re.UNICODE)
    if not 2 <= len(words) <= 5:
        return False
    return sum(1 for word in words[:2] if word[:1].isupper()) >= 2


def render_colon_heading(heading: str) -> str:
    plain = html.unescape(strip_html_tags(heading or "")).strip()
    if "🗣" in plain or "🎙" in plain or looks_like_speaker_heading(heading):
        speaker_name = speaker_name_from_heading(heading)
        if speaker_name:
            return f"🎙️ <b>{html.escape(speaker_name, quote=False)}</b>:"
    return f"<b>{html.escape(heading, quote=False)}</b>:"


def should_blockquote_quote(text: str, speaker_context: bool = False) -> bool:
    if normalize_club_name(text) in FOOTBALL_CLUB_KEYS:
        return False
    words = word_count(text)
    if words < 1:
        return False
    if words < 3 and not speaker_context:
        return False
    return True


def is_football_club_name(text: str) -> bool:
    return normalize_club_name(text) in FOOTBALL_CLUB_KEYS


def split_quoted_segments(line: str) -> list:
    segments = []
    buf = []
    i = 0

    while i < len(line):
        opener = line[i]
        closer = QUOTE_PAIRS.get(opener)
        if closer:
            end = line.find(closer, i + 1)
            if end != -1:
                if buf:
                    segments.append(("text", "".join(buf)))
                    buf = []
                quote = line[i + 1:end].strip()
                if quote:
                    segments.append(("quote", quote))
                i = end + 1
                continue

        buf.append(opener)
        i += 1

    if buf:
        segments.append(("text", "".join(buf)))
    return segments


def split_colon_heading(line: str) -> tuple[str, str]:
    if ":" not in line:
        return "", ""

    before, after = line.split(":", 1)
    before = before.strip()
    after = after.strip()

    if not before:
        return "", ""
    return before, after


def join_orphan_lines(text: str) -> str:
    """Склеивает переносы внутри предложения (Twitter/Nitter), не трогая абзацы."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    orphan_endings = (
        "в", "на", "за", "с", "со", "к", "ко", "у", "о", "об", "по", "против",
        "для", "от", "до", "при", "без", "над", "под", "и", "а", "но", "или",
    )
    lines = text.split("\n")
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            if merged and merged[-1] != "":
                merged.append("")
            i += 1
            continue

        while i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if not nxt:
                break
            if nxt.startswith("#"):
                break
            # Avoid merging bullet/feature lists into neighbor sentences.
            if nxt.startswith(("✅", "☑", "✔")):
                break
            if ":" in line and looks_like_speaker_heading(split_colon_heading(line)[0] or line):
                break
            if nxt.endswith(":") and looks_like_speaker_heading(split_colon_heading(nxt)[0] or nxt):
                break
            if nxt.startswith("❓") or line.startswith("❓"):
                break

            last_word = re.findall(
                r"[\wА-Яа-яЁёÀ-ÿ'-]+",
                line,
                flags=re.UNICODE,
            )
            ends_orphan = (
                (last_word and last_word[-1].casefold() in orphan_endings)
                or not re.search(r"[.!?…:»»\"]\s*$", line)
            )
            continues = (
                nxt[:1].islower()
                or (word_count(nxt) <= 4 and not re.search(r"[.!?…]$", nxt))
            )
            if ends_orphan and continues:
                line = f"{line} {nxt}"
                i += 1
                continue
            break

        merged.append(line)
        i += 1

    return "\n".join(merged)


def merge_country_player_list_lines(text: str) -> str:
    """Страна — на одной строке с игроком, без «» вокруг имени."""
    lines = (text or "").split("\n")
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            if merged and merged[-1] != "":
                merged.append("")
            i += 1
            continue

        if i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and re.search(r"(?:—|–)\s*/?\s*$", line):
                player = extract_quote_text(nxt)
                if player and word_count(player) <= 10:
                    country = re.sub(r"(?:—|–)\s*/?\s*$", "—", line).strip()
                    merged.append(f"{country} {player}")
                    i += 2
                    continue

        merged.append(line)
        i += 1

    return "\n".join(merged)


def normalize_paragraph_breaks(text: str) -> str:
    """Пустая строка между законченными предложениями."""
    lines = (text or "").split("\n")
    result = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            if result and result[-1] != "":
                result.append("")
            continue

        result.append(line)
        if i + 1 >= len(lines):
            continue

        nxt = lines[i + 1].strip()
        if not nxt or nxt.startswith("#"):
            continue
        if re.search(r"(?:—|–)\s*/?\s*$", line) or re.search(r"(?:—|–)\s*/?\s*$", nxt):
            continue
        if looks_like_speaker_heading(split_colon_heading(line)[0] or line):
            continue
        if nxt.endswith(":") and looks_like_speaker_heading(split_colon_heading(nxt)[0] or nxt):
            continue

        if re.search(r'[.!?…]["»»\s]*$', line) and (
            nxt[:1].isupper() or nxt.startswith(("«", "❓", "🎙", "🗣", "🚨", "❗"))
        ):
            if result[-1] != "":
                result.append("")

    return "\n".join(result)


def ensure_hashtag_spacing(text: str) -> str:
    lines = (text or "").split("\n")
    out = []
    for line in lines:
        if (
            line.strip().startswith("#")
            and out
            and out[-1].strip()
            and out[-1] != ""
        ):
            out.append("")
        out.append(line)
    return "\n".join(out).strip()


def preprocess_post_text(text: str) -> str:
    """Единая нормализация plain-текста до HTML и AI-верстки."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = strip_decorative_pipes(text)
    text = strip_follow_us_tail(text)
    text = normalize_source_brackets(text)
    text = join_orphan_lines(text)
    text = merge_country_player_list_lines(text)
    text = normalize_paragraph_breaks(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def looks_like_country_player_item(line: str) -> bool:
    plain = strip_html_tags(line or "").strip()
    if not re.search(r"(?:—|–)\s*\S", plain):
        return False
    if re.search(r"[\U0001F1E6-\U0001F1FF]", plain):
        return True
    left = re.split(r"(?:—|–)", plain, maxsplit=1)[0]
    return word_count(left) <= 6


def extract_quote_text(text: str) -> str:
    text = (text or "").strip()
    match = re.search(r"«([^»]+)»", text)
    if match:
        return match.group(1).strip()
    match = re.search(r'"([^"\n]+)"', text)
    if match:
        return match.group(1).strip()
    return text.strip("«»\"' ")


def clean_interview_question_line(line: str) -> str:
    line = strip_decorative_pipes(strip_html_tags(line or "")).strip()
    line = re.sub(r"[🚨❗🔥📢🎙🗣]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" |")
    if not line.startswith("❓"):
        line = f"❓ {line}"
    return line


def try_format_interview_qa(text: str) -> str | None:
    """
    Вопрос ❓ + пустая строка + 🎙️ спикер + blockquote-цитата (без пустой строки между спикером и цитатой).
    """
    text = strip_html_tags(text or "").replace("@", "-").strip()
    if not text:
        return None

    text = strip_decorative_pipes(join_orphan_lines(text))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None

    hashtags = []
    while lines and re.fullmatch(r"#[\wА-Яа-яA-Za-zÀ-ÿ_]+", lines[-1], flags=re.UNICODE):
        hashtags.insert(0, lines.pop())

    if len(lines) < 2:
        return None

    question = clean_interview_question_line(lines[0])
    if "?" not in question:
        return None

    speaker_line = lines[1]
    speaker_heading, speaker_tail = split_colon_heading(speaker_line)
    if not speaker_heading or not looks_like_speaker_heading(speaker_heading):
        return None

    quote_parts = []
    if speaker_tail:
        quote_parts.append(speaker_tail)
    quote_parts.extend(lines[2:])
    joined_answer = " ".join(quote_parts).strip()
    quote = extract_quote_text(joined_answer)
    # Fallback: some interviews are formatted without explicit «...» quotes.
    if not quote:
        quote = joined_answer
    if not quote or word_count(quote) < 3:
        return None

    q_plain = question.lstrip("❓").strip()
    blocks = [
        f"❓ <b>{html.escape(q_plain, quote=False)}</b>",
        "",
        render_colon_heading(speaker_heading),
        f"<blockquote>«{html.escape(quote, quote=False)}»</blockquote>",
    ]
    if hashtags:
        blocks.extend(["", "\n".join(hashtags)])
    return "\n".join(blocks).strip()


def render_telegram_line(
    line: str,
    bold_line: bool,
    apply_colon_rule: bool = True,
    speaker_context: bool = False,
) -> list:
    if apply_colon_rule:
        heading, body = split_colon_heading(line)
        if heading:
            heading_is_speaker = looks_like_speaker_heading(heading)
            rendered = [render_colon_heading(heading)]
            if body:
                rendered.extend(
                    render_telegram_line(
                        body,
                        False,
                        apply_colon_rule=False,
                        speaker_context=speaker_context or heading_is_speaker,
                    )
                )
            return rendered

    rendered = []
    for kind, value in split_quoted_segments(line):
        value = value.strip()
        if not value:
            continue
        if kind == "text":
            value = re.sub(r"^[.·]\s+", "", value, flags=re.UNICODE).strip()
            if not value:
                continue
        escaped = html.escape(value, quote=False)
        if kind == "quote":
            if looks_like_country_player_item(line):
                rendered.append(escaped)
            elif should_blockquote_quote(value, speaker_context=speaker_context):
                rendered.append(f"<blockquote>«{escaped}»</blockquote>")
            elif is_football_club_name(value):
                rendered.append(escaped)
            else:
                rendered.append(f"«{escaped}»")
        elif bold_line:
            rendered.append(f"<b>{escaped}</b>")
        else:
            rendered.append(escaped)
    return rendered


def make_telegram_html(text: str, bold_first_line: bool = True) -> str:
    """
    Telegram parse_mode=HTML очень строгий. Поэтому экранируем весь внешний текст
    и сами добавляем только контролируемый тег <b> для первой строки.
    """
    text = strip_html_tags(text).replace("@", "-").strip()
    if not text:
        return ""

    text = preprocess_post_text(text)
    interview = try_format_interview_qa(text)
    if interview:
        return ensure_hashtag_spacing(interview)

    output = []
    first_content_line = True
    speaker_quote_context = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue

        bold_line = bool(bold_first_line and first_content_line)
        output.extend(render_telegram_line(line, bold_line, speaker_context=speaker_quote_context))
        first_content_line = False
        speaker_quote_context = looks_like_speaker_heading(split_colon_heading(line)[0] or line)

    return ensure_hashtag_spacing("\n".join(output).strip())


def make_instagram_telegram_html(caption: str) -> str:
    prefix = html.escape(INSTAGRAM_PREFIX.replace("@", "-"), quote=False)
    caption = strip_html_tags(caption or "").replace("@", "-").strip()

    if not caption:
        return f"<b>{prefix}</b>"

    rendered_caption = make_telegram_html(caption, bold_first_line=False)
    return f"<b>{prefix}</b>\n{rendered_caption}"


def fetch_rss_items(url: str) -> list:
    """Загружает RSS/JSON Feed и возвращает список постов."""
    try:
        r = requests.get(url, timeout=15, headers=REQUEST_HEADERS)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "")

        if "json" in content_type or url.endswith(".json"):
            return r.json().get("items", [])

        import xml.etree.ElementTree as ET

        root = ET.fromstring(r.text)
        items = []
        for el in root.iter("item"):
            def t(tag):
                return (el.findtext(tag) or "").strip()

            content_html = (
                t("{http://purl.org/rss/1.0/modules/content/}encoded")
                or t("description")
            )
            snippet = re.sub(r"<[^>]+>", " ", content_html)
            snippet = re.sub(r"\s+", " ", snippet).strip()
            items.append(
                {
                    "id": t("guid") or t("link"),
                    "url": t("link"),
                    "title": t("title"),
                    "content_text": snippet,
                    "content_html": content_html,
                    "date_published": t("pubDate")
                    or t("{http://purl.org/dc/elements/1.1/}date"),
                    "image": "",
                }
            )
        return items
    except Exception as e:
        print(f"  RSS ошибка ({url[:80]}): {e}")
        return []


# ════════════════════════════════════════════════════════════
# ИЗВЛЕЧЕНИЕ МЕДИА
# ════════════════════════════════════════════════════════════

def normalize_nitter_media_url(raw_url: str, page_url: str) -> str:
    url = html.unescape(raw_url or "").strip()
    url = unquote(url)
    url = urljoin(page_url, url)

    # Sometimes Nitter wraps a full URL as /pic/https://...
    url = re.sub(r"https?://[^/]+/pic/(https?://)", r"\1", url)

    # Nitter proxies Twitter videos as /pic/video.twimg.com/...
    url = re.sub(
        r"https?://[^/]+/pic/video\.twimg\.com/",
        "https://video.twimg.com/",
        url,
    )

    # Nitter often uses /pic/orig/media/... or /pic/media/... proxies.
    # Both must become the real Twitter CDN /media/... URL.
    url = re.sub(
        r"https?://[^/]+/pic/(?:orig/)?media/",
        "https://pbs.twimg.com/media/",
        url,
    )

    return url


def is_twitter_video_url(url: str) -> bool:
    lowered = url.lower()
    return "video.twimg.com" in lowered or re.search(r"\.(mp4|mov|m4v|webm)(?:[?#]|$)", lowered) is not None


def add_media_item(media: list, url: str, media_type: str):
    if not url:
        return
    if is_twitter_video_url(url):
        media_type = "video"
    if ("/profile_images/" in url) or ("emoji" in url.lower()):
        return
    if url not in [x["url"] for x in media]:
        media.append({"url": url, "type": media_type})


def extract_media_from_feed_item(item: dict, base_url: str) -> list:
    """Fallback: pull media directly from RSS/JSON content when the Nitter page fails."""
    media = []
    content_html = item.get("content_html", "") or ""

    if item.get("image"):
        add_media_item(
            media,
            normalize_nitter_media_url(item["image"], base_url),
            "photo",
        )

    for m in re.finditer(
        r"<img[^>]+src=[\"']([^\"']+)[\"']",
        content_html,
        re.I,
    ):
        add_media_item(
            media,
            normalize_nitter_media_url(m.group(1), base_url),
            "photo",
        )

    for m in re.finditer(
        r"<source[^>]+src=[\"']([^\"']+\.mp4[^\"']*)[\"']",
        content_html,
        re.I,
    ):
        add_media_item(
            media,
            normalize_nitter_media_url(m.group(1), base_url),
            "video",
        )

    for m in re.finditer(
        r"(https?://[^\"'<>\s]*(?:video\.twimg\.com|/pic/video\.twimg\.com)[^\"'<>\s]*\.mp4[^\"'<>\s]*)",
        html.unescape(content_html),
        re.I,
    ):
        add_media_item(
            media,
            normalize_nitter_media_url(m.group(1), base_url),
            "video",
        )

    # Some feeds escape the Nitter /pic/... URL as plain text inside HTML.
    for m in re.finditer(
        r"(https?://[^\"'<>\s]+/(?:pic|media)/[^\"'<>\s]+)",
        html.unescape(content_html),
        re.I,
    ):
        normalized = normalize_nitter_media_url(m.group(1), base_url)
        add_media_item(
            media,
            normalized,
            "video" if is_twitter_video_url(normalized) else "photo",
        )

    if media:
        print(f"  Найдено {len(media)} медиафайлов в RSS item")
    return media[:10]


def extract_media_from_nitter_page(post_url: str) -> list:
    """
    Парсим HTML страницу поста на Nitter: там обычно есть все картинки и видео,
    а не только первое медиа из RSS.
    """
    for mirror in NITTER_MIRRORS:
        media = []
        page_url = re.sub(r"https?://[^/]+", mirror, post_url)
        try:
            r = requests.get(page_url, timeout=12, headers=REQUEST_HEADERS)
            if r.status_code != 200:
                print(f"  Nitter {mirror}: HTTP {r.status_code}")
                continue

            html_text = r.text

            for m in re.finditer(
                r"<img[^>]+src=[\"']([^\"']*(?:pbs\.twimg\.com|/pic/|nitter)[^\"']*)[\"']",
                html_text,
                re.I,
            ):
                url = normalize_nitter_media_url(m.group(1), page_url)
                add_media_item(media, url, "photo")

            for m in re.finditer(
                r"<source[^>]+src=[\"']([^\"']+\.mp4[^\"']*)[\"']",
                html_text,
                re.I,
            ):
                url = normalize_nitter_media_url(m.group(1), page_url)
                add_media_item(media, url, "video")

            for m in re.finditer(
                r"(?:src|href|data-url|data-src)=[\"']([^\"']*(?:video\.twimg\.com|/pic/video\.twimg\.com|\.mp4)[^\"']*)[\"']",
                html_text,
                re.I,
            ):
                url = normalize_nitter_media_url(m.group(1), page_url)
                add_media_item(media, url, "video")

            for m in re.finditer(
                r"(https?://[^\"'<>\s]*(?:video\.twimg\.com|/pic/video\.twimg\.com)[^\"'<>\s]*\.mp4[^\"'<>\s]*)",
                html.unescape(html_text),
                re.I,
            ):
                url = normalize_nitter_media_url(m.group(1), page_url)
                add_media_item(media, url, "video")

            if media:
                print(f"  Найдено {len(media)} медиафайлов на {mirror}")
                for item in media[:10]:
                    print(f"    media: {item['type']} {item['url'][:160]}")
                return media[:10]

        except Exception as e:
            print(f"  Nitter HTML ошибка ({mirror}): {e}")

    return []


# ════════════════════════════════════════════════════════════
# INSTAGRAM через Apify
# ════════════════════════════════════════════════════════════

def instagram_account_to_username(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if not re.search(r"^https?://", value, flags=re.I):
        return value.lstrip("@").strip("/")
    try:
        parsed = urlparse(value)
    except Exception:
        return value
    path_parts = [part for part in (parsed.path or "").split("/") if part]
    if not path_parts:
        return ""
    if path_parts[0].lower() in {"p", "reel", "tv"}:
        return value
    return path_parts[0].lstrip("@")


def build_apify_instagram_input() -> dict:
    usernames = []
    direct_urls = []
    for account in APIFY_INSTAGRAM_ACCOUNTS:
        normalized = instagram_account_to_username(account)
        if not normalized:
            continue
        if re.search(r"^https?://", normalized, flags=re.I):
            direct_urls.append(normalized)
        else:
            usernames.append(normalized)

    actor_input = {
        "username": usernames,
        "resultsLimit": APIFY_INSTAGRAM_RESULTS_LIMIT,
        "skipPinnedPosts": APIFY_INSTAGRAM_SKIP_PINNED,
    }
    if direct_urls:
        actor_input["directUrls"] = direct_urls
    if APIFY_INSTAGRAM_ONLY_NEWER_THAN:
        actor_input["onlyPostsNewerThan"] = APIFY_INSTAGRAM_ONLY_NEWER_THAN
    if APIFY_INSTAGRAM_DATA_DETAIL_LEVEL:
        actor_input["dataDetailLevel"] = APIFY_INSTAGRAM_DATA_DETAIL_LEVEL

    if APIFY_INSTAGRAM_INPUT_JSON:
        try:
            override = json.loads(APIFY_INSTAGRAM_INPUT_JSON)
            if isinstance(override, dict):
                actor_input.update(override)
        except Exception as e:
            print(f"  APIFY_INSTAGRAM_INPUT_JSON не разобран: {e}")

    return actor_input


def fetch_instagram_posts() -> list:
    """Fetch Instagram posts from Apify Instagram Post Scraper."""
    if not APIFY_TOKEN:
        print("  APIFY_TOKEN не задан — пропускаем")
        return []
    if not APIFY_INSTAGRAM_ACCOUNTS and not APIFY_INSTAGRAM_INPUT_JSON:
        print("  STEP2_INSTAGRAM_ACCOUNTS не задан — пропускаем")
        return []

    actor_input = build_apify_instagram_input()
    url = f"{APIFY_BASE_URL}/acts/{APIFY_INSTAGRAM_ACTOR}/run-sync-get-dataset-items"
    params = {
        "format": "json",
        "clean": "1",
        "timeout": str(APIFY_RUN_TIMEOUT),
    }
    if APIFY_MAX_ITEMS:
        params["maxItems"] = APIFY_MAX_ITEMS
    if APIFY_MAX_TOTAL_CHARGE_USD:
        params["maxTotalChargeUsd"] = APIFY_MAX_TOTAL_CHARGE_USD

    try:
        r = requests.post(
            url,
            params=params,
            json=actor_input,
            headers={
                **REQUEST_HEADERS,
                "Authorization": f"Bearer {APIFY_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=APIFY_RUN_TIMEOUT + 45,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items", []) or data.get("data", {}).get("items", [])
        return []
    except Exception as e:
        print(f"  Apify ошибка: {e}")
        return []


def instagram_media_from_apify_post(post: dict) -> list:
    media_items = []
    seen_keys = set()

    def as_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def add(url: str, media_type: str):
        if not url:
            return
        url = html.unescape(str(url)).strip()
        if not url or url.startswith("data:"):
            return
        if "instagram.com/p/" in url.lower() or "instagram.com/reel/" in url.lower():
            return
        identity = canonical_media_url(url)
        if identity and identity not in seen_keys:
            seen_keys.add(identity)
            media_items.append({"url": url, "type": media_type})

    def media_type_from_node(node: dict, url: str = "") -> str:
        marker = f"{node.get('type', '')} {node.get('__typename', '')} {url}".lower()
        if "video" in marker or re.search(r"\.(mp4|mov|m4v|webm)(?:[?#]|$)", marker):
            return "video"
        return "photo"

    def add_url_value(value, media_type: str):
        if isinstance(value, str):
            add(value, media_type)
        elif isinstance(value, dict):
            add_post_media(value)

    def add_images(value):
        for image in as_list(value):
            if isinstance(image, dict):
                for key in ("url", "src", "displayUrl", "imageUrl"):
                    add_url_value(image.get(key), "photo")
            else:
                add_url_value(image, "photo")

    def add_post_media(node: dict):
        if not isinstance(node, dict):
            return

        before_children = len(media_items)
        for key in ("childPosts", "children", "sidecarChildren"):
            for child in as_list(node.get(key)):
                add_post_media(child)
        if len(media_items) > before_children:
            return

        for key in ("videoUrl", "video_url", "videoURL", "video"):
            add_url_value(node.get(key), "video")

        has_video = len(media_items) > before_children
        node_type = media_type_from_node(node)
        if not has_video and node_type == "video":
            for key in ("url", "mediaUrl", "media_url"):
                add_url_value(node.get(key), "video")

        if not has_video:
            for key in (
                "displayUrl",
                "display_url",
                "thumbnailUrl",
                "thumbnail_url",
                "thumbnailSrc",
                "thumbnail_src",
                "imageUrl",
                "image_url",
                "image",
                "imageSrc",
                "image_src",
                "photoUrl",
                "photo_url",
                "mediaUrl",
                "media_url",
            ):
                add_url_value(node.get(key), "photo")
            add_images(node.get("images"))
            add_images(node.get("displayResources"))
            add_images(node.get("thumbnailResources"))

    add_post_media(post)

    if media_items:
        print(f"  Apify media найдено: {len(media_items)}")
        for item in media_items[:TELEGRAM_MEDIA_GROUP_LIMIT]:
            print(f"    instagram media: {item['type']} {item['url'][:160]}")
        if len(media_items) > TELEGRAM_MEDIA_GROUP_LIMIT:
            print(f"    ...и еще {len(media_items) - TELEGRAM_MEDIA_GROUP_LIMIT} медиа")

    return media_items


def instagram_caption_from_apify_post(post: dict) -> str:
    return (
        post.get("caption")
        or post.get("text")
        or post.get("description")
        or post.get("title")
        or ""
    ).strip()


def instagram_date_from_apify_post(post: dict) -> str:
    value = (
        post.get("timestamp")
        or post.get("date")
        or post.get("takenAt")
        or post.get("takenAtTimestamp")
        or post.get("createdAt")
        or ""
    )
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value or "")


# ════════════════════════════════════════════════════════════
# ПЕРЕВОД И РЕДАКТУРА
# ════════════════════════════════════════════════════════════

def translate_deepl(text: str) -> str:
    if not text or not DEEPL_KEY:
        return text
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={
                "Authorization": f"DeepL-Auth-Key {DEEPL_KEY}",
                "Content-Type": "application/json",
            },
            json={"text": [text], "target_lang": "RU"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["translations"][0]["text"].strip()
    except Exception as e:
        print(f"  DeepL ошибка: {e}")
        return text


def edit_openrouter(text: str) -> str:
    if not text or not OPENROUTER_KEY:
        return text
    prompt = (
        "Ты опытный редактор русскоязычного Telegram-канала.\n"
        "Отредактируй текст так, чтобы он звучал естественно по-русски.\n"
        "Правила:\n"
        "- Сохрани все факты и эмодзи.\n"
        "- Не используй HTML, Markdown и ссылки.\n"
        "- Не разбивай одно предложение на несколько строк.\n"
        "- Не используй символ | как разделитель.\n"
        "- Формат интервью: ❓ вопрос?, пустая строка, 🎙️ Имя:, цитата в «ёлочках».\n"
        "- Списки стран: «флаг Страна — Игрок» в одной строке, без кавычек вокруг имён.\n"
        "- Перед хештегом #... оставь пустую строку.\n"
        "- Верни ТОЛЬКО готовый текст.\n\n"
        + text
    )
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  OpenRouter ошибка: {e}")
        return text


def telegram_html_tags_balanced(text: str) -> bool:
    stack = []
    for match in re.finditer(r"<\s*(/?)\s*(b|blockquote|a)(?:\s+[^>]*)?>", text or "", re.I):
        closing, tag = match.group(1), match.group(2).lower()
        if closing:
            if not stack or stack[-1] != tag:
                return False
            stack.pop()
        else:
            stack.append(tag)
    return not stack


def sanitize_telegram_layout_html(text: str) -> str:
    """Keep only Telegram tags that the layout AI is allowed to return."""
    text = html.unescape(str(text or ""))
    allowed_tag = re.compile(r"<\s*(/?)\s*(b|blockquote)\s*>", re.I)
    output = []
    pos = 0

    for match in allowed_tag.finditer(text):
        output.append(html.escape(text[pos:match.start()], quote=False))
        closing, tag = match.group(1), match.group(2).lower()
        output.append(f"</{tag}>" if closing else f"<{tag}>")
        pos = match.end()

    output.append(html.escape(text[pos:], quote=False))
    cleaned = "".join(output)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


INTRO_LABEL_RE = r"(?:последние\s+новости|последняя\s+новость|главная\s+новость|новости|новость|официально|срочно|эксклюзив|breaking(?:\s+news)?|official|latest\s+news)"
INTRO_LABEL_PREFIX_RE = r"^[ \t]*(?:[^\w<#@\n]+[ \t]*)*"
CAPS_WORD_RE = re.compile(r"(?<![#/@\w-])([А-ЯЁ]{1,}|[A-Z]{3,})(?![\w-])", re.UNICODE)
HTML_LAYOUT_TAG_RE = re.compile(r"(</?(?:b|blockquote)>)", re.I)
CAPS_WORD_EXCEPTIONS = {
    "VAR", "FIFA", "UEFA", "UCL", "PSG", "MLS", "NBA",
    "РФ", "США", "ОАЭ", "АПЛ", "ЛЧ", "ЛЕ", "РПЛ", "ЦСКА", "ПСЖ",
}


def strip_intro_labels(text: str) -> str:
    text = str(text or "")

    intro_prefix_re = re.compile(
        r"^(?P<indent>[ \t]*)(?P<open><b>\s*)?"
        r"(?P<marker>(?:[^\w<#@\n:：!—–-]+[ \t]*)*?)"
        + INTRO_LABEL_RE
        + r"\s*(?:[:：!]|[—–-])+[ \t]*(?P<close></b>[ \t]*)?",
        flags=re.I,
    )

    def clean_line(line: str) -> str:
        match = intro_prefix_re.match(line)
        if not match:
            return line
        marker = (match.group("marker") or "").strip()
        rest = line[match.end():]
        if not marker:
            return (match.group("indent") or "") + rest.lstrip(" \t")
        if match.group("open") or match.group("close"):
            prefix = f"{match.group('indent') or ''}<b>{marker}</b>"
        else:
            prefix = f"{match.group('indent') or ''}{marker}"
        separator = "" if not rest or rest[0] in " \t\r\n" else " "
        return prefix + separator + rest.lstrip(" \t")

    text = "".join(clean_line(line) for line in text.splitlines(keepends=True))
    text = re.sub(r"<b>\s*</b>\s*", "", text, flags=re.I)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_sentence_capitalization(text: str) -> str:
    def normalize_caps_word(match: re.Match) -> str:
        word = match.group(1)
        if word.upper() in CAPS_WORD_EXCEPTIONS:
            return word.upper()
        if not word.isupper():
            return word
        return word.lower()

    def capitalize_start(match: re.Match) -> str:
        return match.group(1) + match.group(2).upper()

    text = CAPS_WORD_RE.sub(normalize_caps_word, text)
    text = re.sub(r"(^|[\n\r]+|(?<=[.!?…])\s+)([a-zа-яё])", capitalize_start, text)
    text = re.sub(r"([«“\"(]\s*)([a-zа-яё])", capitalize_start, text)
    return text


def normalize_posting_text(text: str) -> str:
    text = strip_intro_labels(text)
    parts = HTML_LAYOUT_TAG_RE.split(text)
    normalized = [
        part if HTML_LAYOUT_TAG_RE.fullmatch(part or "") else normalize_sentence_capitalization(part)
        for part in parts
    ]
    return strip_intro_labels("".join(normalized))


def plain_layout_text(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"</(?:p|blockquote)\s*>", "\n", text, flags=re.I)
    text = strip_html_tags(text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def content_preserved(original: str, candidate: str) -> bool:
    original_plain = plain_layout_text(original)
    candidate_plain = plain_layout_text(candidate)
    if not original_plain or not candidate_plain:
        return False

    length_ratio = len(candidate_plain) / max(1, len(original_plain))
    if length_ratio < 0.72 or length_ratio > 1.35:
        return False

    original_tokens = news_tokens(original_plain)
    candidate_tokens = news_tokens(candidate_plain)
    if not original_tokens:
        return True
    if not candidate_tokens:
        return False

    overlap = len(original_tokens & candidate_tokens) / max(1, len(original_tokens))
    return overlap >= 0.72


def protected_formatting_preserved(original: str, candidate: str) -> bool:
    original_blockquotes = len(re.findall(r"<blockquote\b", original or "", flags=re.I))
    candidate_blockquotes = len(re.findall(r"<blockquote\b", candidate or "", flags=re.I))
    if candidate_blockquotes < original_blockquotes:
        return False

    original_headings = []
    for match in re.finditer(r"<b>(.*?)</b>:\s*\n", original or "", flags=re.I | re.S):
        heading = plain_layout_text(match.group(1))
        if heading:
            original_headings.append(heading)

    for heading in original_headings:
        pattern = (
            r"<b>\s*"
            + re.escape(html.escape(heading, quote=False))
            + r"\s*</b>:\s*\n"
        )
        if not re.search(pattern, candidate or "", flags=re.I):
            return False

    return True


def local_layout_repair(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    plain = strip_html_tags(text)
    plain = preprocess_post_text(plain)
    interview = try_format_interview_qa(plain)
    if interview:
        return ensure_hashtag_spacing(interview)

    text = plain
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    repaired = []
    speaker_answer_mode = False
    speaker_answer_buf: list[str] = []

    def flush_speaker_answer():
        nonlocal speaker_answer_mode, speaker_answer_buf
        if not speaker_answer_buf:
            speaker_answer_mode = False
            return
        block_text = "\n".join(speaker_answer_buf).strip()
        speaker_answer_buf = []
        speaker_answer_mode = False
        if not block_text:
            return
        # If interview already contains «...» we don't add another wrapper.
        if "«" in block_text or "»" in block_text:
            repaired.append(f"<blockquote>{html.escape(block_text, quote=False)}</blockquote>")
        else:
            repaired.append(f"<blockquote>«{html.escape(block_text, quote=False)}»</blockquote>")

    def split_embedded_checkmarks(s: str) -> list[str]:
        """If ✅ is embedded into a sentence, split into separate bullet lines."""
        if "✅" not in s:
            return [s]
        positions = [m.start() for m in re.finditer("✅", s)]
        if len(positions) == 1 and positions[0] > 0:
            before = s[: positions[0]].strip()
            after = s[positions[0] :].strip()
            if before and after:
                return [before, after]
            return [s]
        parts: list[str] = []
        if positions and positions[0] > 0:
            head = s[: positions[0]].strip()
            if head:
                parts.append(head)
        for idx, pos in enumerate(positions):
            end = positions[idx + 1] if idx + 1 < len(positions) else len(s)
            seg = s[pos:end].strip()
            if seg:
                parts.append(seg)
        return parts or [s]

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_speaker_answer()
            if repaired and repaired[-1] != "":
                repaired.append("")
            continue

        # Иногда после автоперевода отдельная строка выглядит как ". 😂".
        line = re.sub(r"^[.·]\s+(?=[^\w\s]|$)", "", line, flags=re.UNICODE)

        # When we already saw a speaker-only heading "Имя Фамилия:" — wrap everything after it into <blockquote>.
        if speaker_answer_mode:
            stop = False
            if line.startswith("#") or line.startswith("❓"):
                stop = True
            elif ":" in line:
                h, _ = split_colon_heading(strip_html_tags(line))
                if h and looks_like_speaker_heading(h):
                    stop = True
            if stop:
                flush_speaker_answer()
            else:
                speaker_answer_buf.append(line)
                continue

        parts = split_embedded_checkmarks(line)
        for part in parts:
            line_part = part.strip()
            if not line_part:
                continue

            # detect speaker-only heading and switch to blockquote mode
            if (
                ":" in line_part
                and "<b>" not in line_part.lower()
                and "<a " not in line_part.lower()
                and not re.search(r"https?://", line_part)
            ):
                heading, body = split_colon_heading(strip_html_tags(line_part))
                if heading:
                    rendered_heading = render_colon_heading(heading)
                    repaired.append(rendered_heading)
                    # Start blockquote for speaker answer when the heading is alone.
                    if not body and looks_like_speaker_heading(heading):
                        speaker_answer_mode = True
                        speaker_answer_buf = []
                    elif body:
                        repaired.append(html.escape(body, quote=False))
                    continue

            if looks_like_country_player_item(line_part):
                left, _, right = re.split(r"(—|–)", line_part, maxsplit=1)
                player = extract_quote_text(right)
                if player:
                    repaired.append(f"{left.strip()} — {player}")
                    continue

            repaired.append(line_part)
    flush_speaker_answer()

    return ensure_hashtag_spacing(normalize_angle_quotes("\n".join(repaired).strip()))


def parse_layout_ai_response(content: str) -> str:
    content = (content or "").strip()
    content = re.sub(r"^```(?:json|html)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    content = re.sub(
        r"^\s*(?:вот|держи)\s+[^:\n]{0,80}:\s*",
        "",
        content,
        flags=re.I,
    )

    try:
        match = re.search(r"\{.*\}", content, flags=re.S)
        data = json.loads(match.group(0) if match else content)
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
    except Exception:
        pass

    return content.strip()


def gemini_layout_text(prompt: str) -> str:
    r = requests.post(
        f"{GEMINI_BASE_URL}/models/{LAYOUT_AI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [
                {
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 1400,
                "candidateCount": 1,
            },
        },
        timeout=LAYOUT_AI_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"].get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def ai_check_telegram_layout(full_text: str) -> str:
    locally_repaired = local_layout_repair(normalize_posting_text(full_text))
    locally_repaired = normalize_posting_text(locally_repaired)
    rag_candidate = rag_check_telegram_layout(locally_repaired)
    if rag_candidate != locally_repaired:
        locally_repaired = normalize_posting_text(rag_candidate)

    if not GEMINI_API_KEY or not LAYOUT_AI_MODEL:
        print("  Gemini-проверка верстки пропущена: GEMINI_API_KEY не задан")
        return normalize_posting_text(locally_repaired)
    if len(locally_repaired) > LAYOUT_AI_MAX_CHARS:
        print("  Gemini-проверка верстки пропущена: текст слишком длинный")
        return normalize_posting_text(locally_repaired)

    prompt = (
        "Поправь форматирование для телеграм канала, не меняй сам текст можно менять "
        "только форматирование абзацев, строк и расположения эмоджи. Выводи только "
        "сам отформатированный текст, без вспомогательных слов вроде "
        "\"вот отличный вариант\":\n\n"
        "Текст будет отправлен в Telegram parse_mode=HTML.\n"
        "Если в тексте уже есть <blockquote>...</blockquote>, не убирай это форматирование.\n"
        "Склеивай только неестественные переносы внутри одного предложения "
        "(строка обрывается на предлоге или следующая строка — продолжение с маленькой буквы). "
        "Не склеивай строки после 🎙️ <b>Имя</b>: и не склеивай блоки интервью.\n"
        "Правило двоеточия для спикера: 🎙️ <b>Имя Фамилия</b>:, затем цитата на следующей строке.\n"
        "Дополнительные правила канала:\n"
        "1) Интервью вопрос-ответ: вопрос одной строкой ❓ <b>текст вопроса?</b>, "
        "после вопроса пустая строка; спикер 🎙️ <b>Имя Фамилия</b>:; ответ сразу "
        "следующей строкой <blockquote>«текст ответа»</blockquote> без пустой строки "
        "между спикером и цитатой; между вопросом и спикером — только одна пустая строка.\n"
        "2) Статистика: каждый показатель на отдельной строке в формате "
        "<эмодзи> <число> <показатель>, без склеивания нескольких показателей в одну строку.\n"
        "3) Цитаты одного спикера: первая строка 🎙️ <b>Имя Фамилия</b>:, каждая "
        "цитата отдельным <blockquote>«текст цитаты»</blockquote>, между цитатами пустая строка.\n"
        "4) Кавычки всегда должны быть ёлочками «». Текст в кавычках короче 3 слов "
        "не оформляй в <blockquote>, кроме случая, когда предыдущая строка — имя/фамилия спикера.\n"
        "5) Убери декоративные символы | после эмодзи и в конце строки.\n"
        "6) Списки «страна — игрок»: одна строка, имена без «».\n"
        "7) Перед хештегом #... — пустая строка.\n"
        "8) Между законченными предложениями (. ! ?) — пустая строка, если это разные абзацы.\n"
        "9) Источник в скобках: [username], без лишних пробелов и дефисов внутри.\n"
        "Разрешенные HTML-теги: <b>, </b>, <blockquote>, </blockquote>. "
        "Не используй Markdown, ссылки и другие теги.\n\n"
        f"ТЕКСТ:\n{locally_repaired}"
    )

    try:
        content = gemini_layout_text(prompt)
        candidate_raw = parse_layout_ai_response(content)
        candidate = sanitize_telegram_layout_html(candidate_raw)
        candidate = local_layout_repair(candidate)
        candidate = normalize_posting_text(candidate)

        if not candidate:
            return normalize_posting_text(locally_repaired)
        if not telegram_html_tags_balanced(candidate):
            print("  Gemini-верстка отклонена: HTML-теги не сбалансированы")
            save_hard_case(
                stage="gemini_layout",
                reason="unbalanced_html_tags",
                original=locally_repaired,
                candidate=candidate,
            )
            return normalize_posting_text(locally_repaired)
        if not protected_formatting_preserved(locally_repaired, candidate):
            print("  Gemini-верстка отклонена: удалены обязательные quote/переносы")
            save_hard_case(
                stage="gemini_layout",
                reason="protected_formatting_lost",
                original=locally_repaired,
                candidate=candidate,
            )
            return normalize_posting_text(locally_repaired)
        if not content_preserved(locally_repaired, candidate):
            print("  Gemini-верстка отклонена: текст изменился слишком сильно")
            save_hard_case(
                stage="gemini_layout",
                reason="content_changed_too_much",
                original=locally_repaired,
                candidate=candidate,
            )
            return normalize_posting_text(locally_repaired)
        # Double-check: if local repair has interview answers (blockquotes) but Gemini removed them — revert.
        if "<blockquote>" in locally_repaired and "<blockquote>" not in candidate:
            print("  Gemini-верстка отклонена: потеряно blockquote после спикера")
            save_hard_case(
                stage="gemini_layout",
                reason="lost_blockquote_after_speaker",
                original=locally_repaired,
                candidate=candidate,
            )
            return normalize_posting_text(locally_repaired)
        if candidate != locally_repaired:
            print("  Gemini-верстка поправлена")
        else:
            print("  Gemini-верстка проверена")
        return candidate
    except Exception as e:
        print(f"  Gemini-проверка верстки недоступна: {e}")
        return normalize_posting_text(locally_repaired)


def rag_check_telegram_layout(text: str) -> str:
    if not FORMAT_RAG_ENABLED:
        return text
    if not OPENROUTER_KEY:
        print("  RAG-проверка верстки пропущена: OPENROUTER_KEY не задан")
        return text

    examples = load_examples()
    if not examples:
        print("  RAG-проверка верстки пропущена: нет примеров в format_model/examples.jsonl")
        return text

    selected = select_similar_examples(text, examples)
    prompt = build_layout_prompt(text, selected)
    result = call_openrouter_layout_checker(prompt, OPENROUTER_KEY, FORMAT_RAG_MODEL)
    if not result:
        print("  RAG-проверка верстки недоступна: неверный ответ модели")
        save_hard_case(
            stage="rag_layout",
            reason="invalid_json_or_empty_result",
            original=text,
            candidate="",
        )
        return text

    fixed_raw = result.get("fixed_text", "")
    fixed_text = sanitize_telegram_layout_html(fixed_raw)
    fixed_text = local_layout_repair(fixed_text)
    fixed_text = normalize_posting_text(fixed_text)
    if not fixed_text:
        save_hard_case(
            stage="rag_layout",
            reason="empty_fixed_text_after_sanitize",
            original=text,
            candidate=fixed_raw,
        )
        return text
    if not telegram_html_tags_balanced(fixed_text):
        print("  RAG-верстка отклонена: HTML-теги не сбалансированы")
        save_hard_case(
            stage="rag_layout",
            reason="unbalanced_html_tags",
            original=text,
            candidate=fixed_text,
        )
        return text
    if not protected_formatting_preserved(text, fixed_text):
        print("  RAG-верстка отклонена: удалены обязательные quote/переносы")
        save_hard_case(
            stage="rag_layout",
            reason="protected_formatting_lost",
            original=text,
            candidate=fixed_text,
        )
        return text
    if not content_preserved(text, fixed_text):
        print("  RAG-верстка отклонена: текст изменился слишком сильно")
        save_hard_case(
            stage="rag_layout",
            reason="content_changed_too_much",
            original=text,
            candidate=fixed_text,
        )
        return text

    if fixed_text != text:
        print(f"  RAG-верстка поправлена (примеров: {len(selected)})")
    else:
        print(f"  RAG-верстка проверена (примеров: {len(selected)})")
    return fixed_text


# ════════════════════════════════════════════════════════════
# ОТПРАВКА В TELEGRAM
# ════════════════════════════════════════════════════════════

def log_tg_error(method: str, res: dict):
    if not res.get("ok"):
        print(f"  Telegram ошибка {method}: {res.get('description')} | {res}")


def tg(method: str, payload: dict) -> dict:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=25,
        )
        res = r.json()
        log_tg_error(method, res)
        return res
    except Exception as e:
        print(f"  Telegram request ошибка {method}: {e}")
        return {"ok": False, "description": str(e)}


def split_caption(full_text: str, has_media: bool) -> tuple:
    limit = CAPTION_LIMIT if has_media else MSG_LIMIT
    sig_len = len(SIGNATURE)
    max_text = limit - sig_len

    if len(full_text) <= max_text:
        return full_text + SIGNATURE, None

    cut = 0
    for separator in ("\n\n", "\n", " "):
        probe = full_text.rfind(separator, 0, max_text)
        while probe > 0:
            left = full_text[:probe].strip()
            right = full_text[probe:].strip()
            if (
                left
                and right
                and telegram_html_tags_balanced(left)
                and telegram_html_tags_balanced(right)
            ):
                cut = probe
                break
            probe = full_text.rfind(separator, 0, probe)
        if cut:
            break

    if cut < 1:
        cut = max_text

    return (
        full_text[:cut].strip() + SIGNATURE,
        full_text[cut:].strip() + SIGNATURE,
    )


def plain_text_for_crosspost(full_text: str) -> str:
    text = full_text or ""
    text = re.sub(
        r"\n\n#NicoPaz\s*\|\s*<a\s+href=[\"'][^\"']+[\"']>Follow us</a>\s*$",
        CROSSPOST_SIGNATURE,
        text,
        flags=re.I,
    )
    text = text.replace(SIGNATURE, CROSSPOST_SIGNATURE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = strip_html_tags(text)
    text = html.unescape(text)
    text = text.replace("Follow us", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, limit: int) -> list:
    text = text.strip()
    if not text:
        return []
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < 1:
            cut = text.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def post_to_vk(full_text: str) -> bool:
    if not VK_ACCESS_TOKEN or not VK_OWNER_ID:
        return False

    text = plain_text_for_crosspost(full_text)
    if not text:
        return False

    try:
        r = requests.post(
            "https://api.vk.com/method/wall.post",
            data={
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
                "owner_id": VK_OWNER_ID,
                "from_group": VK_FROM_GROUP,
                "message": text,
            },
            timeout=25,
        )
        res = r.json()
        if "error" in res:
            print(f"  VK ошибка wall.post: {res['error']}")
            return False
        print(f"  VK опубликовано: {res.get('response')}")
        return True
    except Exception as e:
        print(f"  VK request ошибка: {e}")
        return False


def post_to_max(full_text: str) -> bool:
    if not MAX_TOKEN or not (MAX_CHAT_ID or MAX_USER_ID):
        return False

    text = plain_text_for_crosspost(full_text)
    parts = split_text(text, MAX_TEXT_LIMIT)
    if not parts:
        return False

    params = {}
    if MAX_CHAT_ID:
        params["chat_id"] = MAX_CHAT_ID
    else:
        params["user_id"] = MAX_USER_ID

    ok = True
    for i, part in enumerate(parts):
        try:
            r = requests.post(
                "https://platform-api.max.ru/messages",
                params=params,
                headers={
                    "Authorization": MAX_TOKEN,
                    "Content-Type": "application/json",
                },
                json={"text": part, "notify": True},
                timeout=25,
            )
            try:
                res = r.json()
            except Exception:
                res = {"status_code": r.status_code, "text": r.text[:500]}
            if r.status_code >= 400:
                print(f"  MAX ошибка messages: {res}")
                ok = False
                break
            print(f"  MAX опубликовано: часть {i + 1}/{len(parts)}")
            if i < len(parts) - 1:
                time.sleep(1)
        except Exception as e:
            print(f"  MAX request ошибка: {e}")
            ok = False
            break
    return ok


def crosspost_after_telegram(full_text: str):
    vk_enabled = bool(VK_ACCESS_TOKEN and VK_OWNER_ID)
    max_enabled = bool(MAX_TOKEN and (MAX_CHAT_ID or MAX_USER_ID))

    if not vk_enabled and not max_enabled:
        return

    print("  Кросспостинг после Telegram...")
    if vk_enabled:
        post_to_vk(full_text)
    if max_enabled:
        post_to_max(full_text)


def media_extension(media_type: str, content_type: str, url: str) -> str:
    if media_type == "video":
        return ".mp4"
    if "png" in content_type.lower() or url.lower().endswith(".png"):
        return ".png"
    if "webp" in content_type.lower() or url.lower().endswith(".webp"):
        return ".webp"
    return ".jpg"


def download_media(url: str, media_type: str, index: int) -> Path | None:
    MEDIA_DIR.mkdir(exist_ok=True)
    try:
        r = requests.get(
            url,
            timeout=45,
            headers=REQUEST_HEADERS,
            stream=True,
            allow_redirects=True,
        )
        r.raise_for_status()

        content_type = r.headers.get("Content-Type", "")
        ext = media_extension(media_type, content_type, url)
        path = MEDIA_DIR / f"media_{int(time.time())}_{index}{ext}"

        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    total += len(chunk)
                    f.write(chunk)

        if total < 1024:
            print(f"  Медиа слишком маленькое ({total} байт): {url}")
            path.unlink(missing_ok=True)
            return None

        print(f"  Медиа скачано: {path.name} ({total // 1024} KB)")
        return path

    except Exception as e:
        print(f"  Не удалось скачать медиа {url}: {e}")
        return None


def send_single_media(downloaded: dict, caption: str) -> bool:
    method = "sendVideo" if downloaded["type"] == "video" else "sendPhoto"
    field = "video" if downloaded["type"] == "video" else "photo"

    try:
        with open(downloaded["path"], "rb") as f:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                data={
                    "chat_id": CHANNEL_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={field: f},
                timeout=90,
            ).json()
        log_tg_error(method, res)
        return res.get("ok", False)
    except Exception as e:
        print(f"  Telegram upload ошибка {method}: {e}")
        return False


def send_media_group(downloaded: list, caption: str) -> bool:
    files = {}
    media_arr = []

    try:
        for i, item in enumerate(downloaded[:TELEGRAM_MEDIA_GROUP_LIMIT]):
            attach_name = f"file{i}"
            files[attach_name] = open(item["path"], "rb")

            entry = {"type": item["type"], "media": f"attach://{attach_name}"}
            if i == 0:
                entry["caption"] = caption
                entry["parse_mode"] = "HTML"
            media_arr.append(entry)

        res = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup",
            data={"chat_id": CHANNEL_ID, "media": json.dumps(media_arr)},
            files=files,
            timeout=120,
        ).json()
        log_tg_error("sendMediaGroup", res)
        return res.get("ok", False)

    except Exception as e:
        print(f"  Медиагруппа upload ошибка: {e}")
        return False

    finally:
        for f in files.values():
            f.close()


def send_to_telegram(
    media_items: list,
    full_text: str,
    step4_custom_sig: bool = False,
):
    """Отправить пост в Telegram.

    Возвращает:
      True  — успешно опубликовано
      False — временная ошибка Telegram API (стоит повторить)
      None  — медиа недоступны (404/нет файлов); повтор бессмысленен
    """
    if not media_items:
        print("  Медиа нет — пост не отправляем")
        return None

    downloaded = []

    for i, item in enumerate(media_items):
        path = download_media(item["url"], item["type"], i)
        if path:
            downloaded.append({"path": path, "type": item["type"]})

    if not downloaded:
        print("  Все медиа недоступны — пост не отправляем")
        shutil.rmtree(MEDIA_DIR, ignore_errors=True)
        return None

    # Для шага 4 layout-check уже выполнен снаружи и подпись встроена в full_text
    if step4_custom_sig:
        final_text = full_text
    else:
        final_text = ai_check_telegram_layout(full_text)
    caption, overflow = split_caption(final_text, True)

    sent_ok = True
    chunks = [
        downloaded[i:i + TELEGRAM_MEDIA_GROUP_LIMIT]
        for i in range(0, len(downloaded), TELEGRAM_MEDIA_GROUP_LIMIT)
    ]

    for chunk_index, chunk in enumerate(chunks):
        chunk_caption = caption if chunk_index == 0 else ""
        if len(chunk) == 1:
            chunk_ok = send_single_media(chunk[0], chunk_caption)
        else:
            chunk_ok = send_media_group(chunk, chunk_caption)

        if not chunk_ok:
            sent_ok = False
            print("  Медиа не отправилось — текстом не отправляем")
            break
        if chunk_index < len(chunks) - 1:
            time.sleep(1.5)

    if overflow and sent_ok:
        time.sleep(1.5)
        tg(
            "sendMessage",
            {"chat_id": CHANNEL_ID, "text": overflow, "parse_mode": "HTML"},
        )

    if sent_ok:
        crosspost_after_telegram(final_text)

    shutil.rmtree(MEDIA_DIR, ignore_errors=True)
    return sent_ok


# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ
# ════════════════════════════════════════════════════════════

def nitter_rss_url(account: str) -> str:
    for mirror in NITTER_MIRRORS:
        url = f"{mirror}/{account}/rss"
        try:
            r = requests.get(url, timeout=10, headers=REQUEST_HEADERS)
            if r.status_code == 200 and "<rss" in r.text[:800].lower():
                return url
            print(f"  RSS @{account} на {mirror}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  RSS @{account} на {mirror}: {e}")
    return ""


# ════════════════════════════════════════════════════════════
# ШАГИ
# ════════════════════════════════════════════════════════════

def build_scweet_keyword_query(keywords: list) -> str:
    parts = []
    for keyword in keywords or []:
        keyword = str(keyword or "").strip()
        if not keyword:
            continue
        keyword = keyword.replace('"', '\\"')
        if re.search(r"\s", keyword):
            parts.append(f'"{keyword}"')
        else:
            parts.append(keyword)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"({' OR '.join(parts)})"


def normalize_scweet_tweet_url(url: str, post_id: str = "") -> str:
    url = str(url or "").strip()
    if url.startswith("/"):
        url = f"https://x.com{url}"
    if not url and post_id:
        url = f"https://x.com/i/web/status/{post_id}"
    return url


def twitter_author_from_url(url: str) -> str:
    try:
        path_parts = [part for part in urlparse(url).path.split("/") if part]
    except Exception:
        return ""
    if not path_parts or path_parts[0].lower() in {"i", "intent", "search"}:
        return ""
    return normalize_twitter_account(path_parts[0])


def scweet_result_items(tweets) -> list:
    if not tweets:
        return []
    if isinstance(tweets, dict):
        values = list(tweets.values())
        if values and all(isinstance(value, dict) for value in values):
            return values
        return [tweets]
    return list(tweets)


def is_probable_twitter_media_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return (
        "twimg.com" in lowered
        or "video.twimg.com" in lowered
        or re.search(r"\.(?:jpg|jpeg|png|webp|gif|mp4|mov|m4v|webm)(?:[?#]|$)", lowered) is not None
    )


def flatten_scweet_media_urls(value) -> list[str]:
    urls = []
    if not value:
        return urls

    if isinstance(value, str):
        url = html.unescape(value).strip().strip("'\"")
        if url.startswith("//"):
            url = f"https:{url}"
        if re.match(r"^https?://", url, flags=re.I) and is_probable_twitter_media_url(url):
            urls.append(url)
        return urls

    if isinstance(value, (list, tuple, set)):
        for item in value:
            urls.extend(flatten_scweet_media_urls(item))
        return urls

    if isinstance(value, dict):
        variants = value.get("variants")
        if isinstance(variants, list):
            video_variants = [
                variant
                for variant in variants
                if isinstance(variant, dict)
                and (
                    "video" in str(variant.get("content_type") or "").lower()
                    or is_twitter_video_url(str(variant.get("url") or ""))
                )
            ]
            if video_variants:
                best = max(video_variants, key=lambda item: int(item.get("bitrate") or 0))
                urls.extend(flatten_scweet_media_urls(best.get("url")))

        for item in value.values():
            urls.extend(flatten_scweet_media_urls(item))
    return urls


def extract_scweet_media_items(tw: dict) -> list:
    video_candidates = []
    image_candidates = []

    def add_candidates(value, fallback_type: str):
        for url in flatten_scweet_media_urls(value):
            media_type = "video" if is_twitter_video_url(url) else fallback_type
            target = video_candidates if media_type == "video" else image_candidates
            if url not in target:
                target.append(url)

    video_keys = (
        "video_url",
        "videoUrl",
        "video_urls",
        "videoUrls",
        "video_links",
        "videoLinks",
        "videos",
    )
    image_keys = (
        "image_links",
        "imageLinks",
        "image_urls",
        "imageUrls",
        "images",
        "photos",
        "photo_urls",
        "photoUrls",
    )

    for key in video_keys:
        add_candidates(tw.get(key), "video")
    media_block = tw.get("media")
    add_candidates(media_block, "photo")
    for key in image_keys:
        add_candidates(tw.get(key), "photo")

    # Scweet v5 keeps the original GraphQL payload in raw; videos often live there.
    add_candidates(tw.get("raw"), "photo")

    media_items = []
    if video_candidates:
        for url in video_candidates:
            add_media_item(media_items, url, "video")
        return media_items[:10]

    for url in image_candidates:
        add_media_item(media_items, url, "photo")
    return media_items[:10]


def fetch_scweet_tweets(
    keywords: list,
    from_accounts: list | None = None,
    limit: int = 50,
) -> list:
    """Получает твиты через Scweet (Twitter GraphQL API через куки).

    Возвращает список dict с ключами:
      post_id, post_url, raw, pub_date, media_items
    """
    if not SCWEET_AUTH_TOKEN:
        print("  SCWEET_AUTH_TOKEN не задан — пропускаем Scweet")
        return []

    try:
        from Scweet import Scweet as ScweetClient
    except ImportError:
        print("  Scweet не установлен: pip install scweet")
        return []

    try:
        kwargs = {"auth_token": SCWEET_AUTH_TOKEN}
        if SCWEET_PROXY:
            kwargs["proxy"] = SCWEET_PROXY
        if SCWEET_MANIFEST_SCRAPE_ON_INIT:
            kwargs["manifest_scrape_on_init"] = True
        try:
            s = ScweetClient(**kwargs)
        except TypeError as e:
            if "manifest_scrape_on_init" not in str(e):
                raise
            kwargs.pop("manifest_scrape_on_init", None)
            s = ScweetClient(**kwargs)
    except Exception as e:
        print(f"  Scweet init error: {e}")
        return []

    account_filters = [
        account
        for account in (normalize_twitter_account(a) for a in (from_accounts or []))
        if account
    ]
    keyword_query = build_scweet_keyword_query(keywords)
    if not keyword_query and not account_filters:
        return []

    # Only media posts. Accounts and retweet filtering go through Scweet v5 params.
    search_query = " ".join(part for part in (keyword_query, "filter:media") if part).strip()

    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=48)).strftime("%Y-%m-%d")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"  Scweet query: {search_query[:120]}")
    if account_filters:
        print(f"  Scweet from_users: {len(account_filters)} account(s)")

    try:
        tweets = s.search(
            search_query,
            since=since,
            until=until,
            display_type="Latest",
            limit=limit,
            from_users=account_filters or None,
            tweet_type="exclude_retweets",
            save=False,
        )
    except TypeError as e:
        fallback_parts = [keyword_query]
        if account_filters:
            fallback_parts.append(f"({' OR '.join(f'from:{a}' for a in account_filters)})")
        fallback_parts.extend(["filter:media", "-filter:retweets"])
        fallback_query = " ".join(part for part in fallback_parts if part).strip()
        print(f"  Scweet structured params недоступны ({e}) — fallback query: {fallback_query[:120]}")
        try:
            tweets = s.search(
                fallback_query,
                since=since,
                until=until,
                display_type="Latest",
                limit=limit,
                save=False,
            )
        except Exception as fallback_error:
            print(f"  Scweet search error: {fallback_error}")
            return []
    except Exception as e:
        print(f"  Scweet search error: {e}")
        return []

    results = []
    items = scweet_result_items(tweets)
    for tw in items:
        # Scweet возвращает dict или list в зависимости от версии
        if isinstance(tw, list):
            tw = tw[0] if tw else {}
        if not isinstance(tw, dict):
            continue

        post_id = str(tw.get("tweet_id") or tw.get("tweetId") or tw.get("id") or "").strip()
        post_url = normalize_scweet_tweet_url(tw.get("tweet_url") or tw.get("Tweet URL"), post_id)
        if not post_id:
            ids = twitter_status_ids_from_text(post_url)
            post_id = next(iter(ids), "") if ids else ""

        user = tw.get("user") if isinstance(tw.get("user"), dict) else {}
        author = (
            normalize_twitter_account(
                user.get("screen_name")
                or user.get("username")
                or tw.get("user_screen_name")
                or tw.get("handle")
                or twitter_author_from_url(post_url)
            )
            or "twitter"
        )
        raw = str(tw.get("text") or tw.get("full_text") or tw.get("Text") or "").strip()
        pub_date = str(tw.get("timestamp") or tw.get("created_at") or tw.get("postdate") or tw.get("Timestamp") or "")
        media_items = extract_scweet_media_items(tw)

        if not media_items:
            continue  # без медиа не берём

        results.append({
            "post_id": post_id,
            "post_url": post_url,
            "author": author,
            "raw": raw,
            "pub_date": pub_date,
            "media_items": media_items,
        })

    print(f"  Scweet: получено {len(items)} твитов, с медиа: {len(results)}")
    return results


def step1_twitter_keywords(published: set) -> set:
    print("\n══════ ШАГ 1: Twitter по ключевым словам (Scweet) ══════")
    if not STEP1_KEYWORDS:
        print("  STEP1_KEYWORDS не задан — пропускаем")
        return published
    if not SCWEET_AUTH_TOKEN:
        print("  SCWEET_AUTH_TOKEN не задан — пропускаем")
        return published

    new_count = 0
    source_label = ",".join(STEP1_ACCOUNTS) if STEP1_ACCOUNTS else "global_search"

    tweets = fetch_scweet_tweets(
        keywords=STEP1_KEYWORDS,
        from_accounts=STEP1_ACCOUNTS or None,
        limit=STEP1_LIMIT,
    )

    for tw in tweets:
        post_id     = tw["post_id"]
        post_url    = tw["post_url"]
        pub_date    = tw["pub_date"]
        raw         = clean_text(tw["raw"])
        media_items = tw["media_items"]

        dup = find_duplicate(published, post_id=post_id, url=post_url)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if is_too_old(pub_date):
            print(f"  Старый пост ({pub_date[:16]}) — пропуск")
            continue

        if not raw:
            continue

        dup = find_duplicate(published, post_id=post_id, url=post_url, text=raw, media_items=media_items)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if is_semantic_duplicate(raw, source=source_label, post_id=post_id, url=post_url):
            continue

        translated = translate_deepl(raw)
        edited = edit_openrouter(translated)
        full_text = make_telegram_html(edited, bold_first_line=False)

        print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
        ok = send_to_telegram(media_items, full_text)
        if ok:
            mark_published(published, post_id=post_id, url=post_url, text=raw, media_items=media_items)
            save_published(published)
            record_source_news(raw, step=1, source=source_label, post_id=post_id, url=post_url)
            new_count += 1

    print(f"\n  Итого опубликовано: {new_count}")
    return published
def step2_instagram(published: set) -> set:
    print("\n══════ ШАГ 2: Instagram ══════")
    if not APIFY_TOKEN:
        print("  APIFY_TOKEN не задан — пропускаем")
        return published
    if not APIFY_INSTAGRAM_ACCOUNTS and not APIFY_INSTAGRAM_INPUT_JSON:
        print("  STEP2_INSTAGRAM_ACCOUNTS не задан — пропускаем")
        return published

    posts = fetch_instagram_posts()
    print(f"  Получено Apify posts: {len(posts)}")
    new_count = 0

    if not posts:
        print("  Новых постов нет")
        return published

    for post in posts[:10]:
        post_id = str(post.get("id") or post.get("shortCode") or post.get("url") or "")
        post_url = post.get("url") or ""
        if not post_url and post.get("shortCode"):
            post_url = f"https://www.instagram.com/p/{post['shortCode']}/"
        ig_keys = instagram_extra_duplicate_keys(post)
        if not post_id:
            print("  Пост без id/url — пропуск")
            continue

        dup = find_duplicate(published, post_id=post_id, url=post_url, extra_keys=ig_keys)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        pub_date = instagram_date_from_apify_post(post)
        if is_too_old(pub_date):
            print(f"  Старый Instagram-пост ({pub_date[:16]}) — пропуск")
            continue

        media_items = instagram_media_from_apify_post(post)
        if not media_items:
            print("  Медиа не найдено — пропуск")
            continue

        raw_caption = clean_text(instagram_caption_from_apify_post(post))

        dup = find_duplicate(published, post_id=post_id, url=post_url, text=raw_caption, media_items=media_items, extra_keys=ig_keys)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if raw_caption and is_semantic_duplicate(raw_caption, source="instagram-apify", post_id=post_id, url=post_url):
            continue

        if raw_caption:
            translated = translate_deepl(raw_caption)
            full_text = make_instagram_telegram_html(translated)
        else:
            full_text = make_instagram_telegram_html("")

        print(f"  Публикуем ({len(media_items)} медиа): {post_id}")
        ok = send_to_telegram(media_items, full_text)
        if ok:
            mark_published(published, post_id=post_id, url=post_url, text=raw_caption, media_items=media_items, extra_keys=ig_keys)
            save_published(published)
            record_source_news(raw_caption, step=2, source="instagram-apify", post_id=post_id, url=post_url)
            new_count += 1

    print(f"\n  Итого опубликовано: {new_count}")
    return published
def step3_twitter_accounts(published: set) -> set:
    print("\n══════ ШАГ 3: Twitter-аккаунты (Scweet) ══════")
    if not STEP3_ACCOUNTS:
        print("  STEP3_ACCOUNTS не задан — пропускаем")
        return published
    if not SCWEET_AUTH_TOKEN:
        print("  SCWEET_AUTH_TOKEN не задан — пропускаем")
        return published

    new_count = 0

    # Забираем свежие посты сразу по всем аккаунтам одним запросом
    tweets = fetch_scweet_tweets(
        keywords=[],           # без фильтра по словам — все посты аккаунтов
        from_accounts=STEP3_ACCOUNTS,
        limit=STEP3_LIMIT,
    )

    for tw in tweets:
        post_id     = tw["post_id"]
        post_url    = tw["post_url"]
        pub_date    = tw["pub_date"]
        raw         = clean_text(tw["raw"])
        media_items = tw["media_items"]

        source = tw.get("author") or twitter_author_from_url(post_url) or "twitter"

        dup = find_duplicate(published, post_id=post_id, url=post_url)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if is_too_old(pub_date):
            print(f"  Старый ({pub_date[:16]}) — пропуск")
            continue

        if not raw:
            continue

        dup = find_duplicate(published, post_id=post_id, url=post_url, text=raw, media_items=media_items)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if is_semantic_duplicate(raw, source=f"@{source}", post_id=post_id, url=post_url):
            continue

        translated = translate_deepl(raw)
        edited = edit_openrouter(translated)
        full_text = make_telegram_html(edited, bold_first_line=False)

        print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
        ok = send_to_telegram(media_items, full_text)
        if ok:
            mark_published(published, post_id=post_id, url=post_url, text=raw, media_items=media_items)
            save_published(published)
            record_source_news(raw, step=3, source=f"@{source}", post_id=post_id, url=post_url)
            new_count += 1

    print(f"\n  Итого опубликовано: {new_count}")
    return published
def is_hashtag_post_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    url = str(item.get("url") or "")
    if re.search(r"instagram\.com/(?:p|reel|tv)/", url, flags=re.I):
        return True
    if item.get("shortCode") or item.get("shortcode"):
        return True
    media_keys = (
        "displayUrl",
        "display_url",
        "thumbnailUrl",
        "thumbnail_url",
        "thumbnailSrc",
        "imageUrl",
        "image_url",
        "image",
        "imageSrc",
        "photoUrl",
        "videoUrl",
        "video_url",
        "mediaUrl",
        "media_url",
        "images",
        "childPosts",
        "children",
        "sidecarChildren",
    )
    return any(item.get(key) for key in media_keys)


def flatten_hashtag_posts(data) -> list:
    posts = []
    seen = set()

    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
            return
        if not isinstance(value, dict):
            return
        if is_hashtag_post_item(value):
            key = value.get("id") or value.get("shortCode") or value.get("shortcode") or value.get("url") or id(value)
            if key not in seen:
                seen.add(key)
                posts.append(value)
            return
        for key in ("posts", "latestPosts", "topPosts", "items", "results", "data"):
            if key in value:
                walk(value.get(key))

    walk(data)
    return posts


def fetch_hashtag_posts(hashtag: str) -> list:
    """Получает посты по хэштегу через apify~instagram-hashtag-scraper."""
    if not APIFY_TOKEN:
        return []

    actor_input = {
        "hashtags": [hashtag],
        "resultsLimit": APIFY_HASHTAG_RESULTS_LIMIT,
    }
    if APIFY_HASHTAG_ONLY_NEWER_THAN:
        actor_input["onlyPostsNewerThan"] = APIFY_HASHTAG_ONLY_NEWER_THAN

    url = f"{APIFY_BASE_URL}/acts/{APIFY_HASHTAG_ACTOR}/run-sync-get-dataset-items"
    params = {
        "format": "json",
        "clean": "1",
        "timeout": str(APIFY_RUN_TIMEOUT),
    }
    if APIFY_MAX_TOTAL_CHARGE_USD:
        params["maxTotalChargeUsd"] = APIFY_MAX_TOTAL_CHARGE_USD

    try:
        r = requests.post(
            url,
            params=params,
            json=actor_input,
            headers={
                **REQUEST_HEADERS,
                "Authorization": f"Bearer {APIFY_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=APIFY_RUN_TIMEOUT + 45,
        )
        r.raise_for_status()
        data = r.json()
        posts = flatten_hashtag_posts(data)
        if posts:
            return posts
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items", []) or data.get("data", {}).get("items", [])
        return []
    except Exception as e:
        print(f"  Apify hashtag ошибка (#{hashtag}): {e}")
        return []


def hashtag_post_owner(post: dict) -> str:
    """Извлекает username автора поста из ответа Apify hashtag-scraper.

    Поле может называться по-разному в зависимости от версии актора.
    Возвращает lowercase username без '@', или '' если не найден.
    """
    candidates = [
        post.get("ownerUsername"),
        post.get("username"),
        post.get("authorUsername"),
        post.get("author"),
        post.get("user", {}).get("username") if isinstance(post.get("user"), dict) else None,
        post.get("owner", {}).get("username") if isinstance(post.get("owner"), dict) else None,
        post.get("ownerFullName"),  # fallback — имя, не username
    ]
    for c in candidates:
        if c and isinstance(c, str):
            return c.strip().lstrip("@").lower()
    return ""


def is_allowed_account(post: dict) -> bool:
    """Возвращает True если пост можно публиковать с учётом фильтра по аккаунтам.

    Если STEP4_ALLOWED_ACCOUNTS пустой — разрешены все аккаунты.
    Если задан — разрешён только пост от одного из перечисленных аккаунтов.
    """
    if not STEP4_ALLOWED_ACCOUNTS:
        return True  # фильтр не задан — публикуем всё
    owner = hashtag_post_owner(post)
    if not owner:
        # Не удалось определить автора — пропускаем во избежание мусора
        return False
    return owner in STEP4_ALLOWED_ACCOUNTS


def make_hashtag_telegram_html(caption: str, hashtag: str) -> str:
    """Формирует HTML-текст поста с подписью шага 4."""
    caption = strip_html_tags(caption or "").replace("@", "-").strip()

    # Подпись-заголовок (если задан префикс)
    if STEP4_PREFIX:
        prefix_escaped = html.escape(STEP4_PREFIX.replace("@", "-"), quote=False)
        header = f"<b>{prefix_escaped}</b>"
    else:
        header = f"<b>#{html.escape(hashtag, quote=False)}</b>"

    if caption:
        rendered = make_telegram_html(caption, bold_first_line=False)
        body = f"{header}\n{rendered}"
    else:
        body = header

    # Кастомная подпись шага 4 вместо стандартной SIGNATURE
    sig = f"\n\n{html.escape(STEP4_SIGNATURE, quote=False)}" if STEP4_SIGNATURE else ""
    return body + sig


def step4_instagram_hashtags(published: set) -> set:
    print("\n══════ ШАГ 4: Instagram Hashtags ══════")
    if not APIFY_TOKEN:
        print("  APIFY_TOKEN не задан — пропускаем")
        return published
    if not STEP4_HASHTAGS:
        print("  STEP4_HASHTAGS не задан — пропускаем")
        return published

    new_count = 0
    for hashtag in STEP4_HASHTAGS:
        print(f"\n  #{hashtag}")
        posts = fetch_hashtag_posts(hashtag)
        print(f"  Получено постов: {len(posts)}")

        if STEP4_ALLOWED_ACCOUNTS:
            print(f"  Фильтр по аккаунтам: {', '.join(sorted(STEP4_ALLOWED_ACCOUNTS))}")

        if not posts:
            print("  Новых постов нет")
            continue

        for post in posts[:APIFY_HASHTAG_RESULTS_LIMIT]:
            if not is_allowed_account(post):
                owner = hashtag_post_owner(post)
                if owner:
                    print(f"  Пропуск: @{owner} не в списке разрешённых")
                else:
                    print("  Пропуск: не удалось определить автора")
                continue

            post_id = str(post.get("id") or post.get("shortCode") or post.get("shortcode") or post.get("url") or "")
            post_url = post.get("url") or ""
            if not post_url and post.get("shortCode"):
                post_url = f"https://www.instagram.com/p/{post['shortCode']}/"
            if not post_id:
                print("  Пост без id/url — пропуск")
                continue

            ig_keys = instagram_extra_duplicate_keys(post)
            dup = find_duplicate(published, post_id=post_id, url=post_url, extra_keys=ig_keys)
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue

            pub_date = instagram_date_from_apify_post(post)
            if is_too_old(pub_date):
                print(f"  Старый пост ({pub_date[:16]}) — пропуск")
                continue

            media_items = instagram_media_from_apify_post(post)
            if not media_items:
                print("  Медиа не найдено — пропуск")
                continue

            raw_caption = clean_text(instagram_caption_from_apify_post(post))
            dup = find_duplicate(published, post_id=post_id, url=post_url, text=raw_caption, media_items=media_items, extra_keys=ig_keys)
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue

            if raw_caption and is_semantic_duplicate(raw_caption, source=f"ig-hashtag-#{hashtag}", post_id=post_id, url=post_url):
                continue

            translated = translate_deepl(raw_caption) if raw_caption else ""
            full_text = ai_check_telegram_layout(make_hashtag_telegram_html(translated, hashtag))

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
            ok = send_to_telegram(media_items, full_text, step4_custom_sig=True)
            if ok:
                mark_published(published, post_id=post_id, url=post_url, text=raw_caption, media_items=media_items, extra_keys=ig_keys)
                save_published(published)
                record_source_news(raw_caption, step=4, source=f"ig-hashtag-#{hashtag}", post_id=post_id, url=post_url)
                new_count += 1

    print(f"\n  Итого опубликовано: {new_count}")
    return published
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, required=True, choices=[1, 2, 3, 4])
    args = parser.parse_args()

    print(f"▶ Шаг {args.step} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    published_state_exists = Path(PUBLISHED_FILE).exists()
    published = load_published()
    print(f"  Уже опубликовано ID в кэше: {len(published)}")
    if REQUIRE_PUBLISHED_CACHE and not published_state_exists:
        print(
            "  published_ids.json не восстановлен — запуск остановлен, "
            "чтобы не опубликовать дубли."
        )
        print("\n✓ Готово.")
        return

    if args.step == 1:
        step1_twitter_keywords(published)
    elif args.step == 2:
        step2_instagram(published)
    elif args.step == 3:
        step3_twitter_accounts(published)
    elif args.step == 4:
        step4_instagram_hashtags(published)

    ensure_state_files(published)
    print("\n✓ Готово.")

if __name__ == "__main__":
    main()
