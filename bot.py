#!/usr/bin/env python3
"""
NicoPaz Telegram Bot
STEP 1: Twitter/Nitter по ключевым словам  -> DeepL + OpenRouter -> Telegram
STEP 2: Instagram через RSS.app             -> DeepL -> Telegram
STEP 3: Twitter-аккаунты                   -> DeepL + OpenRouter -> Telegram

Запуск: python bot.py --step 1|2|3
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
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests

# ────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
DEEPL_KEY = os.environ["DEEPL_KEY"]
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")

# Cross-posting after a successful Telegram post.
VK_ACCESS_TOKEN = os.environ.get("VK_ACCESS_TOKEN", "").strip()
VK_OWNER_ID = os.environ.get("VK_OWNER_ID", "").strip()
VK_API_VERSION = os.environ.get("VK_API_VERSION", "5.199").strip()
VK_FROM_GROUP = os.environ.get("VK_FROM_GROUP", "1").strip()

MAX_TOKEN = os.environ.get("MAX_TOKEN", "").strip()
MAX_CHAT_ID = os.environ.get("MAX_CHAT_ID", "").strip()
MAX_USER_ID = os.environ.get("MAX_USER_ID", "").strip()

# Шаг 1
STEP1_RSS_URLS = [
    u.strip() for u in os.environ.get("STEP1_RSS_URLS", "").split(",") if u.strip()
]
STEP1_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("STEP1_KEYWORDS", "nicopaz,nico paz").split(",")
    if k.strip()
]

# Шаг 2
RSSAPP_URL = os.environ.get("RSSAPP_URL", "").strip()
INSTAGRAM_PREFIX = "Нико Пас в IG:"

# Шаг 3
STEP3_ACCOUNTS = [
    a.strip().lstrip("@")
    for a in os.environ.get("STEP3_ACCOUNTS", "").split(",")
    if a.strip()
]

NITTER_MIRRORS = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

SIGNATURE = '\n\n#NicoPaz | <a href="https://t.me/+YRPVzgbXbA1kODEy">Follow us</a>'
CROSSPOST_SIGNATURE = "\n\n#NicoPaz"
CAPTION_LIMIT = 1024
MSG_LIMIT = 4096
MAX_TEXT_LIMIT = 4000
MAX_AGE_DAYS = 2
PUBLISHED_FILE = "published_ids.json"
MEDIA_DIR = Path("media_tmp")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NicoPazBot/1.0)"}


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def load_published() -> set:
    if Path(PUBLISHED_FILE).exists():
        try:
            with open(PUBLISHED_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"  Не удалось прочитать {PUBLISHED_FILE}: {e}")
    return set()


def save_published(ids: set):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(ids)[-5000:], f, ensure_ascii=False)


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
    urls = [canonical_url(item.get("url", "")) for item in media_items or [] if item.get("url")]
    urls = sorted(u for u in urls if u)
    if not urls:
        return ""
    return short_hash("|".join(urls[:10]))


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


def instagram_shortcode_from_post(post: dict) -> str:
    candidates = []

    def collect(value):
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
        elif isinstance(value, str):
            candidates.append(value)

    for key in ("id", "url", "external_url", "link", "guid", "content_html", "content_text", "summary", "title"):
        collect(post.get(key))
    collect(post.get("attachments"))
    collect(post.get("media"))
    collect(post.get("enclosures"))

    for candidate in candidates:
        shortcode = instagram_shortcode_from_text(candidate)
        if shortcode:
            return shortcode
    return ""


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


def clean_text(text: str) -> str:
    text = re.sub(r"https?://nitter\.[^\s]+", "", text)
    text = re.sub(r"https?://t\.co/[^\s]+", "", text)
    text = re.sub(r"https?://twitter\.[^\s]+", "", text)
    text = re.sub(r"https?://instagram\.[^\s]+", "", text)
    text = re.sub(r"\n[^\n]+\(@[^)]+\)\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("@", "-")
    paras = text.split("\n\n")
    seen, unique = set(), []
    for p in paras:
        cleaned = p.strip()
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


def normalize_club_name(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[^\w\sА-Яа-яA-Za-zÀ-ÿ-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


FOOTBALL_CLUB_KEYS = {normalize_club_name(name) for name in FOOTBALL_CLUB_NAMES}


def word_count(text: str) -> int:
    return len(re.findall(r"[\wА-Яа-яA-Za-zÀ-ÿ-]+", text, flags=re.UNICODE))


def should_blockquote_quote(text: str) -> bool:
    if normalize_club_name(text) in FOOTBALL_CLUB_KEYS:
        return False
    return word_count(text) > 0


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


def render_telegram_line(line: str, bold_line: bool) -> list:
    rendered = []
    for kind, value in split_quoted_segments(line):
        value = value.strip()
        if not value:
            continue
        escaped = html.escape(value, quote=False)
        if kind == "quote":
            if should_blockquote_quote(value):
                rendered.append(f"<blockquote>{escaped}</blockquote>")
            else:
                rendered.append(f'"{escaped}"')
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

    output = []
    first_content_line = True
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue

        bold_line = bool(bold_first_line and first_content_line)
        output.extend(render_telegram_line(line, bold_line))
        first_content_line = False

    return "\n".join(output).strip()


def make_instagram_telegram_html(caption: str) -> str:
    prefix = html.escape(INSTAGRAM_PREFIX.replace("@", "-"), quote=False)
    caption = strip_html_tags(caption or "").replace("@", "-").strip()

    if not caption:
        return f"<b>{prefix}</b>"

    escaped_caption = html.escape(caption, quote=False)
    return f"<b>{prefix}</b>\n<blockquote>{escaped_caption}</blockquote>"


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
# INSTAGRAM через RSS.app
# ════════════════════════════════════════════════════════════

def fetch_instagram_posts() -> list:
    """Fetch Instagram posts from an RSS.app JSON feed."""
    try:
        r = requests.get(RSSAPP_URL, timeout=15, headers=REQUEST_HEADERS)
        r.raise_for_status()
        data = r.json()
        return data.get("items", [])
    except Exception as e:
        print(f"  RSS.app ошибка: {e}")
        return []


def instagram_media_from_rssapp_post(post: dict) -> list:
    media_items = []
    seen_keys = set()

    def as_list(value):
        if not value:
            return []
        return value if isinstance(value, list) else [value]

    def media_type_from(url: str, mime: str = "", key_hint: str = "") -> str:
        marker = f"{mime} {key_hint} {url}".lower()
        if any(x in marker for x in ("video", ".mp4", ".mov", ".m4v", ".webm")):
            return "video"
        return "photo"

    def media_identity(url: str) -> str:
        # Keep query for actual downloading, but dedupe common resized copies.
        return re.sub(r"([?&])(width|height|w|h|resize|size)=[^&]+", r"\1", url).rstrip("?&")

    def looks_like_media_url(url: str, key_hint: str = "") -> bool:
        lower_url = url.lower()
        lower_hint = key_hint.lower()
        if lower_url.startswith("data:"):
            return False
        if "instagram.com/p/" in lower_url or "instagram.com/reel/" in lower_url:
            return False
        if any(x in lower_url for x in ("cdninstagram", "fbcdn", "scontent", "rssapp")):
            return True
        if re.search(r"\.(jpg|jpeg|png|webp|gif|mp4|mov|m4v|webm)(?:[?#]|$)", lower_url):
            return True
        return any(x in lower_hint for x in ("image", "photo", "video", "thumbnail", "media", "attachment", "enclosure"))

    def add(url: str, mime: str = "", key_hint: str = ""):
        if not url:
            return
        url = html.unescape(str(url)).strip()
        if not looks_like_media_url(url, key_hint):
            return
        identity = media_identity(url)
        if identity not in seen_keys:
            seen_keys.add(identity)
            media_type = media_type_from(url, mime, key_hint)
            media_items.append({"url": url, "type": media_type})

    for attachment in as_list(post.get("attachments")):
        if not isinstance(attachment, dict):
            add(str(attachment), key_hint="attachments")
            continue
        url = attachment.get("url", "")
        mime = attachment.get("mime_type", "") or attachment.get("mimeType", "") or attachment.get("type", "")
        add(url, mime, "attachments")

    for enclosure in as_list(post.get("enclosures")):
        if not isinstance(enclosure, dict):
            add(str(enclosure), key_hint="enclosures")
            continue
        url = enclosure.get("url", "")
        mime = enclosure.get("mime_type", "") or enclosure.get("mimeType", "") or enclosure.get("type", "")
        add(url, mime, "enclosures")

    for media in as_list(post.get("media")):
        if isinstance(media, dict):
            add(media.get("url", ""), media.get("mime_type", "") or media.get("type", ""), "media")
        elif isinstance(media, str):
            add(media, key_hint="media")

    for image in as_list(post.get("images")):
        if isinstance(image, dict):
            add(image.get("url", ""), image.get("mime_type", "") or image.get("type", ""), "images")
        else:
            add(str(image), key_hint="images")

    if post.get("image"):
        add(post["image"], "image", "image")

    html_fields = [
        post.get("content_html", ""),
        post.get("content", ""),
        post.get("summary", ""),
        post.get("description", ""),
    ]
    for html_text in html_fields:
        html_text = html.unescape(html_text or "")
        for tag in re.finditer(r"<img[^>]+>", html_text, re.I):
            tag_text = tag.group(0)
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                m = re.search(attr + r"=[\"']([^\"']+)[\"']", tag_text, re.I)
                if m:
                    add(m.group(1), "image", attr)
            srcset = re.search(r"srcset=[\"']([^\"']+)[\"']", tag_text, re.I)
            if srcset:
                for candidate in srcset.group(1).split(","):
                    add(candidate.strip().split(" ")[0], "image", "srcset")

        for tag in re.finditer(r"<(?:video|source)[^>]+>", html_text, re.I):
            tag_text = tag.group(0)
            m = re.search(r"src=[\"']([^\"']+)[\"']", tag_text, re.I)
            if m:
                add(m.group(1), "video", "video")

        for m in re.finditer(r"https?://[^\"'<>\s]+", html_text, re.I):
            add(m.group(0), key_hint="content_html")

    def scan_nested(value, path: str = ""):
        if isinstance(value, dict):
            for key, nested in value.items():
                scan_nested(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                scan_nested(nested, f"{path}[{index}]")
        elif isinstance(value, str):
            if value.startswith("http"):
                add(value, key_hint=path)
            elif ("<img" in value.lower()) or ("<video" in value.lower()) or ("<source" in value.lower()):
                for m in re.finditer(r"https?://[^\"'<>\s]+", html.unescape(value), re.I):
                    add(m.group(0), key_hint=path)

    scan_nested(post)

    if media_items:
        print(f"  RSS.app media найдено: {len(media_items)}")
        for item in media_items[:10]:
            print(f"    instagram media: {item['type']} {item['url'][:160]}")

    return media_items[:10]


def instagram_caption_from_rssapp_post(post: dict) -> str:
    return (
        post.get("content_text")
        or post.get("summary")
        or post.get("title")
        or ""
    ).strip()


def instagram_date_from_rssapp_post(post: dict) -> str:
    return (
        post.get("date_published")
        or post.get("date_modified")
        or post.get("published")
        or post.get("pubDate")
        or post.get("isoDate")
        or post.get("date")
        or ""
    )


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
                "model": "meta-llama/llama-3.1-8b-instruct:free",
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

    cut = full_text.rfind(" ", 0, max_text)
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
        for i, item in enumerate(downloaded[:10]):
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


def send_to_telegram(media_items: list, full_text: str) -> bool:
    caption, overflow = split_caption(full_text, bool(media_items))
    downloaded = []

    for i, item in enumerate(media_items[:10]):
        path = download_media(item["url"], item["type"], i)
        if path:
            downloaded.append({"path": path, "type": item["type"]})

    if not downloaded:
        if media_items:
            print("  Все медиа недоступны — шлём текст")
        res = tg(
            "sendMessage",
            {"chat_id": CHANNEL_ID, "text": caption, "parse_mode": "HTML"},
        )
        sent_ok = res.get("ok", False)

    elif len(downloaded) == 1:
        sent_ok = send_single_media(downloaded[0], caption)
        if not sent_ok:
            print("  Медиа не отправилось — пробуем отправить текст")
            res = tg(
                "sendMessage",
                {"chat_id": CHANNEL_ID, "text": caption, "parse_mode": "HTML"},
            )
            sent_ok = res.get("ok", False)

    else:
        sent_ok = send_media_group(downloaded, caption)
        if not sent_ok:
            print("  Медиагруппа не отправилась — пробуем отправить текст")
            res = tg(
                "sendMessage",
                {"chat_id": CHANNEL_ID, "text": caption, "parse_mode": "HTML"},
            )
            sent_ok = res.get("ok", False)

    if overflow and sent_ok:
        time.sleep(1.5)
        tg(
            "sendMessage",
            {"chat_id": CHANNEL_ID, "text": overflow, "parse_mode": "HTML"},
        )

    if sent_ok:
        crosspost_after_telegram(full_text)

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

def step1_twitter_keywords(published: set) -> set:
    print("\n══════ ШАГ 1: Twitter по ключевым словам ══════")
    if not STEP1_RSS_URLS:
        print("  STEP1_RSS_URLS не задан — пропускаем")
        return published

    new_count = 0
    for rss_url in STEP1_RSS_URLS:
        print(f"\n  Источник: {rss_url[:90]}")
        items = fetch_rss_items(rss_url)
        print(f"  Получено RSS items: {len(items)}")

        for item in items[:10]:
            post_id = item.get("id") or item.get("url") or ""
            post_url = item.get("url", "")
            pub_date = item.get("date_published", "")

            dup = find_duplicate(published, post_id=post_id, url=post_url)
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue
            if is_too_old(pub_date):
                print(f"  Старый пост ({pub_date[:16]}) — пропуск")
                continue

            all_text = " ".join(
                [item.get("title", ""), item.get("content_text", "")]
            ).lower()
            if not any(kw in all_text for kw in STEP1_KEYWORDS):
                continue

            raw = clean_text(item.get("content_text") or item.get("title") or "")
            if not raw:
                continue

            media_items = extract_media_from_nitter_page(post_url) if post_url else []
            if not media_items:
                media_items = extract_media_from_feed_item(item, post_url or rss_url)

            dup = find_duplicate(
                published,
                post_id=post_id,
                url=post_url,
                text=raw,
                media_items=media_items,
            )
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue

            translated = translate_deepl(raw)
            edited = edit_openrouter(translated)
            full_text = make_telegram_html(edited, bold_first_line=False)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
            ok = send_to_telegram(media_items, full_text)
            if ok:
                mark_published(
                    published,
                    post_id=post_id,
                    url=post_url,
                    text=raw,
                    media_items=media_items,
                )
                save_published(published)
                new_count += 1
                time.sleep(2)

    print(f"\n  Итого опубликовано: {new_count}")
    return published


def step2_instagram(published: set) -> set:
    print("\n══════ ШАГ 2: Instagram ══════")
    if not RSSAPP_URL:
        print("  RSSAPP_URL не задан — пропускаем")
        return published

    posts = fetch_instagram_posts()
    print(f"  Получено RSS.app posts: {len(posts)}")
    new_count = 0

    if not posts:
        print("  Новых постов нет")
        return published

    for post in posts[:10]:
        post_id = post.get("id") or post.get("url") or ""
        post_url = post.get("url") or ""
        ig_shortcode = instagram_shortcode_from_post(post)
        ig_keys = [f"ig:{ig_shortcode.lower()}"] if ig_shortcode else []
        if not post_id:
            print("  Пост без id/url — пропуск")
            continue

        dup = find_duplicate(
            published,
            post_id=post_id,
            url=post_url,
            extra_keys=ig_keys,
        )
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        pub_date = instagram_date_from_rssapp_post(post)
        if is_too_old(pub_date):
            print(f"  Старый Instagram-пост ({pub_date[:16]}) — пропуск")
            continue

        media_items = instagram_media_from_rssapp_post(post)
        raw_caption = clean_text(instagram_caption_from_rssapp_post(post))

        dup = find_duplicate(
            published,
            post_id=post_id,
            url=post_url,
            text=raw_caption,
            media_items=media_items,
            extra_keys=ig_keys,
        )
        if dup:
            print(f"  Дубль ({dup[:80]}) — пропуск")
            continue

        if raw_caption:
            translated = translate_deepl(raw_caption)
            full_text = make_instagram_telegram_html(translated)
        else:
            full_text = make_instagram_telegram_html("")

        print(f"  Публикуем ({len(media_items)} медиа): {post_id}")
        ok = send_to_telegram(media_items, full_text)
        if ok:
            mark_published(
                published,
                post_id=post_id,
                url=post_url,
                text=raw_caption,
                media_items=media_items,
                extra_keys=ig_keys,
            )
            save_published(published)
            new_count += 1
            time.sleep(2)

    print(f"\n  Итого опубликовано: {new_count}")
    return published


def step3_twitter_accounts(published: set) -> set:
    print("\n══════ ШАГ 3: Twitter-аккаунты ══════")
    if not STEP3_ACCOUNTS:
        print("  STEP3_ACCOUNTS не задан — пропускаем")
        return published

    new_count = 0
    for account in STEP3_ACCOUNTS:
        rss_url = nitter_rss_url(account)
        if not rss_url:
            print(f"  Нет RSS для @{account}")
            continue

        print(f"\n  @{account}: {rss_url}")
        items = fetch_rss_items(rss_url)
        print(f"  Получено RSS items: {len(items)}")

        for item in items[:10]:
            post_id = item.get("id") or item.get("url") or ""
            post_url = item.get("url", "")
            pub_date = item.get("date_published", "")

            dup = find_duplicate(published, post_id=post_id, url=post_url)
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue
            if is_too_old(pub_date):
                print(f"  Старый ({pub_date[:16]}) — пропуск")
                continue

            raw = clean_text(item.get("content_text") or item.get("title") or "")
            if not raw:
                continue

            media_items = extract_media_from_nitter_page(post_url) if post_url else []
            if not media_items:
                media_items = extract_media_from_feed_item(item, post_url or rss_url)

            dup = find_duplicate(
                published,
                post_id=post_id,
                url=post_url,
                text=raw,
                media_items=media_items,
            )
            if dup:
                print(f"  Дубль ({dup[:80]}) — пропуск")
                continue

            translated = translate_deepl(raw)
            edited = edit_openrouter(translated)
            full_text = make_telegram_html(edited, bold_first_line=False)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
            ok = send_to_telegram(media_items, full_text)
            if ok:
                mark_published(
                    published,
                    post_id=post_id,
                    url=post_url,
                    text=raw,
                    media_items=media_items,
                )
                save_published(published)
                new_count += 1
                time.sleep(2)

    print(f"\n  Итого опубликовано: {new_count}")
    return published


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, required=True, choices=[1, 2, 3])
    args = parser.parse_args()

    print(f"▶ Шаг {args.step} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    published = load_published()
    print(f"  Уже опубликовано ID в кэше: {len(published)}")

    if args.step == 1:
        step1_twitter_keywords(published)
    elif args.step == 2:
        step2_instagram(published)
    elif args.step == 3:
        step3_twitter_accounts(published)

    print("\n✓ Готово.")


if __name__ == "__main__":
    main()
