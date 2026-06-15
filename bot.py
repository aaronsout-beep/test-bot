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
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from format_model.rag_layout import (
    build_layout_prompt,
    load_examples,
    parse_checker_json,
    select_similar_examples,
)
from format_model.hard_cases import save_hard_case
from format_model.candidates import save_candidate, pending_count
from glossary import GLOSSARY, glossary_for_prompt

# ────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
BOT_NAME = os.environ.get("BOT_NAME", "NicoPazBot").strip()
ALERT_CHANNEL_ID = os.environ.get("ALERT_CHANNEL_ID", "").strip()
DEEPL_KEY = os.environ.get("DEEPL_KEY", "").strip()
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
YANDEX_API_KEY   = os.environ.get("YANDEX_API_KEY", "").strip()
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "").strip()
YANDEX_API_URL   = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
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

# Cloudinary — для публичных URL медиафайлов (используется в sendRichMessage)
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY    = os.environ.get("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

# Cross-posting after a successful Telegram post.
VK_ACCESS_TOKEN = os.environ.get("VK_ACCESS_TOKEN", "").strip()
VK_OWNER_ID = os.environ.get("VK_OWNER_ID", "").strip()
VK_DEFAULT_API_VERSION = "5.199"
VK_API_VERSION = os.environ.get("VK_API_VERSION", "").strip() or VK_DEFAULT_API_VERSION
VK_FROM_GROUP = os.environ.get("VK_FROM_GROUP", "1").strip()
VK_CROSSPOST_DELAY_SECONDS = int((os.environ.get("VK_CROSSPOST_DELAY_SECONDS") or "0").strip())
POST_DELAY_SECONDS = int((os.environ.get("POST_DELAY_SECONDS") or "3").strip())

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
STEP3_SCAN_LIMIT = int(
    (
        os.environ.get("STEP3_SCAN_LIMIT")
        or os.environ.get("STEP3_FETCH_LIMIT")
        or str(max(STEP3_LIMIT, 50))
    ).strip()
)
STEP3_NITTER_FALLBACK = os.environ.get("STEP3_NITTER_FALLBACK", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
STEP3_NITTER_STALE_HOURS = float((os.environ.get("STEP3_NITTER_STALE_HOURS") or "12").strip())

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
 
_SIG_HASHTAG  = os.environ.get("SIGNATURE_HASHTAG", "").strip()
_SIG_URL      = os.environ.get("SIGNATURE_URL", "").strip()
_SIG_URL_TEXT = os.environ.get("SIGNATURE_URL_TEXT", "Follow us").strip()
if _SIG_HASHTAG and _SIG_URL:
    SIGNATURE = f'\n\n#{_SIG_HASHTAG} | <a href="{_SIG_URL}">{_SIG_URL_TEXT}</a>'
elif _SIG_HASHTAG:
    SIGNATURE = f'\n\n#{_SIG_HASHTAG}'
else:
    SIGNATURE = '\n\n#KenanYildiz | <a href="https://t.me/+6mqOQu0luT0yNzMy">Follow us</a>'
CROSSPOST_SIGNATURE = f"\n\n#{_SIG_HASHTAG}" if _SIG_HASHTAG else "\n\n#NicoPaz"
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
SEMANTIC_AI_PREFILTER_THRESHOLD = 0.18  # было 0.24 — снижено чтобы цитаты с другими словами проходили на AI
DUPLICATE_TEXT_MIN_WORDS = int((os.environ.get("DUPLICATE_TEXT_MIN_WORDS") or "7").strip())
DUPLICATE_TEXT_MIN_CHARS = int((os.environ.get("DUPLICATE_TEXT_MIN_CHARS") or "45").strip())
SOURCE_DUPLICATE_TEXT_MIN_WORDS = int((os.environ.get("SOURCE_DUPLICATE_TEXT_MIN_WORDS") or "5").strip())
SOURCE_DUPLICATE_TEXT_MIN_CHARS = int((os.environ.get("SOURCE_DUPLICATE_TEXT_MIN_CHARS") or "35").strip())
LAYOUT_AI_MODEL = os.environ.get(
    "LAYOUT_AI_MODEL",
    "gemini-2.5-flash-lite",
).strip()
LAYOUT_AI_MAX_CHARS = int(os.environ.get("LAYOUT_AI_MAX_CHARS", "3200"))
LAYOUT_AI_TIMEOUT = int(os.environ.get("LAYOUT_AI_TIMEOUT", "12"))
STYLE_EDIT_ENABLED = os.environ.get("STYLE_EDIT_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
STYLE_EDIT_MODEL = (os.environ.get("STYLE_EDIT_MODEL") or LAYOUT_AI_MODEL).strip()
STYLE_EDIT_MAX_CHARS = int(os.environ.get("STYLE_EDIT_MAX_CHARS", "1800"))
STYLE_EDIT_TIMEOUT = int(os.environ.get("STYLE_EDIT_TIMEOUT", "18"))
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
                published = expand_published_keys(set(json.load(f)))
                before_source_keys = len(published)
                published.update(source_news_duplicate_keys())
                if len(published) > before_source_keys:
                    print(f"  Content-дедуп из {SOURCE_NEWS_FILE}: +{len(published) - before_source_keys} ключей")
                    save_published(published)
                return published
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


def normalize_duplicate_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[@#][\wА-Яа-яÀ-ÿ_]+", " ", text, flags=re.UNICODE)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w\sА-Яа-яÀ-ÿ-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def duplicate_text_word_count(normalized_text: str) -> int:
    return len(re.findall(r"[\wА-Яа-яÀ-ÿ-]{2,}", normalized_text or "", flags=re.UNICODE))


def stable_duplicate_text_fingerprint(
    text: str,
    min_words: int = DUPLICATE_TEXT_MIN_WORDS,
    min_chars: int = DUPLICATE_TEXT_MIN_CHARS,
) -> str:
    normalized = normalize_duplicate_text(text)
    if not normalized:
        return ""
    if len(normalized) < min_chars and duplicate_text_word_count(normalized) < min_words:
        return ""
    return short_hash(normalized[:700])


def source_fingerprint(source: str) -> str:
    source = html.unescape(str(source or "")).strip().casefold()
    if not source:
        return ""
    if re.search(r"^(?:https?://|www\.)", source) or re.search(
        r"(?:instagram\.com|twitter\.com|x\.com|nitter\.)/",
        source,
    ):
        normalized_url = canonical_url(source)
        source = normalized_url
    else:
        source = source.lstrip("@")
        source = re.sub(r"\s+", " ", source)
        source = re.sub(r"[^\w\sА-Яа-яÀ-ÿ.-]", "", source, flags=re.UNICODE).strip()
    return short_hash(source) if source else ""


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
    """Stable extra keys for Instagram duplicates.
    Использует только надёжные идентификаторы поста:
    shortCode, id, и прямой URL поста на instagram.com.
    НЕ собирает CDN/медиа ссылки — они нестабильны и вызывают ложные дубли.
    """
    keys = set()

    # shortCode — самый надёжный идентификатор Instagram поста
    for field in ("shortCode", "shortcode", "code"):
        shortcode = str(post.get(field) or "").strip().lower()
        if shortcode:
            keys.add(f"ig:{shortcode}")

    for shortcode in instagram_shortcodes_from_post(post):
        keys.add(f"ig:{shortcode}")

    # Только прямой URL поста на instagram.com (не CDN, не медиа)
    for field in ("url", "postUrl", "permalink", "link"):
        url = str(post.get(field) or "").strip()
        if url and "instagram.com/p/" in url:
            normalized = canonical_url(url)
            if normalized:
                keys.add(f"igurl:{normalized}")

    return keys


def duplicate_keys(
    post_id: str = "",
    url: str = "",
    text: str = "",
    source: str = "",
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

    media_key = media_fingerprint(media_items or [])
    if media_key:
        keys.add(f"media:{media_key}")

    stable_text_key = stable_duplicate_text_fingerprint(text)

    # Different media posts can legitimately reuse a short caption.
    # Long exact captions are stable enough to block repost loops.
    text_key = text_fingerprint(text)
    if text_key and (not media_key or stable_text_key):
        keys.add(f"text:{text_key}")

    if stable_text_key:
        keys.add(f"content:{stable_text_key}")

    source_key = source_fingerprint(source)
    source_text_key = stable_duplicate_text_fingerprint(
        text,
        min_words=SOURCE_DUPLICATE_TEXT_MIN_WORDS,
        min_chars=SOURCE_DUPLICATE_TEXT_MIN_CHARS,
    )
    if source_key and source_text_key:
        keys.add(f"source-text:{source_key}:{source_text_key}")

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


def source_news_duplicate_keys() -> set:
    try:
        records = load_source_news_cache()
    except NameError:
        return set()
    except Exception as e:
        print(f"  Не удалось подмешать content-дубли из {SOURCE_NEWS_FILE}: {e}")
        return set()

    keys = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        keys.update(
            duplicate_keys(
                post_id=str(record.get("post_id") or ""),
                url=str(record.get("url") or ""),
                text=str(record.get("text") or ""),
                source=str(record.get("source") or ""),
            )
        )
    return keys


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


def parse_datetime_utc(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    for fmt in (
        "%a %b %d %H:%M:%S %z %Y",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(normalized, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

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
    """
    Возвращает максимум из трёх метрик схожести.

    overlap  — доля общих токенов относительно МЕНЬШЕГО текста.
               Высокий когда короткий текст является подмножеством длинного.
    cosine   — геометрическая метрика, менее чувствительна к разнице размеров.
    contain  — направленная метрика: насколько кандидат из кэша «покрывает»
               новый текст. Используется только когда новый текст длиннее
               кэшированного — защищает от пропуска «сборных» постов.
    """
    left_tokens  = news_tokens(left)
    right_tokens = news_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0

    common = len(left_tokens & right_tokens)
    overlap  = common / min(len(left_tokens), len(right_tokens))
    cosine   = common / ((len(left_tokens) * len(right_tokens)) ** 0.5)
    # contain: насколько left покрывается right (right — из кэша, left — новый)
    contain  = common / len(left_tokens) if left_tokens else 0.0
    return max(overlap, cosine, contain)


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
    """AI-проверка семантических дублей через Gemini (ранее OpenRouter)."""
    if not GEMINI_API_KEY or not candidates:
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
        "Mark duplicate=true when it is the same event/quote/news with mostly the same facts, "
        "even if worded differently or from another source.\n"
        "Pay special attention to quotes: if the NEW item quotes the same person "
        "saying essentially the same thing as a PREVIOUS item — mark as duplicate.\n"
        "Return only valid JSON without markdown: "
        "{\"duplicate\": true|false, \"reason\": \"one short sentence\"}.\n\n"
        f"NEW:\n{text[:1600]}\n\n"
        f"PREVIOUS:\n{json.dumps(previous, ensure_ascii=False)}"
    )

    try:
        r = requests.post(
            f"{GEMINI_BASE_URL}/models/{LAYOUT_AI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 120,
                    "candidateCount": 1,
                    "responseMimeType": "application/json",
                },
            },
            timeout=18,
        )
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"].get("parts", [])
        content = "\n".join(p.get("text", "") for p in parts).strip()
        # Парсим: от первой { до последней }
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            data = json.loads(content[start:end + 1])
        else:
            data = json.loads(content)
        if data.get("duplicate") is True:
            print(f"  AI-дубль по смыслу: {data.get('reason', '')}")
            return True
        return False
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  AI-проверка дублей недоступна: HTTP {status}")
        return False
    except Exception as e:
        print(f"  AI-проверка дублей недоступна: {e}")
        return False


def _looks_like_quote_post(text: str) -> bool:
    """
    True если пост выглядит как цитата конкретного человека.
    Цитата одного спикера в разных источниках — почти всегда дубль.
    Срабатывает на паттерны: 🎙/🗣 + имя + двоеточие, или «Имя о Ком-то:»
    """
    plain = strip_html_tags(html.unescape(text or ""))
    # Эмодзи спикера + текст перед двоеточием
    if re.search(r"[🎙🗣️].{3,60}:", plain):
        return True
    # «Имя Фамилия о Имя:» или «Имя Фамилия:»
    if re.search(r"[A-ZА-ЯЁ][a-zа-яё]{2,}\s+[A-ZА-ЯЁ][a-zа-яё]{2,}.*:", plain):
        return True
    return False


def _quote_speaker(text: str) -> str:
    """
    Извлекает имя спикера из поста-цитаты для сравнения.
    Возвращает нормализованную строку вида «франческо тотти».
    """
    plain = strip_html_tags(html.unescape(text or ""))
    # «🎙 Франческо Тотти о Кенане:» → «франческо тотти»
    m = re.search(r"[🎙🗣️]\s*([A-ZА-ЯЁ][^\n:]{3,50}):", plain)
    if m:
        name_part = m.group(1).strip()
        # Убираем «о Ком-то» в конце
        name_part = re.sub(r"\s+[оo]\s+.+$", "", name_part, flags=re.I)
        return name_part.strip().casefold()
    return ""


def is_semantic_duplicate(text: str, source: str = "", post_id: str = "", url: str = "") -> bool:
    if word_count(text) < 6:
        return False

    cache = load_source_news_cache()
    candidates = top_semantic_candidates(text, cache, url=url)
    if not candidates:
        return False

    best_score, best_record = candidates[0]

    # ── Адаптивный локальный порог ──────────────────────────────────────────
    # Для постов-цитат (🎙 Спикер: «...») порог снижаем:
    # один человек даёт одно интервью — разные источники цитируют одно и то же.
    # Дополнительно проверяем что спикер совпадает — чтобы не блокировать
    # два разных интервью одного человека.
    is_quote = _looks_like_quote_post(text)
    local_threshold = SEMANTIC_LOCAL_DUPLICATE_THRESHOLD  # 0.86 по умолчанию

    if is_quote:
        speaker_new = _quote_speaker(text)
        speaker_old = _quote_speaker(best_record.get("text", ""))
        same_speaker = (
            speaker_new
            and speaker_old
            and (speaker_new in speaker_old or speaker_old in speaker_new)
        )
        if same_speaker:
            # Цитата того же спикера — достаточно 0.45 токен-схожести
            local_threshold = 0.45
        else:
            # Спикер другой или не распознан — стандартный порог
            local_threshold = SEMANTIC_LOCAL_DUPLICATE_THRESHOLD

    if best_score >= local_threshold:
        label = "цитата" if is_quote and local_threshold < SEMANTIC_LOCAL_DUPLICATE_THRESHOLD else "смысловой дубль"
        print(
            f"  {label.capitalize()} по локальному кэшу "
            f"({best_score:.2f} ≥ {local_threshold:.2f}, {best_record.get('source', '')}) — пропуск"
        )
        return True

    # ── AI-проверка для оставшихся кандидатов ───────────────────────────────
    # Для цитат передаём все кандидаты — AI лучше понимает что «та же мысль,
    # другими словами» это дубль.
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
    dt = parse_datetime_utc(date_str)
    if dt:
        return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
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


def strip_extra_hashtags(text: str, signature: str = "") -> str:
    """Удаляет все хэштеги из тела поста, кроме тех, что входят в SIGNATURE.

    Логика:
    - Из signature извлекаем разрешённые хэштеги (например, #DesireDoue).
    - Все прочие #Хэштег в теле поста удаляем.
    - Саму signature не трогаем.
    """
    sig = signature or SIGNATURE
    allowed = set(re.findall(r"#[\wА-Яа-яA-Za-zÀ-ÿ_]+", sig, flags=re.UNICODE))

    # Отрезаем подпись от тела, чтобы не удалить хэштег подписи
    sig_stripped = sig.strip()
    if sig_stripped and text.rstrip().endswith(sig_stripped.rstrip()):
        body = text[: text.rstrip().rfind(sig_stripped.rstrip())]
        tail = sig_stripped
    else:
        body = text
        tail = ""

    def remove_hashtag(match: re.Match) -> str:
        tag = match.group(0)
        return tag if tag in allowed else ""

    body = re.sub(r"(?<!\S)#[\wА-Яа-яA-Za-zÀ-ÿ_]+", remove_hashtag, body, flags=re.UNICODE)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return (body.rstrip() + "\n\n" + tail if tail else body).strip()


def normalize_source_brackets(text: str) -> str:
    def fix(match: re.Match) -> str:
        inner = match.group(1).strip()
        inner = re.sub(r"^[-–—]\s*", "", inner)
        return f"[{inner}]" if inner else "[]"

    return re.sub(r"\[\s*([^\]]+?)\s*\]", fix, text or "")


def strip_noise_only_lines(text: str) -> str:
    lines = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line and re.fullmatch(r"[.\"'“”«»`´·•…]+", line):
            continue
        lines.append(raw_line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"(?:\n\s*){3,}", "\n\n", cleaned)
    return cleaned.strip()


def strip_unwanted_source_tags(text: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text or "", flags=re.I)
    text = re.sub(r"</\s*(?:p|div|li|tr)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*(?:p|div|li|tr|td|span|strong|em|i|u|ul|ol)\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<(?!/?(?:b|blockquote)\s*>)[^>]+>", "", text)
    return text


def clean_text(text: str) -> str:
    text = strip_unwanted_source_tags(text or "")
    text = re.sub(r"https?://nitter\.[^\s]+", "", text)
    text = re.sub(r"https?://t\.co/[^\s]+", "", text)
    text = re.sub(r"https?://twitter\.[^\s]+", "", text)
    text = re.sub(r"https?://x\.[^\s]+", "", text)
    text = re.sub(r"https?://instagram\.[^\s]+", "", text)
    text = re.sub(r"\n[^\n]+\(@[^)]+\)\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("@", "-")
    text = strip_noise_only_lines(text)
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
    # Строки рейтингов/номинаций вида «25/26», «NN/NN» — не спикер
    if re.search(r"\d{1,2}/\d{2}", plain):
        return False
    # Строки с флагами стран — элементы списка, не спикер
    if re.search(r"[\U0001F1E0-\U0001F1FF]", plain):
        return False
    # Строки вида «Имя (Клуб)» — элементы рейтинга/номинации, не спикер
    if re.search(r"\([^)]{2,30}\)\s*$", name):
        return False

    words = re.findall(r"[A-Za-zА-Яа-яЁёÀ-ÿ][\wА-Яа-яЁёÀ-ÿ'-]*", name, flags=re.UNICODE)
    if not 2 <= len(words) <= 5:
        return False
    return sum(1 for word in words[:2] if word[:1].isupper()) >= 2


def render_colon_heading(heading: str) -> str:
    plain = html.unescape(strip_html_tags(heading or "")).strip()
    if re.fullmatch(r"\d{1,2}", plain):
        return f"{html.escape(plain, quote=False)}:"
    if "🗣" in plain or "🎙" in plain or looks_like_speaker_heading(heading):
        speaker_name = speaker_name_from_heading(heading)
        if speaker_name:
            return f"🎙️ <b>{html.escape(speaker_name, quote=False)}</b>:"
    if (
        word_count(plain) <= 6
        and not re.search(r"\d", plain)
        and SECTION_COLON_HEADING_RE.search(plain)
    ):
        return f"<b>{html.escape(heading, quote=False)}</b>:"
    return f"{html.escape(heading, quote=False)}:"


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


def text_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁёÀ-ÿ][\wА-Яа-яЁёÀ-ÿ'-]*", text or "", flags=re.UNICODE)


def leading_football_club_words(text: str, max_words: int = 5) -> int:
    words = text_words(text)
    for count in range(min(max_words, len(words)), 0, -1):
        if is_football_club_name(" ".join(words[:count])):
            return count
    return 0


def starts_with_football_club_name(text: str) -> bool:
    return leading_football_club_words(text) > 0


def trailing_football_club_words(text: str, max_words: int = 5) -> int:
    """Возвращает число слов в конце строки, образующих название клуба.

    Например, для «Бернарду Силва является реальным вариантом для Барселоны»
    хвост — это не клуб, а для «Источники в Барселоне» хвост «Барселоне»
    распознаётся как словоформа Барселоны (Барселона ∈ FOOTBALL_CLUB_KEYS).
    """
    words = text_words(text)
    for count in range(min(max_words, len(words)), 0, -1):
        if is_football_club_name(" ".join(words[-count:])):
            return count
    return 0


def ends_with_football_club_name(text: str) -> bool:
    return trailing_football_club_words(text) > 0


def lowercase_continuation_start(text: str) -> str:
    if not text:
        return text
    if text.lstrip().startswith(("#", "@", "http://", "https://", "<", "«", "\"", "“")):
        return text

    def replace(match: re.Match) -> str:
        prefix, word = match.group(1), match.group(2)
        if word.upper() in CAPS_WORD_EXCEPTIONS or word.isupper():
            return match.group(0)
        return prefix + word[:1].lower() + word[1:]

    return re.sub(
        r"^(\s*[^\wА-Яа-яЁёA-Za-zÀ-ÿ#@<«\"“]*)([А-ЯЁA-Z][а-яёa-z][\wА-Яа-яЁёÀ-ÿ'-]*)",
        replace,
        text,
        count=1,
        flags=re.UNICODE,
    )


def lowercase_word_after_prefix_words(text: str, prefix_word_count: int) -> str:
    matches = list(
        re.finditer(
            r"[A-Za-zА-Яа-яЁёÀ-ÿ][\wА-Яа-яЁёÀ-ÿ'-]*",
            text or "",
            flags=re.UNICODE,
        )
    )
    if len(matches) <= prefix_word_count:
        return text

    match = matches[prefix_word_count]
    word = match.group(0)
    if word.upper() in CAPS_WORD_EXCEPTIONS or word.isupper():
        return text
    lowered = word[:1].lower() + word[1:]
    return text[:match.start()] + lowered + text[match.end():]


def merge_football_club_line(line: str, nxt: str) -> str | None:
    if not line or not nxt:
        return None
    if re.search(r"[.!?…]\s*[»\"]?\s*$", line):
        return None
    if line.startswith(("#", "@")) or nxt.startswith(("#", "@")):
        return None

    # Предлоги, с которых может начинаться продолжение оборванного предложения,
    # если за предлогом идёт название клуба.
    _CLUB_PREPOSITIONS = {
        "в", "для", "из", "к", "на", "о", "об", "от", "перед", "по",
        "при", "про", "с", "у", "за", "до", "из-за", "из-под",
    }

    nxt_words = text_words(nxt)

    # Правило A: следующая строка начинается с «предлог + название клуба».
    # Пример: «Бернарду Силва является «Реальным вариантом»\nДля Барселоны, и...»
    # Это правило проверяем ДО guard looks_like_speaker_heading, потому что
    # «Бернарду Силва является «Реальным вариантом»» может ложно
    # распознаться как заголовок говорящего.
    if nxt_words:
        first_word_lower = nxt_words[0].casefold()
        rest_nxt = nxt.lstrip()
        after_first = re.sub(
            r"^\s*" + re.escape(nxt_words[0]) + r"\s*", "", rest_nxt, count=1, flags=re.I
        )
        if first_word_lower in _CLUB_PREPOSITIONS and starts_with_football_club_name(after_first):
            return f"{line} {lowercase_continuation_start(nxt)}".strip()

    if looks_like_speaker_heading(split_colon_heading(line)[0] or line):
        return None

    current_words = text_words(line)
    if not current_words:
        return None

    joined = f"{line} {nxt}".strip()
    joined_club_words = leading_football_club_words(joined)
    if joined_club_words > len(current_words):
        return lowercase_word_after_prefix_words(joined, joined_club_words)

    if is_football_club_name(line):
        return f"{line} {lowercase_continuation_start(nxt)}".strip()

    # Правило B: line заканчивается названием клуба или его словоформой, а
    # следующая строка не является самостоятельным абзацем.
    # Пример: «Источники в Барселоне\nСообщают, что...»
    #         «...в Манчестер Сити\nНа 50 %, чтобы...»
    # DeepL нередко переводит начало оборванной строки с заглавной буквы,
    # поэтому проверяем не регистр, а признаки нового абзаца.
    if ends_with_football_club_name(line):
        nxt_stripped = nxt.lstrip()
        is_new_paragraph = bool(
            re.match(r"^[#@]", nxt_stripped)
            or re.match(r"^[🚨❗❓🎙🗣📌🔴🟡⚽🏆]", nxt_stripped)
        )
        if not is_new_paragraph:
            return f"{line} {lowercase_continuation_start(nxt)}".strip()

    return None


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


BROKEN_NUMERIC_COLON_RE = re.compile(
    r"(\d{1,2}):\s*\n\s*(\d{1,2})(?!\d)",
    re.MULTILINE,
)
BROKEN_NUMERIC_COLON_HTML_RE = re.compile(
    r"(\d{1,2}):\s*(?:</b>)?\s*\n\s*(?:<b>)?\s*(\d{1,2})(?!\d)",
    re.MULTILINE | re.IGNORECASE,
)
INLINE_LIST_MARKER_RE = re.compile(
    r"(?<=\S)\s+(?=(?:[•·★⭐✅☑️✔]|\[\d+\]|\d+[.)])\s)",
    re.UNICODE,
)
LIST_LINE_START_RE = re.compile(
    r"^(?:[•·✅☑️✔⭐★]|\[\d+\]|\d+[.)])\s",
    re.UNICODE,
)
SECTION_COLON_HEADING_RE = re.compile(
    r"(?:в этом сезоне|итоги|статистика|результаты|возраст|игроки|тренеры|вратари)$",
    re.I,
)


def is_numeric_colon_context(before: str, after: str) -> bool:
    """True, если двоеточие — часть счёта/времени, а не заголовок секции."""
    left = (before or "").strip()
    right = (after or "").strip()
    if not left:
        return False
    if re.fullmatch(r"\d{1,2}", left):
        if not right:
            return True
        first_token = re.match(r"(\d{1,2})", right)
        if first_token:
            return True
    if re.fullmatch(r"\d+", left) and re.match(r"\d+(?:\s|$|[,.])", right):
        return True
    if re.search(r"\d{1,2}$", left) and re.match(r"^\d{1,2}(?:\s|$|[,.])", right):
        return True
    return False


def fix_broken_numeric_colons(text: str) -> str:
    """Склеивает разорванные счёт/время вида «2:\\n1» или «19:\\n30»."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    prev = None
    while prev != text:
        prev = text
        text = BROKEN_NUMERIC_COLON_HTML_RE.sub(r"\1:\2", text)
        text = BROKEN_NUMERIC_COLON_RE.sub(r"\1:\2", text)
    return text


def _is_list_line(line: str) -> bool:
    return bool(LIST_LINE_START_RE.match((line or "").strip()))


def split_inline_lists(text: str) -> str:
    """Разносит соседние пункты списка (•, ★, [20], 1)) по отдельным строкам."""
    lines = (text or "").split("\n")
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        markers = len(re.findall(
            r"(?:^|\s)(?:[•·★⭐✅☑️✔]|\[\d+\]|\d+[.)])\s",
            line,
        ))
        if markers >= 2:
            for part in INLINE_LIST_MARKER_RE.split(line):
                part = part.strip()
                if part:
                    out.append(part)
            continue
        out.append(line)
    return "\n".join(out)


def split_inline_numbered_lists(text: str) -> str:
    """Обратная совместимость."""
    return split_inline_lists(text)


def split_colon_heading(line: str) -> tuple[str, str]:
    if ":" not in line:
        return "", ""

    before, after = line.split(":", 1)
    before = before.strip()
    after = after.strip()

    if not before:
        return "", ""
    if is_numeric_colon_context(before, after):
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
            # Не склеивать соседние пункты списка.
            if _is_list_line(line) or _is_list_line(nxt):
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

            club_merged = merge_football_club_line(line, nxt)
            if club_merged:
                line = club_merged
                i += 1
                continue

            if re.search(r"\d:$", line) and re.match(r"^\d", nxt):
                line = f"{line}{nxt}"
                i += 1
                continue

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
    text = fix_broken_numeric_colons(text)
    text = strip_unwanted_source_tags(text)
    text = strip_intro_labels(text)
    text = strip_noise_only_lines(text)
    text = strip_decorative_pipes(text)
    text = strip_follow_us_tail(text)
    text = strip_extra_hashtags(text)
    text = normalize_source_brackets(text)
    text = join_orphan_lines(text)
    text = fix_broken_numeric_colons(text)
    text = split_inline_lists(text)
    text = merge_country_player_list_lines(text)
    text = normalize_paragraph_breaks(text)
    text = strip_intro_labels(text)
    text = strip_noise_only_lines(text)
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
    else:
        # Логируем ключи поста чтобы понять структуру ответа Apify
        top_keys = list(post.keys())[:20]
        print(f"  Apify media не найдено. Ключи поста: {top_keys}")

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


def translate_yandex(text: str) -> str:
    """
    Перевод через YandexGPT Pro — основной метод.
    При недоступности Yandex API автоматически откатывается на DeepL.

    YandexGPT используется вместо DeepL + edit_style_gemini:
    перевод и стилистическая адаптация выполняются за один вызов.
    """
    if not text:
        return text

    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        print("  YandexGPT недоступен: YANDEX_API_KEY/YANDEX_FOLDER_ID не заданы — fallback на DeepL")
        return translate_deepl(text)

    system_prompt = (
        "Ты профессиональный переводчик футбольных новостей. "
        "Переведи текст на русский язык. Строгие требования:\n"
        "1. Имена игроков — транслитерация принятая в русских СМИ. "
        "Обязательные написания (НЕЛЬЗЯ менять):\n"
        "   Lamine Yamal → Ламин Ямаль (не «Ямал»)\n"
        "   Vinicius / Vinícius → Винисиус (не «Виниций»)\n"
        "   Jude Bellingham → Джуд Беллингем (не «Беллингхэм»)\n"
        "   Marcus Rashford → Маркус Рэшфорд (не «Рашфорд»)\n"
        "   Arda Güler → Арда Гюлер (не «Гулер»)\n"
        "   Kenan Yıldız → Кенан Йылдыз\n"
        "   Kendry Páez → Кендри Паэс\n"
        "   Guillermo Mora / Gilberto Mora → Гильберто Мора\n"
        "   Lo Celso → Ло Чельсо\n"
        "   Héctor Fort → Эктор Форт\n"
        "Для всех остальных имён — транслитерация по испанской/португальской/английской фонетике.\n"
        "КРИТИЧНО: Все имена и фамилии — ТОЛЬКО с заглавной буквы. "
        "Строчные имена НЕДОПУСТИМЫ ни при каких условиях.\n"
        "2. Названия стран и клубов — с заглавной буквы: "
        "España → Испания, France → Франция, Real Madrid → «Реал Мадрид», "
        "Barcelona → «Барселона».\n"
        "3. Первое слово каждого предложения — с заглавной буквы.\n"
        "4. Текст должен звучать живо и естественно по-русски, "
        "как в популярном спортивном Telegram-канале. "
        "Без канцелярита, без дословного калькирования.\n"
        "5. Счёт матча, время, дробные числа — всегда слитно без переноса строки: "
        "3:1, 19:30, 0,25.\n"
        "6. Все эмодзи оставляй на своих местах без изменений.\n"
        "7. Верни только переведённый текст, без пояснений и комментариев."
    )

    try:
        r = requests.post(
            YANDEX_API_URL,
            headers={
                "Authorization": f"Api-Key {YANDEX_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
                "completionOptions": {
                    "stream": False,
                    "temperature": 0.1,
                    "maxTokens": 2000,
                },
                "messages": [
                    {"role": "system", "text": system_prompt},
                    {"role": "user",   "text": text},
                ],
            },
            timeout=30,
        )
        r.raise_for_status()
        result = r.json()["result"]["alternatives"][0]["message"]["text"].strip()
        if not result:
            raise ValueError("Пустой ответ от YandexGPT")
        return result
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  YandexGPT перевод HTTP {status}: {e} — fallback на DeepL")
        return translate_deepl(text)
    except Exception as e:
        print(f"  YandexGPT перевод ошибка: {e} — fallback на DeepL")
        return translate_deepl(text)


def parse_gemini_editor_response(content: str) -> str:
    content = (content or "").strip()
    content = re.sub(r"^```(?:text)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    content = re.sub(
        r"^\s*(?:вот|держи)\s+[^:\n]{0,80}:\s*",
        "",
        content,
        flags=re.I,
    )
    content = re.sub(
        r"^\s*(?:исправленный|отредактированный|готовый)\s+текст\s*:\s*",
        "",
        content,
        flags=re.I,
    )
    return content.strip()


def gemini_text_completion(
    prompt: str,
    max_tokens: int,
    timeout: int,
    model: str | None = None,
) -> str:
    model = (model or LAYOUT_AI_MODEL).strip()
    r = requests.post(
        f"{GEMINI_BASE_URL}/models/{model}:generateContent",
        params={"key": GEMINI_API_KEY},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "candidateCount": 1,
            },
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"].get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def parse_json_response(content: str):
    content = parse_gemini_editor_response(content)
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except Exception:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = content.find(opener)
        end = content.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except Exception:
                pass
    return None


def parse_replacement_ops(content: str) -> list[tuple[str, str]]:
    data = parse_json_response(content)
    if isinstance(data, dict):
        items = (
            data.get("replacements")
            or data.get("operations")
            or data.get("edits")
            or []
        )
    elif isinstance(data, list):
        items = data
    else:
        return []

    ops: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        old = (
            item.get("from")
            or item.get("old")
            or item.get("source")
            or item.get("before")
            or ""
        )
        new = (
            item.get("to")
            or item.get("new")
            or item.get("target")
            or item.get("after")
            or ""
        )
        old = html.unescape(str(old)).replace("\r\n", "\n").replace("\r", "\n").strip()
        new = html.unescape(str(new)).replace("\r\n", "\n").replace("\r", "\n").strip()
        if old and old != new:
            ops.append((old, new))
    return ops


def apply_case_insensitive_literal(text: str, bad: str, good: str) -> str:
    if not text or not bad or bad == good:
        return text
    pattern = re.compile(
        r"(?<![\w-])" + re.escape(bad) + r"(?![\w-])",
        flags=re.I | re.UNICODE,
    )
    return pattern.sub(good, text)


LOCAL_NAME_REPLACEMENTS = (
    ("Kendry Páez", "Кендри Паэс"),
    ("Kendry Paez", "Кендри Паэс"),
    ("кендри паэс", "Кендри Паэс"),
    ("кендри паес", "Кендри Паэс"),
    ("кендри паэз", "Кендри Паэс"),
    ("кендри кавиедес", "Кендри Паэс"),
    ("кендри кавидес", "Кендри Паэс"),
    ("паэс", "Паэс"),
    ("паес", "Паэс"),
    ("паэз", "Паэс"),
)


def normalize_known_player_names(text: str) -> str:
    for bad, good in LOCAL_NAME_REPLACEMENTS:
        text = apply_case_insensitive_literal(text, bad, good)
    return text


def apply_literal_glossary_replacements(text: str) -> str:
    fixed = text or ""
    for entry in sorted(GLOSSARY, key=lambda e: len(e.get("bad", "")), reverse=True):
        fixed = apply_case_insensitive_literal(
            fixed,
            str(entry.get("bad", "")),
            str(entry.get("good", "")),
        )
    return fixed


def capitalize_first_word(value: str) -> str:
    return re.sub(
        r"^([a-zа-яё])",
        lambda m: m.group(1).upper(),
        str(value or ""),
        count=1,
        flags=re.I,
    )


def apply_local_translation_fixes(text: str) -> str:
    text = normalize_known_player_names(text or "")
    if not text:
        return text

    text = re.sub(
        r"(?m)^([^\w\s<@#«“\"'‘(\[]{1,8})(?=[А-ЯЁA-Z])",
        r"\1 ",
        text,
        flags=re.UNICODE,
    )
    text = re.sub(r"([«“])\s+", r"\1", text)
    text = re.sub(r"\s+([»”])", r"\1", text)
    text = re.sub(
        r"(?<=[А-Яа-яЁёA-Za-z0-9»])([:!?])(?=[А-Яа-яЁёA-Za-z«])",
        r"\1 ",
        text,
    )
    text = re.sub(r"([»\"])\s*[-–]\s*(?=[A-Za-zА-Яа-яЁё0-9_@])", r"\1 — ", text)

    def repl_new_ten(match: re.Match) -> str:
        country = capitalize_first_word(match.group("country"))
        return f"{match.group('prefix')}новая «десятка» {country}"

    text = re.sub(
        r"(?P<prefix>\b)"
        r"нов(?:ый|ая|ое|ым|ой)\s+"
        r"[«\"“”]?\s*10\s*[-–—]?\s*(?:й|я|е|ый|ая|ое)\s*[»\"“”]?"
        r"\s+(?P<country>[А-Яа-яЁёA-Za-z][\wА-Яа-яЁё-]*)",
        repl_new_ten,
        text,
        flags=re.I | re.UNICODE,
    )
    text = re.sub(
        r"\b([Вв])\s+возрасте\s+(Кендри\s+Паэс)\s+уже\b",
        lambda m: f"{'В' if m.group(1).isupper() else 'в'} свои годы {m.group(2)} уже",
        text,
    )
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def glossary_bad_regex(bad: str) -> re.Pattern | None:
    words = re.findall(r"[\wА-Яа-яЁёÀ-ÿ'-]+", str(bad or ""), flags=re.UNICODE)
    if not words:
        return None

    parts = [re.escape(word) for word in words]
    last = words[-1]
    if re.search(r"[А-Яа-яЁё]", last):
        parts[-1] = (
            re.escape(last)
            + r"(?:а|у|ом|е|ы|ой|ого|ому|ым|их|ыми|ами|ями)?"
        )
    pattern = r"(?<![\w-])" + r"\s+".join(parts) + r"(?![\w-])"
    return re.compile(pattern, flags=re.I | re.UNICODE)


def matching_glossary_entries(text: str) -> list[dict]:
    entries = []
    for entry in GLOSSARY:
        pattern = glossary_bad_regex(entry.get("bad", ""))
        if pattern and pattern.search(text or ""):
            entries.append(entry)
    return entries


def glossary_hit_count(text: str) -> int:
    return len(matching_glossary_entries(text))


def glossary_entries_for_prompt(entries: list[dict]) -> str:
    lines = []
    for entry in entries:
        bad = entry.get("bad", "")
        good = entry.get("good", "")
        comment = entry.get("comment", "")
        if not bad or not good or bad == good:
            continue
        line = f'  • Неверно: «{bad}» → Верно: «{good}»'
        if comment:
            line += f"  ({comment})"
        lines.append(line)
    return "\n".join(lines)


def cyrillic_token_variants(token: str) -> set[str]:
    token = normalize_news_for_similarity(token)
    if not token or not re.search(r"[а-яё]", token):
        return {token} if token else set()

    variants = {token}
    if token.endswith(("ь", "й")):
        stem = token[:-1]
        variants.update({stem + end for end in ("я", "ю", "ем", "е")})
    elif token.endswith("а"):
        stem = token[:-1]
        variants.update({stem + end for end in ("ы", "е", "у", "ой", "ою")})
    elif token.endswith("я"):
        stem = token[:-1]
        variants.update({stem + end for end in ("и", "е", "ю", "ей", "ею")})
    else:
        variants.update({token + end for end in ("а", "у", "ом", "е", "ы", "ов", "ев")})
    return variants


def glossary_allowed_good_tokens() -> set[str]:
    allowed: set[str] = set()
    for entry in GLOSSARY:
        for token in news_tokens(entry.get("good", "")):
            allowed.update(cyrillic_token_variants(token))
            allowed.add(token)
    return allowed


def numeric_facts(text: str) -> set[str]:
    plain = html.unescape(strip_html_tags(text or ""))
    return set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?:\s*[%№])?(?!\w)", plain))


def proper_name_phrases(text: str) -> set[str]:
    plain = html.unescape(strip_html_tags(text or ""))
    word = r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁёÀ-ÿ'’.-]{1,}"
    phrases = set()
    for match in re.finditer(rf"\b{word}(?:\s+{word}){{1,4}}\b", plain):
        normalized = normalize_news_for_similarity(match.group(0))
        if normalized:
            phrases.add(normalized)
    return phrases


def glossary_name_mentions(text: str) -> set[str]:
    normalized = normalize_news_for_similarity(text)
    mentions = set()
    for entry in GLOSSARY:
        good = str(entry.get("good", ""))
        comment = str(entry.get("comment", "")).casefold()
        looks_like_name = (
            word_count(good) >= 2
            or any(marker in comment for marker in ("футболист", "игрок", "форвард", "защитник", "фамилия"))
        )
        if not looks_like_name:
            continue
        good_norm = normalize_news_for_similarity(good)
        if good_norm and good_norm in normalized:
            mentions.add(good_norm)
    return mentions


def replacement_ops_preview(ops: list[tuple[str, str]]) -> str:
    return json.dumps(
        [{"from": old, "to": new} for old, new in ops[:8]],
        ensure_ascii=False,
    )


def glossary_edit_preserved(original: str, candidate: str) -> bool:
    """Проверяет что Gemini только заменил словарные термины, ничего не добавив.

    Логика строгая: допускается только уменьшение или нейтральное изменение
    количества токенов (замена bad→good), но не добавление новых токенов
    которых не было в оригинале И не входят в good-формы словаря.
    """
    original_plain = html.unescape(strip_html_tags(original or "")).strip()
    candidate_plain = html.unescape(strip_html_tags(candidate or "")).strip()
    if not original_plain or not candidate_plain:
        return False

    # Ссылки в ответе — признак галлюцинации
    if re.search(r"\[[^\]\n]{1,80}\]\(\s*https?://", candidate_plain, flags=re.I):
        return False
    original_urls = set(re.findall(r"https?://\S+", original_plain, flags=re.I))
    candidate_urls = set(re.findall(r"https?://\S+", candidate_plain, flags=re.I))
    if candidate_urls - original_urls:
        return False

    # Кандидат не должен быть существенно длиннее оригинала
    # Допуск: +10% или +40 символов — только для склонений
    max_growth = max(40, int(len(original_plain) * 0.10))
    if len(candidate_plain) > len(original_plain) + max_growth:
        return False
    if len(candidate_plain) < max(1, int(len(original_plain) * 0.80)):
        return False

    # Глоссарная правка не имеет права менять структуру строк.
    original_lines = [l for l in original_plain.splitlines() if l.strip()]
    candidate_lines = [l for l in candidate_plain.splitlines() if l.strip()]
    if len(candidate_lines) != len(original_lines):
        return False

    # Новые токены в кандидате — только если они являются good-формами из словаря
    original_tokens = news_tokens(original_plain)
    candidate_tokens = news_tokens(candidate_plain)
    if not original_tokens:
        return candidate_tokens == original_tokens
    if not candidate_tokens:
        return False

    added_tokens = candidate_tokens - original_tokens
    if added_tokens:
        # Разрешаем только токены из good-форм словаря, включая простые падежные формы.
        forbidden = added_tokens - glossary_allowed_good_tokens()
        if forbidden:
            return False

    return True


def edit_gemini(text: str) -> str:
    """Глоссарный этап: ТОЛЬКО точечные замены плохих форм на хорошие.

    Модель возвращает JSON с операциями from→to. Код применяет только операции,
    где from буквально есть в исходном тексте, а итог проходит строгий валидатор.
    """
    if not text:
        return text

    text = apply_literal_glossary_replacements(normalize_known_player_names(text))
    entries = matching_glossary_entries(text)
    if not entries or not GEMINI_API_KEY:
        return text

    glossary_block = glossary_entries_for_prompt(entries)
    prompt = (
        "Ты НЕ редактор и НЕ переводчик. Ты только проверяешь глоссарий.\n"
        "Нельзя возвращать готовый пост, переписывать текст, добавлять факты, вопросы, "
        "ответы, цитаты, имена, клубы или пояснения.\n"
        "Можно предложить только точечные замены фрагментов, которые БУКВАЛЬНО есть "
        "в исходном тексте, по парам «неверно → верно» ниже. Падеж/род/число можно "
        "подстроить только внутри заменяемого имени или термина.\n\n"
        "Верни строго JSON без Markdown:\n"
        "{\"replacements\":[{\"from\":\"точный фрагмент из исходного текста\",\"to\":\"исправленный фрагмент\"}]}\n"
        "Если замен нет, верни {\"replacements\":[]}.\n\n"
        "ДОСТУПНЫЙ СЛОВАРЬ ДЛЯ ЭТОГО ТЕКСТА:\n"
        f"{glossary_block}\n\n"
        "ИСХОДНЫЙ ТЕКСТ:\n"
        f"{text}"
    )
    try:
        content = gemini_text_completion(
            prompt,
            max_tokens=700,
            timeout=STYLE_EDIT_TIMEOUT,
            model=LAYOUT_AI_MODEL,
        )
        ops = parse_replacement_ops(content)
        if not ops:
            return text

        candidate = text
        applied: list[tuple[str, str]] = []
        for old, new in ops[:8]:
            if old not in candidate:
                continue
            if not any((glossary_bad_regex(e.get("bad", "")) or re.compile(r"$^")).search(old) for e in entries):
                continue
            next_candidate = candidate.replace(old, new, 1)
            if glossary_edit_preserved(text, next_candidate):
                candidate = next_candidate
                applied.append((old, new))

        if applied:
            return candidate

        print("  Gemini глоссарий отклонён: нет безопасных точечных замен")
        save_hard_case(
            stage="gemini_glossary",
            reason="unsafe_or_missing_replacement_ops",
            original=text,
            candidate=replacement_ops_preview(ops),
        )
        return text
    except Exception as e:
        print(f"  Gemini глоссарий ошибка: {e}")
        return text


def style_edit_preserved(original: str, candidate: str) -> bool:
    original_plain = html.unescape(strip_html_tags(original or "")).strip()
    candidate_plain = html.unescape(strip_html_tags(candidate or "")).strip()
    if not original_plain or not candidate_plain:
        return False

    if re.search(r"\[[^\]\n]{1,80}\]\(\s*https?://", candidate_plain, flags=re.I):
        return False
    if set(re.findall(r"https?://\S+", original_plain, flags=re.I)) != set(
        re.findall(r"https?://\S+", candidate_plain, flags=re.I)
    ):
        return False
    if numeric_facts(original_plain) != numeric_facts(candidate_plain):
        return False

    original_lines = [l for l in original_plain.splitlines() if l.strip()]
    candidate_lines = [l for l in candidate_plain.splitlines() if l.strip()]
    if len(candidate_lines) > len(original_lines) + 2:
        return False
    if len(candidate_lines) < max(1, len(original_lines) - 2):
        return False

    length_ratio = len(candidate_plain) / max(1, len(original_plain))
    if length_ratio < 0.70 or length_ratio > 1.25:
        return False

    original_names = proper_name_phrases(original_plain)
    candidate_names = proper_name_phrases(candidate_plain)
    if candidate_names - original_names:
        return False

    original_glossary_names = glossary_name_mentions(original_plain)
    candidate_glossary_names = glossary_name_mentions(candidate_plain)
    if not original_glossary_names <= candidate_glossary_names:
        return False
    if candidate_glossary_names - original_glossary_names:
        return False

    original_tokens = news_tokens(original_plain)
    candidate_tokens = news_tokens(candidate_plain)
    if not original_tokens or not candidate_tokens:
        return True

    common = len(original_tokens & candidate_tokens)
    min_size = min(len(original_tokens), len(candidate_tokens))
    required_overlap = 0.45 if min_size <= 8 else 0.58
    original_overlap = common / max(1, len(original_tokens))
    candidate_overlap = common / max(1, len(candidate_tokens))
    return original_overlap >= required_overlap and candidate_overlap >= required_overlap


def edit_style_gemini(text: str) -> str:
    """Литературная редактура русского текста без новых смыслов.

    Как и глоссарий, принимает только список локальных replacements. Модель не
    получает права вернуть новый пост целиком.
    """
    if (
        not text
        or not STYLE_EDIT_ENABLED
        or not GEMINI_API_KEY
        or not STYLE_EDIT_MODEL
        or len(text) > STYLE_EDIT_MAX_CHARS
    ):
        return text

    prompt = (
        "Ты аккуратный русскоязычный редактор футбольного Telegram-канала.\n"
        "Нужно отредактировать текст, чтобы он звучал естественно по-русски, но НЕЛЬЗЯ менять смысл.\n\n"
        
        "Строгие запреты:\n"
        "1) Не добавляй факты, вопросы, ответы, цитаты, имена, клубы, источники и числа.\n"
        "2) Не заменяй имена и термины на другие, но ОБЯЗАТЕЛЬНО исправляй их склонение по правилам русского языка.\n"
        "3) Не переписывай пост целиком и не меняй порядок абзацев.\n"
        "4) Не используй HTML, Markdown, ссылки или пояснения.\n\n"
        
        "Обязательно исправляй:\n"
        "- Заглавную букву в начале предложений\n"
        "- Склонение имён после предлогов (например: «у», «с», «для»)\n"
        "- Очевидные грамматические ошибки, даже если они связаны с именами\n\n"
        
        "Разрешены только короткие локальные замены неестественных фрагментов: "
        "падежи, согласование, порядок слов, пробелы, пунктуация, капитализация.\n"
        "Можно исправлять фрагменты внутри предложения, если это необходимо для грамотности.\n\n"
        
        "Примеры допустимого типа правки: «новый 10-й» → «новая «десятка»», "
        "«В возрасте Кендри Паэс уже» → «В свои годы Кендри Паэс уже», "
        "«у Кендри Паэс» → «У Кендри Паэса».\n\n"
        
        "Верни строго JSON без Markdown:\n"
        "{\"replacements\":[{\"from\":\"точный фрагмент из исходного текста\",\"to\":\"естественный вариант\"}]}\n"
        "Если текст уже нормальный или для улучшения нужен новый смысл, верни {\"replacements\":[]}.\n\n"
        
        "ТЕКСТ:\n"
        f"{text}"
    )
    try:
        content = gemini_text_completion(
            prompt,
            max_tokens=900,
            timeout=STYLE_EDIT_TIMEOUT,
            model=STYLE_EDIT_MODEL,
        )
        ops = parse_replacement_ops(content)
        if not ops:
            return text

        candidate = text
        applied: list[tuple[str, str]] = []
        replaced_chars = 0
        max_replaced_chars = max(140, int(len(text) * 0.45))

        for old, new in ops[:6]:
            if old not in candidate:
                continue
            if "\n\n\n" in new or re.search(r"https?://", new, flags=re.I):
                continue
            if len(new) > len(old) + max(45, int(len(old) * 0.35)):
                continue
            if len(new) < max(1, int(len(old) * 0.55)):
                continue
            if replaced_chars + len(old) > max_replaced_chars:
                continue

            next_candidate = candidate.replace(old, new, 1)
            next_candidate = apply_local_translation_fixes(next_candidate)
            if style_edit_preserved(text, next_candidate):
                candidate = next_candidate
                applied.append((old, new))
                replaced_chars += len(old)

        if applied and candidate != text:
            print("  Gemini редактура текста поправлена")
            return candidate

        if ops:
            print("  Gemini редактура текста отклонена: нет безопасных точечных замен")
            save_hard_case(
                stage="gemini_style",
                reason="unsafe_or_missing_replacement_ops",
                original=text,
                candidate=replacement_ops_preview(ops),
            )
        return text
    except Exception as e:
        print(f"  Gemini редактура текста ошибка: {e}")
        return text


def edit_translation_text(text: str) -> str:
    """
    Постобработка после перевода DeepL.
    Цепочка: локальные замены → edit_gemini (глоссарий) → edit_style_gemini (стиль) → локальные замены.
    """
    text = apply_local_translation_fixes(text)
    glossary_checked = edit_gemini(text)
    style_checked = edit_style_gemini(glossary_checked)
    return apply_local_translation_fixes(style_checked)


# Обратная совместимость: старое имя функции.
edit_openrouter = edit_translation_text


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
    text = strip_unwanted_source_tags(text)
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
    cleaned = strip_noise_only_lines(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


INTRO_LABEL_RE = r"(?:последние\s+новости|последняя\s+новость|главная\s+новость|новости|новость|официально|подтверждено|обновлени[ея]|апдейт|видео|срочно|эксклюзив|breaking(?:\s+news)?|just\s+in|official|latest\s+news|update|video)"
INTRO_LABEL_PREFIX_RE = r"^[ \t]*(?:[^\w<#@\n]+[ \t]*)*"
CAPS_WORD_RE = re.compile(r"(?<![#/@\w-])([А-ЯЁ]{1,}|[A-Z]{3,})(?![\w-])", re.UNICODE)
HTML_LAYOUT_TAG_RE = re.compile(r"(</?(?:b|blockquote)>)", re.I)
CAPS_WORD_EXCEPTIONS = {
    "VAR", "FIFA", "UEFA", "UCL", "PSG", "MLS", "NBA",
    "РФ", "США", "ОАЭ", "АПЛ", "ЛЧ", "ЛЕ", "РПЛ", "ЦСКА", "ПСЖ",
}


def strip_intro_labels(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))

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
        linebreak = "\n" if rest.endswith("\n") else ""
        if linebreak:
            rest = rest[:-1]
        if not rest.strip():
            return linebreak
        if not marker:
            return (match.group("indent") or "") + rest.lstrip(" \t") + linebreak
        if match.group("open") or match.group("close"):
            prefix = f"{match.group('indent') or ''}<b>{marker}</b>"
        else:
            prefix = f"{match.group('indent') or ''}{marker}"
        separator = "" if not rest or rest[0] in " \t\r\n" else " "
        return prefix + separator + rest.lstrip(" \t") + linebreak

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
    text = strip_unwanted_source_tags(text)
    text = strip_noise_only_lines(text)
    text = strip_intro_labels(text)
    # Удаляем лишние хэштеги из тела — работает всегда, независимо от Gemini/RAG.
    # Применяем к plain-версии чтобы не задеть href-атрибуты тега <a> в SIGNATURE.
    text_plain = strip_html_tags(text)
    if text_plain != strip_extra_hashtags(text_plain):
        text = strip_extra_hashtags(text)
    parts = HTML_LAYOUT_TAG_RE.split(text)
    normalized = [
        part if HTML_LAYOUT_TAG_RE.fullmatch(part or "") else normalize_sentence_capitalization(part)
        for part in parts
    ]
    return strip_noise_only_lines(strip_intro_labels("".join(normalized)))


def plain_layout_text(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"</(?:p|blockquote)\s*>", "\n", text, flags=re.I)
    text = strip_html_tags(text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_preserved(original: str, candidate: str) -> bool:
    original_plain = plain_layout_text(original)
    candidate_plain = plain_layout_text(candidate)
    if not original_plain or not candidate_plain:
        return False

    length_ratio = len(candidate_plain) / max(1, len(original_plain))
    if length_ratio < 0.72 or length_ratio > 1.20:
        return False

    original_tokens = news_tokens(original_plain)
    candidate_tokens = news_tokens(candidate_plain)
    if not original_tokens:
        return True
    if not candidate_tokens:
        return False

    common = len(original_tokens & candidate_tokens)
    original_overlap = common / max(1, len(original_tokens))
    candidate_overlap = common / max(1, len(candidate_tokens))
    return original_overlap >= 0.72 and candidate_overlap >= 0.86


def protected_formatting_preserved(original: str, candidate: str) -> bool:
    original_blockquotes = len(re.findall(r"<blockquote\b", original or "", flags=re.I))
    candidate_blockquotes = len(re.findall(r"<blockquote\b", candidate or "", flags=re.I))
    if candidate_blockquotes < original_blockquotes:
        return False

    original_headings = []
    for match in re.finditer(r"<b>(.*?)</b>:\s*\n", original or "", flags=re.I | re.S):
        heading = plain_layout_text(match.group(1))
        if not heading:
            continue
        # Не защищать разорванное время/счёт и произвольные не-спикерские заголовки.
        if re.search(r"\d{1,2}$", heading.strip()):
            continue
        if not looks_like_speaker_heading(heading):
            continue
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


def _apply_safe_plain_repairs(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines_input = text.splitlines()
    merged_lines: list[str] = []
    i = 0
    while i < len(lines_input):
        line = lines_input[i].strip()
        # Склеиваем строку «• текст» со следующей строкой «✅» если она одиночная галочка
        if (
            line
            and re.match(r"^[•·]\s+\S", line)
            and i + 1 < len(lines_input)
            and lines_input[i + 1].strip() in {"✅", "☑️", "✔"}
        ):
            merged_lines.append(line + " " + lines_input[i + 1].strip())
            i += 2
            continue
        merged_lines.append(lines_input[i])
        i += 1

    repaired: list[str] = []
    for raw_line in merged_lines:
        line = raw_line.strip()
        if not line:
            if repaired and repaired[-1] != "":
                repaired.append("")
            continue
        line = re.sub(r"^[.·]\s+(?=[^\w\s]|$)", "", line, flags=re.UNICODE)
        parts = []
        if "✅" in line:
            positions = [m.start() for m in re.finditer("✅", line)]
            if len(positions) > 1 or (len(positions) == 1 and positions[0] > 0):
                if positions and positions[0] > 0:
                    head = line[: positions[0]].strip()
                    if head:
                        parts.append(head)
                for idx, pos in enumerate(positions):
                    end = positions[idx + 1] if idx + 1 < len(positions) else len(line)
                    seg = line[pos:end].strip()
                    if seg:
                        parts.append(seg)
        if not parts:
            parts = [line]
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if looks_like_country_player_item(part):
                left, _, right = re.split(r"(—|–)", part, maxsplit=1)
                player = extract_quote_text(right)
                if player:
                    repaired.append(f"{left.strip()} — {player}")
                    continue
            repaired.append(part)

    return ensure_hashtag_spacing(normalize_angle_quotes("\n".join(repaired).strip()))


def safe_plain_layout_fixes(text: str) -> str:
    """Безопасные правки plain-текста — без HTML-тегов."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = fix_broken_numeric_colons(text)
    text = strip_html_tags(text)
    text = preprocess_post_text(text)
    return _apply_safe_plain_repairs(text)


def _layout_structurally_broken(text: str) -> bool:
    """True, если в тексте остались типичные структурные баги верстки."""
    return bool(
        re.search(
            r"\d{1,2}:\s*(?:</b>)?\s*\n\s*(?:<b>)?\s*\d",
            text or "",
            flags=re.I,
        )
        or re.search(r"(?<=\S)\s+[•·★⭐✅☑️✔]\s", text or "")
        or re.search(r"(?<=\S)\s+(?:\[\d+\]|\d+[.)])\s", text or "")
    )


def safe_html_layout_fixes(text: str) -> str:
    """Безопасные правки с сохранением разрешённых HTML-тегов."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = fix_broken_numeric_colons(text)
    if re.search(r"<(?:b|blockquote)\b", text, flags=re.I):
        text = sanitize_telegram_layout_html(text)
        if not telegram_html_tags_balanced(text):
            return safe_plain_layout_fixes(text)
        text = normalize_posting_text(text)
        text = fix_broken_numeric_colons(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return ensure_hashtag_spacing(text.strip())
    return safe_plain_layout_fixes(text)


def safe_layout_fixes(text: str) -> str:
    """Обратная совместимость: plain-правки без HTML."""
    return safe_plain_layout_fixes(text)


def local_layout_repair(text: str, *, conservative: bool = False) -> str:
    if conservative:
        return safe_layout_fixes(text)

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
                if heading and (
                    looks_like_speaker_heading(heading)
                    or "🎙" in heading
                    or "🗣" in heading
                ):
                    rendered_heading = render_colon_heading(heading)
                    repaired.append(rendered_heading)
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
    # Сохраняем оригинал до любых изменений — используется как fallback
    # если и local_layout_repair, и все AI-проверки провалились.
    original_text = normalize_posting_text(full_text)
    original_text = safe_plain_layout_fixes(original_text)

    locally_repaired = local_layout_repair(original_text)
    locally_repaired = normalize_posting_text(locally_repaired)
    pre_rag_text = locally_repaired
    rag_candidate = rag_check_telegram_layout(locally_repaired)
    rag_changed = rag_candidate != pre_rag_text
    if rag_changed:
        locally_repaired = normalize_posting_text(rag_candidate)

    if not GEMINI_API_KEY or not LAYOUT_AI_MODEL:
        print("  Gemini-проверка верстки пропущена: GEMINI_API_KEY не задан")
        return safe_html_layout_fixes(locally_repaired)
    if len(locally_repaired) > LAYOUT_AI_MAX_CHARS:
        print("  Gemini-проверка верстки пропущена: текст слишком длинный")
        return safe_html_layout_fixes(locally_repaired)
    if rag_changed and not _layout_structurally_broken(locally_repaired):
        print("  Gemini-проверка верстки пропущена: RAG уже поправил верстку")
        return safe_html_layout_fixes(locally_repaired)

    prompt = (
        "Поправь форматирование для телеграм канала. Нельзя добавлять новые слова, "
        "новые факты, ответы, вопросы или цитаты. Можно менять только форматирование "
        "абзацев, строк и расположения эмоджи. Если для исправления нужно придумать "
        "содержимое — верни исходный текст. Выводи только "
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
        "10) Хэштеги: в теле поста все хэштеги кроме того, что в подписи (#DesireDoue), удалять. "
        "Хэштег подписи не трогать.\n"
        "11) Удалять вводные метки в начале строки: «подтверждено:», «❗️подтверждено:», "
        "«‼️последние новости:», «последние новости:» и аналогичные — оставлять только сам текст новости.\n"
        "12) Счёт матча или время вида «X:\nY» (цифра, двоеточие, перенос строки, цифра) — "
        "всегда склеивать в «X:Y» без переноса. Пример: «2:\n1» → «2:1», «19:\n30» → «19:30».\n"
        "Разрешенные HTML-теги: <b>, </b>, <blockquote>, </blockquote>. "
        "Не используй Markdown, ссылки и другие теги.\n\n"
        f"ТЕКСТ:\n{locally_repaired}"
    )

    try:
        content = gemini_layout_text(prompt)
        candidate_raw = parse_layout_ai_response(content)
        candidate = sanitize_telegram_layout_html(candidate_raw)
        candidate = normalize_posting_text(candidate)

        if not candidate:
            return safe_html_layout_fixes(locally_repaired)
        if not telegram_html_tags_balanced(candidate):
            print("  Gemini-верстка отклонена: HTML-теги не сбалансированы")
            save_hard_case(
                stage="gemini_layout",
                reason="unbalanced_html_tags",
                original=locally_repaired,
                candidate=candidate,
            )
            return safe_html_layout_fixes(locally_repaired)
        if not protected_formatting_preserved(locally_repaired, candidate):
            print("  Gemini-верстка отклонена: удалены обязательные quote/переносы")
            save_hard_case(
                stage="gemini_layout",
                reason="protected_formatting_lost",
                original=locally_repaired,
                candidate=candidate,
            )
            # Если local_layout_repair добавил лишние blockquote (ложный спикер),
            # а Gemini их правильно убрала — не откатываемся к locally_repaired,
            # а откатываемся к оригиналу до local_layout_repair.
            if "<blockquote>" in locally_repaired and "<blockquote>" not in original_text:
                print("  Откат к оригиналу (local_layout_repair добавил лишние blockquote)")
                return safe_html_layout_fixes(original_text)
            return safe_html_layout_fixes(locally_repaired)
        if not content_preserved(locally_repaired, candidate):
            print("  Gemini-верстка отклонена: текст изменился слишком сильно")
            save_hard_case(
                stage="gemini_layout",
                reason="content_changed_too_much",
                original=locally_repaired,
                candidate=candidate,
            )
            return safe_html_layout_fixes(locally_repaired)
        # Double-check: if local repair has interview answers (blockquotes) but Gemini removed them — revert.
        if "<blockquote>" in locally_repaired and "<blockquote>" not in candidate:
            print("  Gemini-верстка отклонена: потеряно blockquote после спикера")
            save_hard_case(
                stage="gemini_layout",
                reason="lost_blockquote_after_speaker",
                original=locally_repaired,
                candidate=candidate,
            )
            return safe_html_layout_fixes(locally_repaired)
        if candidate != locally_repaired:
            print("  Gemini-верстка поправлена")
            # Сохраняем пару bad→good в кандидаты для ревью.
            # locally_repaired — текст до Gemini (bad), candidate — после (good).
            # Тег определяем автоматически по структуре текста.
            _save_layout_candidate(bad=locally_repaired, good=candidate, source="gemini")
        else:
            print("  Gemini-верстка проверена")
        return safe_html_layout_fixes(candidate)
    except Exception as e:
        print(f"  Gemini-проверка верстки недоступна: {e}")
        # При полном провале Gemini возвращаем locally_repaired.
        # Если local_layout_repair добавил лишние blockquote (ложный спикер),
        # откатываемся к оригиналу до local_layout_repair.
        if "<blockquote>" in locally_repaired and "<blockquote>" not in original_text:
            print("  Откат к оригиналу (local_layout_repair добавил лишние blockquote)")
            return safe_html_layout_fixes(original_text)
        return safe_html_layout_fixes(locally_repaired)


def rag_check_telegram_layout(text: str) -> str:
    if not FORMAT_RAG_ENABLED:
        return text
    if not GEMINI_API_KEY:
        print("  RAG-проверка верстки пропущена: GEMINI_API_KEY не задан")
        return text

    examples = load_examples()
    if not examples:
        print("  RAG-проверка верстки пропущена: нет примеров в examples.jsonl")
        return text

    selected = select_similar_examples(text, examples)
    prompt = build_layout_prompt(text, selected)

    # Вызов через Gemini с responseMimeType для гарантированного JSON
    result = None
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
                    "responseMimeType": "application/json",
                },
            },
            timeout=LAYOUT_AI_TIMEOUT,
        )
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"].get("parts", [])
        content = "\n".join(p.get("text", "") for p in parts).strip()
        result = parse_checker_json(content)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  RAG-проверка верстки недоступна: HTTP {status} от Gemini")
        return text
    except Exception as e:
        print(f"  RAG-проверка верстки недоступна: {e}")
        return text

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
    fixed_text = normalize_posting_text(fixed_text)
    fixed_text = safe_html_layout_fixes(fixed_text)
    if not fixed_text:
        save_hard_case(
            stage="rag_layout",
            reason="empty_fixed_text_after_sanitize",
            original=text,
            candidate=fixed_raw,
        )
        return text

    def _log_rag_rejection(reason_label: str, reason_key: str) -> None:
        """Логирует отклонение RAG с diff'ом и сохраняет в hard_cases."""
        print(f"  RAG-верстка отклонена: {reason_label}")
        _log_layout_diff(original=text, candidate=fixed_text, label="RAG")
        save_hard_case(
            stage="rag_layout",
            reason=reason_key,
            original=text,
            candidate=fixed_text,
        )

    if not telegram_html_tags_balanced(fixed_text):
        _log_rag_rejection("HTML-теги не сбалансированы", "unbalanced_html_tags")
        return text
    if not protected_formatting_preserved(text, fixed_text):
        _log_rag_rejection("удалены обязательные quote/переносы", "protected_formatting_lost")
        return text
    if not content_preserved(text, fixed_text):
        _log_rag_rejection("текст изменился слишком сильно", "content_changed_too_much")
        return text

    if fixed_text != text:
        print(f"  RAG-верстка поправлена (примеров: {len(selected)})")
        _save_layout_candidate(bad=text, good=fixed_text, source="rag")
    else:
        print(f"  RAG-верстка проверена (примеров: {len(selected)})")
    return fixed_text


def _log_layout_diff(original: str, candidate: str, label: str = "RAG") -> None:
    """
    Выводит построчный diff между оригиналом и кандидатом прямо в лог.
    Показывает только изменившиеся строки — не более DIFF_MAX_LINES строк контекста.
    Формат: «- » удалено / «+ » добавлено — легко читается в GitHub Actions.
    """
    DIFF_MAX_LINES = 30  # защита от огромных диффов в логе
    orig_lines = original.splitlines()
    cand_lines = candidate.splitlines()

    # Простой построчный diff без внешних библиотек
    changes: list[str] = []
    max_len = max(len(orig_lines), len(cand_lines))
    for i in range(max_len):
        o = orig_lines[i] if i < len(orig_lines) else None
        c = cand_lines[i] if i < len(cand_lines) else None
        if o == c:
            continue
        if o is not None and c is None:
            changes.append(f"  - {o!r}")
        elif o is None and c is not None:
            changes.append(f"  + {c!r}")
        else:
            changes.append(f"  - {o!r}")
            changes.append(f"  + {c!r}")

    if not changes:
        return

    print(f"  [{label}-diff] изменения ({min(len(changes), DIFF_MAX_LINES)} из {len(changes)} строк):")
    for line in changes[:DIFF_MAX_LINES]:
        print(f"  {line}")
    if len(changes) > DIFF_MAX_LINES:
        print(f"  ... и ещё {len(changes) - DIFF_MAX_LINES} строк")


def _auto_tag_layout(text: str) -> str:
    """
    Определяет тег для кандидата по структурным признакам текста.
    Используется при автосохранении — чтобы кандидаты сразу были размечены.
    """
    import re
    # interview_qa проверяем первым — ❓ более специфичен, чем 🎙
    if re.search(r"❓", text):
        return "interview_qa"
    if re.search(r"[🎙🗣]", text):
        return "speaker_quote"
    if re.search(r"\d:\s*\n\s*\d", text):
        return "broken_score"
    if re.search(r"(?:^|\s)\[\d+\]", text, re.M):
        return "numbered_list"
    if re.search(r"[✅☑️•·]\s", text) or re.search(r"^\d+[.)]\s", text, re.M):
        return "stats_list"
    if re.search(r"\d:\s*\n\s*\d", text):
        return "linebreakes"
    if re.search(r"<b>.*?</b>", text, re.S) and re.search(r"\n{3,}", text):
        return "extralinebreakes"
    if re.search(r"[🚨❗]{2,}", text):
        return "extraemoji"
    if re.search(r"#\w+.*#\w+", text):
        return "extrahashtags"
    if re.search(r"\[[\w\s@.-]{2,30}\]", text):
        return "source_brackets"
    return "linebreakes"


def _save_layout_candidate(bad: str, good: str, source: str = "gemini") -> None:
    """
    Сохраняет успешное исправление верстки в candidates.jsonl для ревью.

    Не пишет напрямую в examples.jsonl — сначала нужен ручной approve
    через review_candidates.py. Это защищает выборку от ошибок модели.
    """
    tag = _auto_tag_layout(bad)
    added = save_candidate(bad=bad, good=good, source=source, tag=tag)
    if added:
        pending = pending_count()
        print(f"  [candidates] сохранён кандидат (тег: {tag}, в очереди: {pending})")
        if pending >= 20:
            print("  [candidates] ⚠️  накопилось 20+ кандидатов — запустите review_candidates.py")


def _save_rag_example(bad: str, good: str, tag: str = "auto") -> None:
    """
    Устаревшая функция — теперь делегирует в _save_layout_candidate.
    Оставлена для обратной совместимости если где-то вызывается напрямую.
    """
    _save_layout_candidate(bad=bad, good=good, source="rag")


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

def _send_telegram_alert(text: str):
    """Отправляет служебное сообщение в ALERT_CHANNEL_ID (или CHANNEL_ID как fallback).

    Формат: «BOT_NAME: описание проблемы + SIGNATURE».
    Используется только для критических ошибок, требующих ручного вмешательства.
    """
    target = ALERT_CHANNEL_ID or CHANNEL_ID
    if not target:
        return
    # Извлекаем plain-text подпись из SIGNATURE (убираем HTML-теги для служебного канала)
    sig_plain = re.sub(r"<[^>]+>", "", SIGNATURE).strip()
    message = f"{BOT_NAME}: {text}\n{sig_plain}" if sig_plain else f"{BOT_NAME}: {text}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": target, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print(f"  _send_telegram_alert ошибка: {e}")


def alert(description: str):
    """Сокращённый alias для _send_telegram_alert. Использовать везде в коде."""
    print(f"  [ALERT] {description}")
    _send_telegram_alert(description)

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
    """
    Подготовка текста для кросс-поста (ВК, MAX).
    Удаляет Telegram-подпись целиком и гарантированно добавляет
    только SIGNATURE_HASHTAG в конец один раз.
    """
    text = full_text or ""

    # 1) Убираем полную телеграм-подпись, если она есть в конце текста
    if SIGNATURE and text.rstrip().endswith(SIGNATURE.strip()):
        text = text[: text.rstrip().rfind(SIGNATURE.strip())].rstrip()

    # 2) Запасная замена, если подпись приехала в слегка другом виде
    if _SIG_HASHTAG:
        text = re.sub(
            r"\n?\n?#" + re.escape(_SIG_HASHTAG) + r"\s*\|\s*.*$",
            "",
            text,
            flags=re.I | re.S,
        )
        text = re.sub(
            r"\n?\n?#" + re.escape(_SIG_HASHTAG) + r"\s*$",
            "",
            text,
            flags=re.I | re.S,
        )

    # 3) Чистим хвосты и лишние пробелы
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # 4) Гарантированно добавляем только один хэштег в конец
    signature_tag = f"#{_SIG_HASHTAG}" if _SIG_HASHTAG else "#NicoPaz"
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]

    while lines and lines[-1].strip() == signature_tag:
        lines.pop()

    if lines:
        lines.append("")
    lines.append(signature_tag)

    return "\n".join(lines).strip()
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


def _vk_group_id() -> str:
    """Возвращает group_id (без минуса) если VK_OWNER_ID — группа, иначе пустую строку."""
    oid = VK_OWNER_ID.strip()
    if oid.startswith("-"):
        return oid.lstrip("-")
    return ""


def _vk_upload_photo(path: Path) -> str | None:
    """Загружает фото в VK и возвращает строку вложения 'photo{owner_id}_{photo_id}' или None.
    
    Пробует сначала с group_id (user token), при ошибке 27 (group token) — без group_id.
    """
    group_id = _vk_group_id()

    def _try_upload(use_group_id: bool) -> str | None:
        try:
            # 1) Получаем адрес сервера для загрузки на стену
            params1 = {"access_token": VK_ACCESS_TOKEN, "v": VK_API_VERSION}
            if use_group_id and group_id:
                params1["group_id"] = group_id
            r = requests.post(
                "https://api.vk.com/method/photos.getWallUploadServer",
                data=params1, timeout=15,
            ).json()
            if "error" in r:
                err = r["error"]
                if err.get("error_code") == 27 and use_group_id:
                    print("  VK: group token — пробуем загрузку без group_id")
                    return _try_upload(use_group_id=False)
                print(f"  VK getWallUploadServer ошибка: {err}")
                return None
            upload_url = r["response"]["upload_url"]

            # 2) Загружаем файл
            with open(path, "rb") as f:
                up = requests.post(upload_url, files={"photo": f}, timeout=60).json()

            # 3) Сохраняем фото
            params3 = {
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
                "photo": up.get("photo", ""),
                "server": up.get("server", ""),
                "hash": up.get("hash", ""),
            }
            if use_group_id and group_id:
                params3["group_id"] = group_id
            save_r = requests.post(
                "https://api.vk.com/method/photos.saveWallPhoto",
                data=params3, timeout=15,
            ).json()
            if "error" in save_r:
                print(f"  VK saveWallPhoto ошибка: {save_r['error']}")
                return None
            photo = save_r["response"][0]
            return f"photo{photo['owner_id']}_{photo['id']}"
        except Exception as e:
            print(f"  VK upload photo ошибка: {e}")
            return None

    return _try_upload(use_group_id=bool(group_id))


def _vk_upload_video(path: Path) -> str | None:
    """Загружает видео в VK и возвращает строку вложения 'video{owner_id}_{video_id}' или None."""
    group_id = _vk_group_id()

    def _try_upload(use_group_id: bool) -> str | None:
        try:
            params = {
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
                "wallpost": 0,
                "no_comments": 1,
            }
            if use_group_id and group_id:
                params["group_id"] = group_id
            save_r = requests.post(
                "https://api.vk.com/method/video.save",
                data=params, timeout=15,
            ).json()
            if "error" in save_r:
                err = save_r["error"]
                if err.get("error_code") == 27 and use_group_id:
                    print("  VK: group token — пробуем загрузку видео без group_id")
                    return _try_upload(use_group_id=False)
                print(f"  VK video.save ошибка: {err}")
                return None
            resp = save_r["response"]
            upload_url = resp["upload_url"]
            owner_id = resp["owner_id"]
            video_id = resp["video_id"]

            with open(path, "rb") as f:
                up_r = requests.post(upload_url, files={"video_file": f}, timeout=180)
            if up_r.status_code >= 400:
                print(f"  VK video upload HTTP {up_r.status_code}")
                return None
            return f"video{owner_id}_{video_id}"
        except Exception as e:
            print(f"  VK upload video ошибка: {e}")
            return None

    return _try_upload(use_group_id=bool(group_id))


def post_to_vk(full_text: str, downloaded: list | None = None) -> bool:
    """Публикует пост в VK.

    downloaded — список dict с ключами 'path' (Path) и 'type' ('photo'/'video'),
    уже скачанных для Telegram. Если передан, медиафайлы загружаются в VK и
    прикрепляются к посту.
    """
    if not VK_ACCESS_TOKEN or not VK_OWNER_ID or not VK_API_VERSION:
        print(" VK пропущен: не заданы VK_ACCESS_TOKEN / VK_OWNER_ID / VK_API_VERSION")
        return False

    text = plain_text_for_crosspost(full_text)
    if not text:
        return False

    # Загружаем медиафайлы в VK
    attachments = []
    dl_list = downloaded or []
    print(f"  VK: получено {len(dl_list)} медиафайлов для загрузки")
    for item in dl_list:
        path = item.get("path")
        media_type = item.get("type", "photo")
        exists = Path(path).exists() if path else False
        print(f"  VK: файл {path} | exists={exists} | type={media_type}")
        if not path or not exists:
            continue
        if media_type == "video":
            attach = _vk_upload_video(Path(path))
        else:
            attach = _vk_upload_photo(Path(path))
        if attach:
            attachments.append(attach)
            print(f"  VK вложение загружено: {attach}")
        else:
            print(f"  VK не удалось загрузить: {path}")

    post_data = {
        "access_token": VK_ACCESS_TOKEN,
        "v": VK_API_VERSION,
        "owner_id": VK_OWNER_ID,
        "from_group": VK_FROM_GROUP,
        "message": text,
    }
    if attachments:
        post_data["attachments"] = ",".join(attachments)

    try:
        r = requests.post(
            "https://api.vk.com/method/wall.post",
            data=post_data,
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


def text_contains_signature_hashtag(text: str) -> bool:
    """
    Проверяет наличие #SIGNATURE_HASHTAG в тексте поста.
    Если SIGNATURE_HASHTAG не задан — фильтр отключён.
    """
    if not _SIG_HASHTAG:
        return True

    plain = strip_html_tags(html.unescape(text or ""))
    return bool(re.search(r"(?<!\w)#" + re.escape(_SIG_HASHTAG) + r"(?!\w)", plain, flags=re.I))


def crosspost_after_telegram(full_text: str, downloaded: list | None = None):
    """Кросспостинг в ВК и MAX после успешной публикации в Telegram.

    downloaded — список dict с ключами 'path' и 'type', уже скачанных медиафайлов.
    Передаётся в post_to_vk для прикрепления фото/видео к посту ВК.
    """
    vk_enabled = bool(VK_ACCESS_TOKEN and VK_OWNER_ID)
    max_enabled = bool(MAX_TOKEN and (MAX_CHAT_ID or MAX_USER_ID))

    if not vk_enabled and not max_enabled:
        return

    crosspost_text = plain_text_for_crosspost(full_text)

    if not text_contains_signature_hashtag(crosspost_text):
        tag = f"#{_SIG_HASHTAG}" if _SIG_HASHTAG else "SIGNATURE_HASHTAG"
        print(f"  Кросспостинг пропущен: {tag} не найден в тексте поста.")
        return

    print("  Кросспостинг после Telegram...")
    if vk_enabled:
        media_count = len([d for d in (downloaded or []) if d.get("path") and Path(d["path"]).exists()])
        print(f"  ВК: медиафайлов для загрузки: {media_count} из {len(downloaded or [])}")
        if VK_CROSSPOST_DELAY_SECONDS:
            print(f"  Задержка перед ВК: {VK_CROSSPOST_DELAY_SECONDS} сек...")
            time.sleep(VK_CROSSPOST_DELAY_SECONDS)
        post_to_vk(crosspost_text, downloaded=downloaded)
    if max_enabled:
        post_to_max(crosspost_text)


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


VIDEO_SIZE_LIMIT = 40 * 1024 * 1024  # 40 MB — Telegram bot API limit


def compress_video(path: Path) -> Path:
    """Сжимает видео если оно больше VIDEO_SIZE_LIMIT.
    Возвращает путь к сжатому файлу (или исходный если ffmpeg недоступен / файл уже маленький).
    """
    try:
        size = path.stat().st_size
    except Exception:
        return path

    if size <= VIDEO_SIZE_LIMIT:
        return path

    import shutil as _shutil
    if not _shutil.which("ffmpeg"):
        print(f"  ffmpeg не найден — видео {size // (1024*1024)} MB не сжато, пропуск")
        return path

    out_path = path.with_suffix(".compressed.mp4")
    size_mb = size / (1024 * 1024)
    print(f"  Видео {size_mb:.1f} MB > 40 MB — сжимаем...")

    # Целевой битрейт: вписать в 38 MB за длину видео
    # Сначала узнаём длительность через ffprobe
    duration = None
    try:
        import subprocess
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(probe.stdout.strip())
    except Exception:
        pass

    try:
        import subprocess
        if duration and duration > 0:
            # Целевой размер 38 MB в битах, минус ~128kbps аудио
            target_bits = 38 * 1024 * 1024 * 8
            audio_bits = 128 * 1024 * duration
            video_bits = max(target_bits - audio_bits, 200 * 1024 * duration)
            video_bitrate = int(video_bits / duration)
            cmd = [
                "ffmpeg", "-y", "-i", str(path),
                "-c:v", "libx264", "-b:v", str(video_bitrate),
                "-maxrate", str(int(video_bitrate * 1.5)),
                "-bufsize", str(video_bitrate * 2),
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",
                str(out_path),
            ]
        else:
            # Без длительности — просто понижаем разрешение и качество
            cmd = [
                "ffmpeg", "-y", "-i", str(path),
                "-c:v", "libx264", "-crf", "28", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",
                str(out_path),
            ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and out_path.exists():
            new_size = out_path.stat().st_size
            if new_size <= VIDEO_SIZE_LIMIT:
                path.unlink(missing_ok=True)
                print(f"  Видео сжато: {new_size // (1024*1024)} MB")
                return out_path
            else:
                print(f"  Сжатие не помогло ({new_size // (1024*1024)} MB) — пропускаем")
                out_path.unlink(missing_ok=True)
                return path
        else:
            print(f"  ffmpeg ошибка (код {result.returncode})")
            out_path.unlink(missing_ok=True)
            return path
    except Exception as e:
        print(f"  Ошибка сжатия видео: {e}")
        out_path.unlink(missing_ok=True)
        return path


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




# ── Вспомогательные функции для Rich API ─────────────────────────────────────

def _upload_file_id(item: dict) -> str | None:
    """
    Загружает файл в Telegram через sendPhoto/sendVideo в приватный чат бота
    (chat_id = bot user id) только для получения file_id — без публикации в канал.

    ВАЖНО: этот метод НЕ используется в send_rich_post. Оставлен как запасной
    вариант для других нужд. send_rich_post грузит файлы напрямую через multipart.
    """
    path = item["path"]
    media_type = item["type"]
    method = "sendVideo" if media_type == "video" else "sendPhoto"
    field  = "video"    if media_type == "video" else "photo"
    # Получаем id самого бота для отправки в личный чат (не в канал)
    try:
        me = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10
        ).json()
        bot_id = me.get("result", {}).get("id")
    except Exception:
        bot_id = None
    if not bot_id:
        print(f"  _upload_file_id: не удалось получить id бота")
        return None
    try:
        with open(path, "rb") as f:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                data={"chat_id": bot_id, "disable_notification": True},
                files={field: f},
                timeout=90,
            ).json()
        if not res.get("ok"):
            print(f"  _upload_file_id: ошибка для {path.name}: {res.get('description')}")
            return None
        msg = res["result"]
        if media_type == "video":
            return msg["video"]["file_id"]
        return msg["photo"][-1]["file_id"]
    except Exception as e:
        print(f"  _upload_file_id: исключение {path.name}: {e}")
        return None


def _html_to_rich_paragraphs(html_text: str) -> list:
    """
    Конвертирует HTML-текст в список RichBlockParagraph с inline-entities.
    Каждый абзац (\n\n) — отдельный блок.
    """
    import html as _html
    blocks = []
    tag_map = {
        "b": "bold", "strong": "bold",
        "i": "italic", "em": "italic",
        "u": "underline",
        "s": "strikethrough", "del": "strikethrough",
        "code": "code", "pre": "pre",
    }
    for para in re.split(r"\n\n+", (html_text or "").strip()):
        para = para.strip()
        if not para:
            continue
        entities, text_buf, stack = [], [], []
        src = para
        i = 0
        while i < len(src):
            if src[i] != "<":
                if src[i] == "&":
                    end = src.find(";", i)
                    if end != -1:
                        text_buf.append(_html.unescape(src[i:end+1]))
                        i = end + 1
                        continue
                text_buf.append(src[i]); i += 1; continue
            end = src.find(">", i)
            if end == -1:
                text_buf.append(src[i]); i += 1; continue
            tag_raw = src[i+1:end]; i = end + 1
            if tag_raw.startswith("/"):
                tag_name = tag_raw[1:].strip().lower()
                for j in range(len(stack)-1, -1, -1):
                    if stack[j][0] == tag_name:
                        s_off, _, etype, extra = stack.pop(j)
                        ln = len("".join(text_buf)) - s_off
                        if ln > 0:
                            ent = {"type": etype, "offset": s_off, "length": ln}
                            if extra: ent.update(extra)
                            entities.append(ent)
                        break
            else:
                parts2 = tag_raw.split(None, 1)
                tn = parts2[0].lower()
                attrs = parts2[1] if len(parts2) > 1 else ""
                if tn == "a":
                    m = re.search(r'href=["\'"]([^"\'">]+)["\'"]?', attrs)
                    stack.append((len("".join(text_buf)), tn, "text_link",
                                  {"url": m.group(1)} if m else {}))
                elif tn in tag_map:
                    stack.append((len("".join(text_buf)), tn, tag_map[tn], {}))
        plain = "".join(text_buf)
        blk = {"type": "paragraph", "text": {"text": plain}}
        if entities:
            blk["text"]["entities"] = entities
        blocks.append(blk)
    return blocks


def _detect_table_block(lines: list) -> tuple:
    """
    Определяет подходит ли список для RichBlockTable.
      "ranking" — строки начинаются с числа (ТОП-листы, рейтинги)
      "value"   — строки заканчиваются числом + единица (стоимости, статистика)
      ""        — обычный список
    Порог: ≥ 3 строк и ≥ 75% соответствуют паттерну.
    """
    if len(lines) < 3:
        return False, ""
    ranking_re = re.compile(r"^\s*\d{1,3}[\s.)\-]")
    value_re   = re.compile(
        r".{4,}\s+(\d[\d\s,.]*)"
        r"\s*(млн|тыс|mil|mln|евро|euro|€|\$|%|к|k|г\.|лет)\b",
        re.IGNORECASE | re.UNICODE,
    )
    n = len(lines)
    if sum(1 for l in lines if ranking_re.match(l)) / n >= 0.75:
        return True, "ranking"
    if sum(1 for l in lines if value_re.search(l)) / n >= 0.75:
        return True, "value"
    return False, ""


def _lines_to_rich_table(lines: list, table_type: str) -> dict:
    """Строит RichBlockTable: ranking → «# / Игрок», value → «Игрок / Стоимость»."""
    def cell(t): return {"type": "paragraph", "text": {"text": t}}

    if table_type == "ranking":
        row_re = re.compile(r"^(\d[\d\s]{0,5})\s+(.+)$")
        header = ["#", "Игрок"]
        rows = []
        for l in lines:
            m = row_re.match(l.strip())
            rows.append([m.group(1).strip(), m.group(2).strip()] if m else ["", l.strip()])
    else:
        val_re = re.compile(
            r"^(.*?)\s+(\d[\d\s,.]*\s*"
            r"(?:млн|тыс|mil|mln|евро|euro|€|\$|%|к|k)\b.*)$",
            re.IGNORECASE | re.UNICODE,
        )
        header = ["Игрок", "Стоимость"]
        rows = []
        for l in lines:
            m = val_re.match(l.strip())
            rows.append([m.group(1).strip(), m.group(2).strip()] if m else [l.strip(), ""])

    table_rows = [{"type": "row", "is_header": True, "cells": [cell(h) for h in header]}]
    for r in rows:
        table_rows.append({"type": "row", "cells": [cell(c) for c in r]})
    return {"type": "table", "rows": table_rows}


def _html_to_rich_text_blocks(html_text: str) -> list:
    """
    Конвертирует HTML-текст в Rich-блоки:
      - рейтинговый список   → RichBlockTable (ranking)
      - список с ценами      → RichBlockTable (value)
      - маркированный список → RichBlockList
      - обычный абзац        → RichBlockParagraph
    """
    blocks = []
    list_marker_re = re.compile(r"^[\-•·–]\s+|^\d{1,3}[\s.)\-]")

    for para in re.split(r"\n\n+", (html_text or "").strip()):
        para = para.strip()
        if not para:
            continue

        lines = [l.strip() for l in para.splitlines() if l.strip()]
        is_list = (
            len(lines) >= 2
            and sum(1 for l in lines if list_marker_re.match(l)) >= len(lines) * 0.6
        )

        if is_list:
            clean = [re.sub(r"^[\-•·–]\s+", "", l) for l in lines]
            is_table, ttype = _detect_table_block(clean)
            if is_table:
                # RichBlockTable для рейтингов и стоимостей
                blocks.append(_lines_to_rich_table(clean, ttype))
            else:
                # RichBlockList для обычных маркированных списков
                items = []
                for line in clean:
                    items.append({
                        "type": "list_item",
                        "content": [{"type": "paragraph", "text": {"text": line}}],
                    })
                blocks.append({"type": "list", "style": "unordered", "items": items})
            continue

        # Прозаический абзац
        blocks.extend(_html_to_rich_paragraphs(para))

    return blocks


def _upload_file_id_silent(item: dict) -> str | None:
    """
    Загружает файл в ALERT_CHANNEL_ID (служебный канал) для получения file_id.
    Сообщение остаётся в служебном канале — в основной канал ничего не попадает.
    Если ALERT_CHANNEL_ID не задан — падает с ошибкой (нужно настроить).
    Возвращает file_id или None при ошибке.
    """
    if not ALERT_CHANNEL_ID:
        print("  _upload_file_id_silent: ALERT_CHANNEL_ID не задан — невозможно получить file_id")
        return None
    path = item["path"]
    media_type = item["type"]
    method = "sendVideo" if media_type == "video" else "sendPhoto"
    field  = "video"    if media_type == "video" else "photo"
    try:
        with open(path, "rb") as f:
            res = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                data={"chat_id": ALERT_CHANNEL_ID, "disable_notification": "true"},
                files={field: f},
                timeout=90,
            ).json()
        if not res.get("ok"):
            print(f"  _upload_file_id_silent: ошибка загрузки {path.name}: {res.get('description')}")
            return None
        msg = res["result"]
        if media_type == "video":
            fid = msg.get("video", {}).get("file_id")
        else:
            photos = msg.get("photo", [])
            fid = photos[-1]["file_id"] if photos else None

        # Сразу удаляем сообщение из канала — если ALERT_CHANNEL_ID совпадает
        # с основным каналом, подписчики иначе видят одиночные фото без текста.
        msg_id = msg.get("message_id")
        if msg_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                    json={"chat_id": ALERT_CHANNEL_ID, "message_id": msg_id},
                    timeout=10,
                )
            except Exception:
                pass  # Удаление — best effort, не критично

        return fid
    except Exception as e:
        print(f"  _upload_file_id_silent: исключение {path.name}: {e}")
        return None


def telegram_html_to_rich_html(text: str) -> str:
    """
    Конвертирует Telegram HTML (с \\n\\n) в валидный Rich Message HTML.

    Rich Message не понимает \\n как перенос — нужны блочные теги.
    """
    text = (text or "").strip()
    if not text:
        return ""

    # Сначала заменяем <br> на \n для единообразия
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)

    # Разбиваем на параграфы по \n\n
    paragraphs = re.split(r"\n\n+", text)
    parts = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Одиночный \n внутри абзаца — заменяем на пробел (Rich не поддерживает inline \n)
        para = re.sub(r"\n+", " ", para)
        # Проверяем — это blockquote?
        if para.startswith("<blockquote") or para.startswith("&laquo;"):
            parts.append(para)  # blockquote оставляем как есть
        else:
            parts.append(f"<p>{para}</p>")

    return "\n".join(parts)


def _sig_escaped(sig_plain: str) -> str:
    """HTML-escape для подписи в промпте."""
    return html.escape(sig_plain, quote=False)


def build_rich_layout(text: str) -> str:
    """
    AI Layout Engine: Gemini форматирует текст специально под Rich Message HTML.

    Возвращает Rich HTML с <p>, <blockquote>, <b>Спикер:</b> и SIGNATURE.
    При ошибке — возвращает telegram_html_to_rich_html(text) как fallback.
    """
    if not GEMINI_API_KEY or not text or not text.strip():
        sig_html = SIGNATURE.strip().lstrip("\n")
        return telegram_html_to_rich_html(text) + f"\n<p>{sig_html}</p>"

    # sig_html — готовый HTML подписи (с <a href="...">), для вставки в Rich HTML
    sig_html = SIGNATURE.strip().lstrip("\n")
    # sig_plain — без тегов, только для отображения в промпте как пример
    sig_plain = re.sub(r"<[^>]+>", "", sig_html).strip()

    prompt = f"""Ты форматируешь текст для Telegram Rich Message (Bot API 10.1+).
Rich Message поддерживает: <p>, <blockquote>, <b>, <i>, <u>, <s>, <a href="...">, <tg-spoiler>.
Переносы строк \\n НЕ работают — только блочные теги.

ПРАВИЛА:
1. Каждый смысловой абзац — отдельный <p>...</p>
2. Цитата спикера — <blockquote>«текст цитаты»</blockquote>
3. Имя спикера/источника — <p><b>Имя Спикера</b></p> перед blockquote
4. Не добавляй и не убирай смысловой текст
5. В конце добавь ТОЧНО эту строку подписи без изменений: <p>{sig_html}</p>

Верни ТОЛЬКО Rich HTML без пояснений.

Текст:
{text[:LAYOUT_AI_MAX_CHARS]}"""

    try:
        res = requests.post(
            f"{GEMINI_BASE_URL}/models/{LAYOUT_AI_MODEL}:generateContent"
            f"?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2000}},
            timeout=LAYOUT_AI_TIMEOUT,
        )
        data = res.json()
        rich_html = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
        # Убираем markdown-обёртку если модель всё равно добавила
        rich_html = re.sub(r"^```(?:html)?\n?", "", rich_html)
        rich_html = re.sub(r"\n?```$", "", rich_html).strip()

        if rich_html and "<p>" in rich_html:
            # Гарантируем что подпись с HTML-ссылкой на месте (модель могла упростить)
            if _SIG_URL and _SIG_URL not in rich_html:
                rich_html += f"\n<p>{sig_html}</p>"
            return rich_html
    except Exception as e:
        print(f"  build_rich_layout ошибка: {e}")

    # Fallback: детерминистическая конвертация + подпись с HTML-ссылкой
    return telegram_html_to_rich_html(text) + f"\n<p>{sig_html}</p>"


def _build_rich_message_html(full_text: str, photo_file_ids: list, video_file_ids: list) -> str:
    """
    Собирает HTML-строку для поля rich_message.html согласно Bot API 10.1.

    Медиа вставляется через тег <tg-collage> (2+ фото), <img> (1 фото),
    <video> (видео). Все медиа-блоки — отдельными блоками, не внутри <p>.
    full_text — уже готовый Rich HTML из build_rich_layout() (с <p>, <blockquote>
    и SIGNATURE). Передаётся как есть, без дополнительной обработки.

    ВАЖНО: Media blocks support only HTTP and HTTPS URLs — поэтому сюда
    передаются НЕ file_id, а публичные HTTPS-URL (после загрузки на хостинг)
    или временные https://api.telegram.org/file/bot{TOKEN}/{path} URL.
    """
    parts = []

    # Фото: 1 фото → <img>, 2+ фото → <tg-collage> — медиа идёт ПЕРВЫМ
    if len(photo_file_ids) == 1:
        parts.append(f'<img src="{photo_file_ids[0]}"/>')
    elif len(photo_file_ids) > 1:
        imgs = "".join(f'<img src="{fid}"/>' for fid in photo_file_ids)
        parts.append(f"<tg-collage>{imgs}</tg-collage>")

    # Видео — каждое отдельным блоком, тоже перед текстом
    for fid in video_file_ids:
        parts.append(f'<video src="{fid}"></video>')

    # Текст поста — после медиа (уже Rich HTML из build_rich_layout)
    if full_text and full_text.strip():
        parts.append(full_text.strip())

    return "\n".join(parts)


def _upload_to_cloudinary(item: dict) -> str | None:
    """
    Загружает локальный файл на Cloudinary и возвращает публичный HTTPS-URL.

    Требует переменных окружения:
      CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

    Cloudinary бесплатный план: 25 GB хранилища, 25 GB трафика/мес.
    Загруженные файлы остаются на Cloudinary — можно периодически чистить
    через консоль или API (/delete_resources) по тегу "nicoPazBot".

    Возвращает secure_url или None при ошибке.
    """
    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        print("  Cloudinary: переменные окружения не заданы — загрузка невозможна")
        return None

    import hashlib
    import hmac
    import time as _time

    path: Path = item["path"]
    media_type: str = item["type"]

    resource_type = "video" if media_type == "video" else "image"
    timestamp = int(_time.time())
    public_id = f"nicoPazBot/{path.stem}"
    tags = "nicoPazBot"

    # Подпись: sha1(public_id=...&tags=...&timestamp=...{api_secret})
    sign_str = f"public_id={public_id}&tags={tags}&timestamp={timestamp}{CLOUDINARY_API_SECRET}"
    signature = hashlib.sha1(sign_str.encode()).hexdigest()

    upload_url = (
        f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}"
        f"/{resource_type}/upload"
    )
    try:
        with open(path, "rb") as f:
            res = requests.post(
                upload_url,
                data={
                    "api_key": CLOUDINARY_API_KEY,
                    "timestamp": timestamp,
                    "public_id": public_id,
                    "tags": tags,
                    "signature": signature,
                },
                files={"file": f},
                timeout=120,
            )
        res.raise_for_status()
        data = res.json()
        url = data.get("secure_url") or data.get("url")
        if url:
            print(f"  Cloudinary: загружено → {url}")
            return url
        print(f"  Cloudinary: нет URL в ответе: {data}")
        return None
    except Exception as e:
        print(f"  Cloudinary исключение ({path.name}): {e}")
        return None


def _get_public_url(item: dict) -> str | None:
    """
    Возвращает публичный HTTPS-URL для медиафайла.

    Стратегия (в порядке приоритета):
      1. Cloudinary — если заданы CLOUDINARY_* переменные.
      2. Telegram getFile — fallback (временный URL ~1 час, не всегда
         принимается sendRichMessage).
    """
    # 1. Cloudinary (рекомендуется)
    if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
        return _upload_to_cloudinary(item)

    # 2. Telegram fallback
    fid = _upload_file_id_silent(item)
    if not fid:
        return None
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": fid},
            timeout=30,
        ).json()
        if not res.get("ok"):
            print(f"  getFile ошибка: {res.get('description')}")
            return None
        file_path = res["result"].get("file_path", "")
        if not file_path:
            print("  getFile: пустой file_path")
            return None
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        print(f"  Telegram fallback URL: {url[:80]}")
        return url
    except Exception as e:
        print(f"  _get_public_url исключение: {e}")
        return None


def send_rich_post(downloaded: list, full_text: str) -> bool:
    """
    Публикует пост через sendRichMessage (Bot API 10.1+).

    Стратегия получения медиа-URL:
      1. Загружаем файл в ALERT_CHANNEL_ID (sendPhoto/sendVideo,
         disable_notification=True) → получаем file_id.
      2. Через getFile получаем временный HTTPS-URL файла
         (https://api.telegram.org/file/bot{TOKEN}/{path}).
      3. Передаём этот URL в тег <img src="..."> / <video src="...">
         внутри html-поля InputRichMessage.

    Telegram скачивает файл по URL в момент обработки sendRichMessage,
    поэтому URL должен быть доступен только в момент отправки запроса.
    После успешного ответа от API исходные файлы можно удалять.

    Примечание: поле rich_message принимает {"html": "..."} или
    {"markdown": "..."} — НЕ {"blocks": [...]}. Это задокументировано
    в Bot API 10.1 в разделе InputRichMessage.
    """
    photos = [item for item in downloaded if item["type"] == "photo"]
    videos = [item for item in downloaded if item["type"] == "video"]

    # ── Получаем публичные URL для всех медиафайлов ───────────────────────────
    photo_urls = []
    for item in photos[:TELEGRAM_MEDIA_GROUP_LIMIT]:
        url = _get_public_url(item)
        if url:
            photo_urls.append(url)

    video_urls = []
    for item in videos[:TELEGRAM_MEDIA_GROUP_LIMIT]:
        url = _get_public_url(item)
        if url:
            video_urls.append(url)

    if not photo_urls and not video_urls:
        print("  send_rich_post: не удалось получить URL ни для одного медиафайла")
        return False

    # ── Строим HTML для rich_message ──────────────────────────────────────────
    html_body = _build_rich_message_html(full_text, photo_urls, video_urls)

    if not html_body.strip():
        print("  send_rich_post: пустой html_body — отмена")
        return False

    input_rich_message = {"html": html_body}

    try:
        res = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendRichMessage",
            json={"chat_id": CHANNEL_ID, "rich_message": input_rich_message},
            timeout=60,
        ).json()
        if not res.get("ok"):
            print(f"  sendRichMessage ошибка: {res.get('description')}")
            preview = html_body[:300].replace("\n", "\\n")
            print(f"  html_body preview: {preview}")
        return res.get("ok", False)
    except Exception as e:
        print(f"  send_rich_post исключение: {e}")
        return False

def send_to_telegram(
    media_items: list,
    full_text: str,
    step4_custom_sig: bool = False,
    content_prefix: str = "",
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

    # Сжимаем видео больше 40 MB
    for item in downloaded:
        if item["type"] == "video":
            item["path"] = compress_video(item["path"])

    # Пропускаем видео которые после сжатия всё ещё > 40 MB
    oversized = [
        item for item in downloaded
        if item["type"] == "video" and item["path"].stat().st_size > VIDEO_SIZE_LIMIT
    ]
    if oversized:
        for item in oversized:
            print(f"  Видео {item['path'].name} слишком большое после сжатия — пропуск")
            item["path"].unlink(missing_ok=True)
        downloaded = [item for item in downloaded if item not in oversized]
    if not downloaded:
        print("  Все медиа недоступны после фильтрации — пост не отправляем")
        shutil.rmtree(MEDIA_DIR, ignore_errors=True)
        return None

    # Для шага 4 layout-check уже выполнен снаружи и подпись встроена в full_text
    if step4_custom_sig:
        final_text = full_text
    else:
        final_text = ai_check_telegram_layout(full_text)

    prefix = (content_prefix or "").strip()
    if prefix:
        prefix_html = html.escape(prefix.replace("@", "-"), quote=False)
        if final_text:
            final_text = f"<b>{prefix_html}</b>\n{final_text}"
        else:
            final_text = f"<b>{prefix_html}</b>"

    # sendRichMessage: 1 фото → RichBlockPhoto, 2+ → RichBlockCollage
    # Видео и смешанные медиа-посты тоже идут через Rich API.
    # Текст (переведённый + SIGNATURE) включается в тот же пост блоками.
    has_only_videos = all(item["type"] == "video" for item in downloaded)
    caption, overflow = split_caption(final_text, True)

    # Видео-посты без фото с коротким текстом → старый sendVideo (caption в одном посте)
    # Всё остальное → sendRichMessage
    use_rich = not (has_only_videos and not overflow)

    if use_rich:
        # build_rich_layout форматирует текст под Rich HTML и добавляет SIGNATURE
        rich_html = build_rich_layout(final_text)
        sent_ok = send_rich_post(downloaded, rich_html)
    else:
        # Только видео + короткий текст → обычный sendVideo с caption
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
            tg("sendMessage",
               {"chat_id": CHANNEL_ID, "text": overflow, "parse_mode": "HTML"})

    if sent_ok:
        crosspost_after_telegram(final_text, downloaded=downloaded)

    shutil.rmtree(MEDIA_DIR, ignore_errors=True)
    if POST_DELAY_SECONDS > 0:
        time.sleep(POST_DELAY_SECONDS)
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


TWITTER_SNOWFLAKE_EPOCH_MS = 1288834974657


def twitter_snowflake_datetime(post_id: str) -> datetime | None:
    try:
        numeric_id = int(str(post_id or "").strip())
    except (TypeError, ValueError):
        return None
    if numeric_id <= 0:
        return None
    timestamp_ms = (numeric_id >> 22) + TWITTER_SNOWFLAKE_EPOCH_MS
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def twitter_item_datetime(item: dict) -> datetime | None:
    if not isinstance(item, dict):
        return None
    parsed_date = parse_datetime_utc(item.get("pub_date", ""))
    if parsed_date:
        return parsed_date
    return twitter_snowflake_datetime(item.get("post_id", ""))


def twitter_item_sort_key(item: dict) -> tuple:
    item_dt = twitter_item_datetime(item)
    timestamp = item_dt.timestamp() if item_dt else 0
    try:
        numeric_id = int(str(item.get("post_id") or "0"))
    except (TypeError, ValueError):
        numeric_id = 0
    return (timestamp, numeric_id)


def sort_twitter_items_newest_first(items: list) -> list:
    return sorted(items or [], key=twitter_item_sort_key, reverse=True)


def twitter_merge_key(item: dict) -> str:
    post_id = str(item.get("post_id") or "").strip()
    if post_id:
        return f"id:{post_id}"

    post_url = str(item.get("post_url") or "").strip()
    ids = twitter_status_ids_from_text(post_url)
    if ids:
        return f"id:{sorted(ids)[0]}"

    normalized_url = canonical_url(post_url)
    if normalized_url:
        return f"url:{normalized_url}"

    media_key = media_fingerprint(item.get("media_items") or [])
    if media_key:
        return f"media:{media_key}"

    return short_hash(str(item))


def merge_twitter_results(*groups: list) -> list:
    seen = set()
    merged = []
    for group in groups:
        for item in group or []:
            key = twitter_merge_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return sort_twitter_items_newest_first(merged)


def twitter_newest_age_hours(items: list) -> float | None:
    dates = [twitter_item_datetime(item) for item in items or []]
    dates = [dt for dt in dates if dt]
    if not dates:
        return None
    return (datetime.now(timezone.utc) - max(dates)).total_seconds() / 3600


def is_probable_twitter_media_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return (
        "twimg.com" in lowered
        or "video.twimg.com" in lowered
        or re.search(r"\.(?:jpg|jpeg|png|webp|gif|mp4|mov|m4v|webm)(?:[?#]|$)", lowered) is not None
    )


def flatten_scweet_media_urls(value) -> list[str]:
    """Извлечь медиа-URL из произвольного значения Scweet.
    Рекурсирует только по безопасным медиа-ключам — не идёт в quoted_status, card, user.
    """
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
                v for v in variants
                if isinstance(v, dict) and (
                    "video" in str(v.get("content_type") or "").lower()
                    or is_twitter_video_url(str(v.get("url") or ""))
                )
            ]
            if video_variants:
                best = max(video_variants, key=lambda v: int(v.get("bitrate") or 0))
                urls.extend(flatten_scweet_media_urls(best.get("url")))
                return urls

        # Рекурсируем ТОЛЬКО по безопасным медиа-ключам.
        # НЕ идём по всем value.values() — там quoted_status, card, user и т.д.
        MEDIA_KEYS = {"extended_entities", "entities", "media", "photos", "videos", "attachments"}
        for k, v in value.items():
            if str(k).lower() in MEDIA_KEYS:
                urls.extend(flatten_scweet_media_urls(v))
    return urls


def extract_scweet_media_items(tw: dict) -> list:
    """Извлечь медиа из твита Scweet 5.3.

    Scweet 5.3 (TweetRecord.model_dump()):
      tw["media"]["image_links"] = ["url1", ...]   — фото
      tw["raw"]["legacy"]["extended_entities"]      — видео

    Приоритет: видео > фото. Дедупликация по имени файла без query-параметров.
    """
    def base_key(url: str) -> str:
        return url.split("?")[0].rstrip("/").split("/")[-1].lower()

    seen: set = set()
    media_items: list = []

    def add(url: str, media_type: str):
        if not url:
            return
        k = base_key(url)
        if k in seen:
            return
        seen.add(k)
        add_media_item(media_items, url, media_type)

    # ── 1. Видео из raw GraphQL (extended_entities) ──
    raw = tw.get("raw") or {}
    if isinstance(raw, dict):
        legacy = raw.get("legacy") or {}
        ext = legacy.get("extended_entities") or {}
        for media_obj in (ext.get("media") or []):
            if not isinstance(media_obj, dict):
                continue
            mtype = str(media_obj.get("type") or "").lower()
            if mtype in ("video", "animated_gif"):
                variants = (media_obj.get("video_info") or {}).get("variants") or []
                video_variants = [
                    v for v in variants
                    if isinstance(v, dict) and (
                        "video" in str(v.get("content_type") or "").lower()
                        or is_twitter_video_url(str(v.get("url") or ""))
                    )
                ]
                if video_variants:
                    best = max(video_variants, key=lambda v: int(v.get("bitrate") or 0))
                    add(str(best.get("url") or ""), "video")

    # Если нашли видео — фото не добавляем
    if media_items:
        return media_items[:10]

    # ── 2. Фото из tw["media"]["image_links"] (Scweet 5.3 TweetMedia) ──
    media_block = tw.get("media")
    if isinstance(media_block, dict):
        for url in (media_block.get("image_links") or []):
            add(str(url), "photo")

    # ── 3. Запасной вариант: прямые ключи на верхнем уровне ──
    if not media_items:
        for key in ("image_links", "imageLinks", "image_urls", "imageUrls", "images", "photos"):
            for url in flatten_scweet_media_urls(tw.get(key)):
                add(url, "photo")

    return media_items[:10]



REPOST_FLAG_KEYS = {
    "is_retweet",
    "isretweet",
    "retweeted",
    "retweeted_status",
    "retweetedstatus",
    "retweeted_status_id",
    "retweetedstatusid",
    "retweet_id",
    "retweetid",
    "retweetedtweet",
    "retweeted_tweet",
    "repost",
    "is_repost",
    "isrepost",
    "is_quote_status",
    "isquotestatus",
    "is_quote",
    "isquote",
    "quoted_status",
    "quotedstatus",
    "quoted_status_id",
    "quotedstatusid",
    "quoted_tweet",
    "quotedtweet",
    "quote_tweet",
    "quotetweet",
    "quote_status",
    "quotestatus",
    "in_reply_to_status_id",
    "inreplytostatusid",
    "in_reply_to_status_id_str",
    "inreplytostatusidstr",
    "in_reply_to_tweet_id",
    "inreplytotweetid",
    "referenced_tweets",
    "referencedtweets",
}

REPOST_TEXT_RE = re.compile(
    r"^\s*(?:RT|QT)\s+@|^\s*(?:ретвит|репост|цитата)\b|"
    r"^\s*(?:retweeted|reposted|quoted)\b|"
    r"\b(?:retweeted|reposted)\s+by\b|\bquoted\s+tweet\b|"
    r"\b(?:ретвитнул|ретвитнула|репостнул|репостнула)\b",
    flags=re.I,
)


def truthy_repost_value(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "null", "no"}
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(value)


def nested_repost_marker(value, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z0-9_]", "_", str(key).strip().lower())
            if normalized_key in REPOST_FLAG_KEYS and truthy_repost_value(nested):
                return str(key)
            reason = nested_repost_marker(nested, depth + 1)
            if reason:
                return reason
    elif isinstance(value, list):
        for nested in value:
            reason = nested_repost_marker(nested, depth + 1)
            if reason:
                return reason
    return ""


def external_status_reference(value, own_post_id: str = "") -> str:
    values = []
    collect_string_values(value, values)
    own_ids = {own_post_id} if own_post_id else set()
    for candidate in values:
        for status_id in twitter_status_ids_from_text(candidate):
            if status_id and status_id not in own_ids:
                return status_id
    return ""


def scweet_repost_reference_reason(tw: dict, raw: str, post_id: str) -> str:
    marker = nested_repost_marker(tw)
    if marker:
        return f"marker:{marker}"
    if REPOST_TEXT_RE.search(raw or ""):
        return "text-marker"
    # Scweet may keep unrelated historical/status URLs inside the raw GraphQL payload.
    # Treat only links visible in the tweet text as "this post refers to another post".
    referenced_status_id = external_status_reference(raw, post_id)
    if referenced_status_id:
        return f"status-reference:{referenced_status_id}"
    return ""


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

    # Only original media posts. A local guard below also rejects quote/repost payloads.
    # filter:media убран для запросов по аккаунтам (keywords=[]):
    # X API иногда не ставит media-флаг на фото-посты, из-за чего они пропадают.
    # Медиа-фильтрация выполняется локально после получения твитов.
    base_filters = ["-filter:retweets", "-filter:replies"]
    if keyword_query:
        base_filters.insert(0, "filter:media")
    search_query = " ".join(
        part for part in ([keyword_query] + base_filters) if part
    ).strip()

    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=48)).strftime("%Y-%m-%d")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"  Scweet query: {search_query[:120]}")
    if account_filters:
        print(f"  Scweet from_users: {len(account_filters)} account(s)")

    import concurrent.futures
    SCWEET_TIMEOUT = 90  # секунд — после этого считаем Scweet завис

    def _run_with_timeout(fn, *args, **kwargs):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(fn, *args, **kwargs)
            try:
                return fut.result(timeout=SCWEET_TIMEOUT)
            except concurrent.futures.TimeoutError:
                print(f"  Scweet search зависла после {SCWEET_TIMEOUT}с — прерываем")
                return None

    try:
        tweets = _run_with_timeout(
            s.search,
            search_query,
            since=since,
            until=until,
            display_type="Latest",
            limit=limit,
            from_users=account_filters or None,
            tweet_type="exclude_retweets",
            save=False,
        )
        if tweets is None:
            return []
    except TypeError as e:
        fallback_parts = [keyword_query]
        if account_filters:
            fallback_parts.append(f"({' OR '.join(f'from:{a}' for a in account_filters)})")
        fallback_parts.extend(["filter:media", "-filter:retweets", "-filter:replies"])
        fallback_query = " ".join(part for part in fallback_parts if part).strip()
        print(f"  Scweet structured params недоступны ({e}) — fallback query: {fallback_query[:120]}")
        tweets = _run_with_timeout(
            s.search,
            fallback_query,
            since=since,
            until=until,
            display_type="Latest",
            limit=limit,
            save=False,
        )
        if tweets is None:
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
        repost_reason = scweet_repost_reference_reason(tw, raw, post_id)
        if repost_reason:
            print(f"  Scweet: репост/ссылка на другой пост — пропуск ({repost_reason})")
            continue
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
    results = sort_twitter_items_newest_first(results)
    if results:
        newest = twitter_item_datetime(results[0])
        if newest:
            print(f"  Scweet: самый свежий твит {newest:%Y-%m-%d %H:%M} UTC")
    return results


def normalize_twitter_post_url(url: str, post_id: str = "") -> str:
    url = normalize_scweet_tweet_url(url, post_id)
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if "nitter." in host or host in {"twitter.com", "x.com"}:
        return urlunparse(("https", "x.com", parsed.path, "", "", ""))
    return url


def nitter_item_text(account: str, item: dict) -> str:
    title = html.unescape(str(item.get("title") or "")).strip()
    content = html.unescape(str(item.get("content_text") or "")).strip()
    text = title or content
    account_re = re.escape(account.lstrip("@"))
    patterns = (
        rf"^\s*@?{account_re}\s*:\s*",
        rf"^\s*[^:]+?\(\s*@?{account_re}\s*\)\s*:\s*",
        rf"^\s*{account_re}\s+on\s+(?:X|Twitter)\s*:\s*",
    )
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.I)
    text = re.sub(r"\s+(?:/|-)\s*Nitter\s*$", "", text, flags=re.I)
    return text.strip() or content


def fetch_nitter_account_tweets(account: str, limit: int = 20) -> list:
    account = normalize_twitter_account(account)
    if not account:
        return []

    rss_url = nitter_rss_url(account)
    if not rss_url:
        print(f"  Nitter @{account}: RSS недоступен")
        return []

    items = fetch_rss_items(rss_url)
    results = []
    for item in items[:limit]:
        post_url = normalize_twitter_post_url(item.get("url") or item.get("id"), "")
        ids = twitter_status_ids_from_text(post_url or str(item.get("id") or ""))
        post_id = sorted(ids)[0] if ids else ""
        if post_id and not post_url:
            post_url = normalize_twitter_post_url("", post_id)

        raw = nitter_item_text(account, item)
        pub_date = str(item.get("date_published") or "")
        if not raw:
            continue

        repost_reason = scweet_repost_reference_reason({}, raw, post_id)
        if repost_reason:
            print(f"  Nitter @{account}: репост/ссылка — пропуск ({repost_reason})")
            continue

        media_items = extract_media_from_feed_item(item, rss_url)
        if not media_items and post_url:
            media_items = extract_media_from_nitter_page(post_url)
        if not media_items:
            continue

        results.append(
            {
                "post_id": post_id,
                "post_url": post_url,
                "author": account,
                "raw": raw,
                "pub_date": pub_date,
                "media_items": media_items,
            }
        )

    results = sort_twitter_items_newest_first(results)
    print(f"  Nitter @{account}: получено {len(items)} RSS items, с медиа: {len(results)}")
    return results


def fetch_nitter_accounts_tweets(accounts: list, limit: int = 20) -> list:
    results = []
    for account in accounts or []:
        results.extend(fetch_nitter_account_tweets(account, limit=limit))
    return sort_twitter_items_newest_first(results)


def step3_should_use_nitter_fallback(scweet_tweets: list) -> bool:
    if not STEP3_NITTER_FALLBACK:
        return False
    newest_age = twitter_newest_age_hours(scweet_tweets)
    if newest_age is None:
        print("  Scweet не дал датированных твитов — включаем Nitter fallback")
        return True
    if newest_age > STEP3_NITTER_STALE_HOURS:
        print(
            "  Scweet выглядит устаревшим "
            f"(самый свежий твит {newest_age:.1f} ч назад) — включаем Nitter fallback"
        )
        return True
    return False


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

        dup = find_duplicate(published, post_id=post_id, url=post_url, text=raw, source=source_label, media_items=media_items)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if is_semantic_duplicate(raw, source=source_label, post_id=post_id, url=post_url):
            continue

        translated = translate_deepl(raw)
        edited = edit_openrouter(translated)

        print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
        ok = send_to_telegram(media_items, edited)
        if ok:
            mark_published(published, post_id=post_id, url=post_url, text=raw, source=source_label, media_items=media_items)
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
        source_label = hashtag_post_owner(post) or "instagram-apify"
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

        dup = find_duplicate(
            published,
            post_id=post_id,
            url=post_url,
            text=raw_caption,
            source=source_label,
            media_items=media_items,
            extra_keys=ig_keys,
        )
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if raw_caption and is_semantic_duplicate(raw_caption, source=source_label, post_id=post_id, url=post_url):
            continue

        if raw_caption:
            translated = translate_deepl(raw_caption)
            edited = edit_openrouter(translated)
        else:
            edited = ""

        print(f"  Публикуем ({len(media_items)} медиа): {post_id}")
        ok = send_to_telegram(
            media_items,
            edited,
            content_prefix=INSTAGRAM_PREFIX,
        )
        if ok:
            mark_published(
                published,
                post_id=post_id,
                url=post_url,
                text=raw_caption,
                source=source_label,
                media_items=media_items,
                extra_keys=ig_keys,
            )
            save_published(published)
            record_source_news(raw_caption, step=2, source=source_label, post_id=post_id, url=post_url)
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

    scan_limit = max(STEP3_LIMIT, STEP3_SCAN_LIMIT)

    # Забираем свежие посты сразу по всем аккаунтам одним запросом
    tweets = fetch_scweet_tweets(
        keywords=[],           # без фильтра по словам — все посты аккаунтов
        from_accounts=STEP3_ACCOUNTS,
        limit=scan_limit,
    )
    if step3_should_use_nitter_fallback(tweets):
        fallback_tweets = fetch_nitter_accounts_tweets(STEP3_ACCOUNTS, limit=scan_limit)
        if fallback_tweets:
            before_merge = len(tweets)
            tweets = merge_twitter_results(tweets, fallback_tweets)
            print(f"  Step 3 merge: Scweet {before_merge} + Nitter {len(fallback_tweets)} => {len(tweets)}")

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

        dup = find_duplicate(published, post_id=post_id, url=post_url, text=raw, source=f"@{source}", media_items=media_items)
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if is_semantic_duplicate(raw, source=f"@{source}", post_id=post_id, url=post_url):
            continue

        translated = translate_deepl(raw)
        edited = edit_openrouter(translated)

        print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
        ok = send_to_telegram(media_items, edited)
        if ok:
            mark_published(published, post_id=post_id, url=post_url, text=raw, source=f"@{source}", media_items=media_items)
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


def make_hashtag_telegram_html(
    caption: str,
    hashtag: str,
    *,
    body_html: str | None = None,
) -> str:
    """Формирует HTML-текст поста с подписью шага 4."""
    caption = strip_html_tags(caption or "").replace("@", "-").strip()

    # Подпись-заголовок (если задан префикс)
    if STEP4_PREFIX:
        prefix_escaped = html.escape(STEP4_PREFIX.replace("@", "-"), quote=False)
        header = f"<b>{prefix_escaped}</b>"
    else:
        header = f"<b>#{html.escape(hashtag, quote=False)}</b>"

    if body_html is not None:
        rendered = (body_html or "").strip()
    elif caption:
        rendered = make_telegram_html(caption, bold_first_line=False)
    else:
        rendered = ""

    if rendered:
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

            owner = hashtag_post_owner(post)
            source_label = f"@{owner}" if owner else f"ig-hashtag-#{hashtag}"
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
            dup = find_duplicate(
                published,
                post_id=post_id,
                url=post_url,
                text=raw_caption,
                source=source_label,
                media_items=media_items,
                extra_keys=ig_keys,
            )
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue

            if raw_caption and is_semantic_duplicate(raw_caption, source=source_label, post_id=post_id, url=post_url):
                continue

            translated = translate_deepl(raw_caption) if raw_caption else ""
            edited = edit_openrouter(translated) if translated else ""
            layout_body = ai_check_telegram_layout(edited) if edited else ""
            full_text = make_hashtag_telegram_html(edited, hashtag, body_html=layout_body)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
            ok = send_to_telegram(media_items, full_text, step4_custom_sig=True)
            if ok:
                mark_published(
                    published,
                    post_id=post_id,
                    url=post_url,
                    text=raw_caption,
                    source=source_label,
                    media_items=media_items,
                    extra_keys=ig_keys,
                )
                save_published(published)
                record_source_news(raw_caption, step=4, source=source_label, post_id=post_id, url=post_url)
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
