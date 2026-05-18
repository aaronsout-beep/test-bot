#!/usr/bin/env python3
"""
NicoPaz Telegram Bot
STEP 1: Twitter/Nitter по ключевым словам  -> DeepL + OpenRouter -> Telegram
STEP 2: Instagram через RSS.app             -> DeepL -> Telegram
STEP 3: Twitter-аккаунты                   -> DeepL + OpenRouter -> Telegram

Запуск: python bot.py --step 1|2|3
"""

import argparse
import html
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

# ────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
DEEPL_KEY = os.environ["DEEPL_KEY"]
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")

# Шаг 1
STEP1_RSS_URLS = [
    u.strip() for u in os.environ.get("STEP1_RSS_URLS", "").split(",") if u.strip()
]
STEP1_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("STEP1_KEYWORDS", "kenanyildiz,kenan yildiz").split(",")
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
CAPTION_LIMIT = 1024
MSG_LIMIT = 4096
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
        json.dump(list(ids)[-2000:], f, ensure_ascii=False)


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
        key = p.strip().lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(p.strip())
    return "\n\n".join(unique).strip()


def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def make_telegram_html(text: str, bold_first_line: bool = True) -> str:
    """
    Telegram parse_mode=HTML очень строгий. Поэтому экранируем весь внешний текст
    и сами добавляем только контролируемый тег <b> для первой строки.
    """
    text = strip_html_tags(text).replace("@", "-").strip()
    if not text:
        return ""

    lines = text.splitlines()
    first = html.escape(lines[0].strip(), quote=False)
    rest = "\n".join(lines[1:]).strip()

    if not bold_first_line:
        return html.escape(text, quote=False)

    if rest:
        return f"<b>{first}</b>\n{html.escape(rest, quote=False)}"
    return f"<b>{first}</b>"


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

    # Nitter often uses /pic/orig/media/... or /pic/media/... proxies.
    # Both must become the real Twitter CDN /media/... URL.
    url = re.sub(
        r"https?://[^/]+/pic/(?:orig/)?media/",
        "https://pbs.twimg.com/media/",
        url,
    )

    return url


def add_media_item(media: list, url: str, media_type: str):
    if not url:
        return
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

    # Some feeds escape the Nitter /pic/... URL as plain text inside HTML.
    for m in re.finditer(
        r"(https?://[^\"'<>\s]+/(?:pic|media)/[^\"'<>\s]+)",
        html.unescape(content_html),
        re.I,
    ):
        add_media_item(
            media,
            normalize_nitter_media_url(m.group(1), base_url),
            "photo",
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

    def add(url: str, mime: str = ""):
        if not url:
            return
        media_type = "video" if (mime or "").startswith("video") or ".mp4" in url.lower() else "photo"
        if url not in [x["url"] for x in media_items]:
            media_items.append({"url": url, "type": media_type})

    for attachment in post.get("attachments") or []:
        url = attachment.get("url", "")
        mime = attachment.get("mime_type", "") or attachment.get("mimeType", "")
        add(url, mime)

    for enclosure in post.get("enclosures") or []:
        url = enclosure.get("url", "")
        mime = enclosure.get("mime_type", "") or enclosure.get("mimeType", "") or enclosure.get("type", "")
        add(url, mime)

    for media in post.get("media") or []:
        if isinstance(media, dict):
            add(media.get("url", ""), media.get("mime_type", "") or media.get("type", ""))
        elif isinstance(media, str):
            add(media)

    images = post.get("images") or []
    if isinstance(images, str):
        images = [images]
    for image in images:
        if isinstance(image, dict):
            add(image.get("url", ""), image.get("mime_type", "") or image.get("type", ""))
        else:
            add(str(image))

    if post.get("image"):
        add(post["image"], "image")

    html_fields = [
        post.get("content_html", ""),
        post.get("content", ""),
        post.get("summary", ""),
        post.get("description", ""),
    ]
    for html_text in html_fields:
        html_text = html.unescape(html_text or "")
        for m in re.finditer(r"<img[^>]+src=[\"']([^\"']+)[\"']", html_text, re.I):
            add(m.group(1), "image")
        for m in re.finditer(r"<(?:video|source)[^>]+src=[\"']([^\"']+)[\"']", html_text, re.I):
            add(m.group(1), "video")

    if media_items:
        print(f"  RSS.app media найдено: {len(media_items)}")

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
            pub_date = item.get("date_published", "")

            if post_id in published:
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

            post_url = item.get("url", "")
            media_items = extract_media_from_nitter_page(post_url) if post_url else []
            if not media_items:
                media_items = extract_media_from_feed_item(item, post_url or rss_url)

            translated = translate_deepl(raw)
            edited = edit_openrouter(translated)
            full_text = make_telegram_html(edited, bold_first_line=True)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
            ok = send_to_telegram(media_items, full_text)
            if ok:
                published.add(post_id)
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
        if not post_id:
            print("  Пост без id/url — пропуск")
            continue

        if post_id in published:
            print(f"  Дубль, пропускаем: {post_id[:60]}")
            continue

        pub_date = instagram_date_from_rssapp_post(post)
        if is_too_old(pub_date):
            print(f"  Старый Instagram-пост ({pub_date[:16]}) — пропуск")
            continue

        media_items = instagram_media_from_rssapp_post(post)
        raw_caption = clean_text(instagram_caption_from_rssapp_post(post))

        if raw_caption:
            translated = translate_deepl(raw_caption)
            full_text = make_telegram_html(
                f"{INSTAGRAM_PREFIX}\n{translated}",
                bold_first_line=True,
            )
        else:
            full_text = make_telegram_html(INSTAGRAM_PREFIX, bold_first_line=True)

        print(f"  Публикуем ({len(media_items)} медиа): {post_id}")
        ok = send_to_telegram(media_items, full_text)
        if ok:
            published.add(post_id)
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
            pub_date = item.get("date_published", "")

            if post_id in published:
                continue
            if is_too_old(pub_date):
                print(f"  Старый ({pub_date[:16]}) — пропуск")
                continue

            raw = clean_text(item.get("content_text") or item.get("title") or "")
            if not raw:
                continue

            post_url = item.get("url", "")
            media_items = extract_media_from_nitter_page(post_url) if post_url else []
            if not media_items:
                media_items = extract_media_from_feed_item(item, post_url or rss_url)

            translated = translate_deepl(raw)
            edited = edit_openrouter(translated)
            full_text = make_telegram_html(edited, bold_first_line=True)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:80]}")
            ok = send_to_telegram(media_items, full_text)
            if ok:
                published.add(post_id)
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
