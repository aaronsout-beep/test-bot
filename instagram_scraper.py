"""
instagram_scraper.py — самостоятельный парсер Instagram без Apify.

Используется вместо Apify в Step 2 (аккаунты) и Step 4 (хэштеги).
Работает через внутренний GraphQL API Instagram с куками залогиненного
аккаунта. Куки хранятся в секрете IG_COOKIES_JSON (GitHub Actions).

Формат IG_COOKIES_JSON: JSON-массив объектов вида:
    [{"name": "sessionid", "value": "...", "domain": ".instagram.com"}, ...]

Экспортируется расширением Cookie-Editor или EditThisCookie из браузера,
где выполнен вход в Instagram-аккаунт, предназначенный только для парсинга.
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Константы Instagram API ───────────────────────────────────────────────────

_IG_APP_ID = "936619743392459"       # публичный ID веб-приложения IG (стабилен)

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
    "Referer": "https://www.instagram.com/",
    "X-IG-App-ID": _IG_APP_ID,
    "X-ASBD-ID": "129477",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.instagram.com",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Ch-Ua": '"Google Chrome";v="136", "Chromium";v="136", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# GraphQL query_hash для профиля пользователя (edge_owner_to_timeline_media).
# Если Instagram вернёт 400 — нужно обновить (см. инструкцию).
_PROFILE_QUERY_HASH = "58b6785bea111c67129decbe6a448951"

# Альтернативный эндпоинт профиля (v1 API, более стабилен чем GraphQL).
_PROFILE_API_URL = "https://www.instagram.com/api/v1/feed/user/{user_id}/"

# Эндпоинт хэштега
_HASHTAG_API_URL = "https://www.instagram.com/api/v1/tags/{hashtag}/sections/"

# Задержки между запросами (секунды). Увеличьте при частых 429.
DELAY_BETWEEN_PAGES   = (2.5, 5.0)   # между страницами одного аккаунта
DELAY_BETWEEN_ACCOUNTS = (30.0, 70.0) # между разными аккаунтами/хэштегами
MAX_RETRIES = 3
RETRY_DELAY = (15.0, 30.0)


# ── Сессия ────────────────────────────────────────────────────────────────────

def build_session(cookies_json: str) -> requests.Session:
    """
    Создаёт requests.Session с куками из JSON-строки (секрет IG_COOKIES_JSON).

    Обязательные куки: sessionid, csrftoken, ds_user_id.
    При их отсутствии сессия будет создана, но запросы вернут 401.
    """
    session = requests.Session()
    try:
        cookies = json.loads(cookies_json)
    except Exception as e:
        logger.error(f"IG: ошибка разбора IG_COOKIES_JSON: {e}")
        return session

    required = {"sessionid", "csrftoken", "ds_user_id"}
    found = set()
    for c in cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        domain = c.get("domain", ".instagram.com")
        if not name or not value:
            continue
        session.cookies.set(name, value, domain=domain)
        if name in required:
            found.add(name)

    missing = required - found
    if missing:
        logger.warning(f"IG: в куках отсутствуют обязательные поля: {missing}")

    csrf = session.cookies.get("csrftoken", "")
    if csrf:
        session.headers.update({"X-CSRFToken": csrf})

    session.headers.update(_BASE_HEADERS)
    return session


def session_is_valid(session: requests.Session) -> bool:
    """Пассивная проверка сессии — не используется в основном потоке.

    Оставлена как утилита для ручного тестирования.
    В production-потоке не вызывается: Instagram отклоняет preflight-запросы
    по useragent mismatch даже при живых куках.
    Реальные ошибки сессии (401/403) обрабатываются в _get_json/_post_json.
    """
    try:
        r = session.get(
            "https://www.instagram.com/api/v1/accounts/current_user/?edit=true",
            timeout=15,
        )
        if r.status_code in (401, 403):
            return False
        if r.status_code == 200:
            data = r.json()
            user = data.get("user") or {}
            return bool(user.get("pk") or user.get("id"))
        return False
    except Exception:
        return False


# ── Получение user_id ─────────────────────────────────────────────────────────

def get_user_id(username: str, session: requests.Session) -> Optional[str]:
    """Получает числовой user_id по username."""
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 404:
                logger.warning(f"IG: аккаунт @{username} не найден")
                return None
            if r.status_code in (401, 403):
                logger.error(f"IG: сессия недействительна (get_user_id {username}: {r.status_code})")
                return None
            if r.status_code == 429:
                wait = random.uniform(*RETRY_DELAY) * (attempt + 1)
                logger.warning(f"IG: 429 при get_user_id({username}), ждём {wait:.0f}с")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            user = data.get("data", {}).get("user") or {}
            uid = user.get("id")
            if uid:
                return str(uid)
            logger.warning(f"IG: user_id не найден в ответе для @{username}")
            return None
        except requests.exceptions.Timeout:
            logger.warning(f"IG: timeout при get_user_id({username}), попытка {attempt+1}")
            time.sleep(random.uniform(*RETRY_DELAY))
        except Exception as e:
            logger.warning(f"IG: ошибка get_user_id({username}): {e}")
            return None
    return None


# ── Парсинг постов аккаунта ───────────────────────────────────────────────────

def fetch_user_posts(
    username: str,
    session: requests.Session,
    max_posts: int = 12,
    only_newer_than: Optional[datetime] = None,
    skip_pinned: bool = True,
    known_user_id: Optional[str] = None,
    on_auth_error=None,
) -> list[dict]:
    """
    Возвращает список постов аккаунта username.

    Каждый пост — dict совместимый с instagram_media_from_apify_post / 
    instagram_caption_from_apify_post / instagram_date_from_apify_post в bot.py.
    Если known_user_id передан — пропускает запрос к web_profile_info (экономит лимиты).
    """
    if known_user_id:
        user_id = known_user_id
    else:
        user_id = get_user_id(username, session)
    if not user_id:
        return []

    posts = []
    max_id = None   # курсор пагинации

    while len(posts) < max_posts:
        params: dict = {"count": min(12, max_posts - len(posts))}
        if max_id:
            params["max_id"] = max_id

        url = _PROFILE_API_URL.format(user_id=user_id)
        data = _get_json(session, url, params=params, on_auth_error=on_auth_error)
        if data is None:
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            ts = item.get("taken_at", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

            if only_newer_than and dt and dt <= only_newer_than:
                return posts  # посты идут от новых к старым

            if skip_pinned and item.get("is_pinned"):
                continue

            post = _normalize_post(item, username)
            post["_user_id"] = user_id  # для кеша user_id в bot.py
            posts.append(post)

        if not data.get("more_available"):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break

        time.sleep(random.uniform(*DELAY_BETWEEN_PAGES))

    return posts


# ── Парсинг постов по хэштегу ─────────────────────────────────────────────────

def fetch_hashtag_posts(
    hashtag: str,
    session: requests.Session,
    max_posts: int = 10,
    only_newer_than: Optional[datetime] = None,
    on_auth_error=None,
) -> list[dict]:
    """
    Возвращает список постов по хэштегу.
    Использует /api/v1/tags/{hashtag}/sections/ (внутренний API).
    """
    posts = []
    page_token = None
    seen_ids: set = set()

    payload_base = {
        "include_persistent": "0",
        "tab": "recent",  # 'top' или 'recent'
        "surface": "grid",
        "rank_token": f"{''.join(random.choices('abcdef0123456789', k=32))}",
    }

    stale_streak = 0          # сколько подряд постов оказались старше порога
    _MAX_STALE_STREAK = 8     # выходим, если 8 подряд старых постов (лента кончилась)

    for _ in range(5):   # максимум 5 страниц
        payload = dict(payload_base)
        if page_token:
            payload["next_max_id"] = page_token

        url = _HASHTAG_API_URL.format(hashtag=hashtag)
        data = _post_json(session, url, data=payload, on_auth_error=on_auth_error)
        if data is None:
            break

        sections = data.get("sections") or []
        for section in sections:
            layout_content = section.get("layout_content") or {}
            medias = layout_content.get("medias") or []
            for media_wrapper in medias:
                item = media_wrapper.get("media") or media_wrapper
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or item.get("pk") or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                ts = item.get("taken_at", 0)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
                if only_newer_than and dt and dt <= only_newer_than:
                    stale_streak += 1
                    if stale_streak >= _MAX_STALE_STREAK:
                        return posts   # достаточно старых постов подряд — выходим
                    continue
                stale_streak = 0      # сбрасываем счётчик при свежем посте

                owner = item.get("user") or {}
                username = owner.get("username", "")
                posts.append(_normalize_post(item, username))
                if len(posts) >= max_posts:
                    return posts

        if not data.get("more_available"):
            break
        page_token = data.get("next_max_id")
        if not page_token:
            break

        time.sleep(random.uniform(*DELAY_BETWEEN_PAGES))

    return posts


# ── Нормализация поста в формат совместимый с bot.py ─────────────────────────

def _normalize_post(item: dict, username: str = "") -> dict:
    """
    Приводит сырой объект Instagram API к формату Apify-поста,
    который ожидают instagram_*_from_apify_post() в bot.py.
    """
    item_id  = str(item.get("id") or item.get("pk") or "")
    code     = item.get("code") or item.get("shortcode") or item.get("shortCode") or ""
    ts       = item.get("taken_at", 0)
    caption_obj = item.get("caption") or {}
    caption  = (
        caption_obj.get("text", "") if isinstance(caption_obj, dict)
        else str(caption_obj or "")
    )
    post_url = f"https://www.instagram.com/p/{code}/" if code else ""

    owner = item.get("user") or {}
    owner_username = username or (
        owner.get("username", "") if isinstance(owner, dict) else ""
    )

    # Медиа: карусель, видео или фото
    media_urls: list[dict] = []
    carousel = item.get("carousel_media") or []
    if carousel:
        for child in carousel:
            _extract_media_urls(child, media_urls)
    else:
        _extract_media_urls(item, media_urls)

    return {
        # Поля, которые читает bot.py
        "id":           item_id,
        "shortCode":    code,
        "shortcode":    code,
        "url":          post_url,
        "timestamp":    ts,
        "takenAt":      ts,
        "caption":      caption,
        "text":         caption,
        # Медиа в формате, понятном instagram_media_from_apify_post
        "displayUrl":   media_urls[0]["url"] if media_urls else "",
        "images":       [{"url": m["url"]} for m in media_urls if m["type"] == "photo"],
        "videoUrl":     next((m["url"] for m in media_urls if m["type"] == "video"), ""),
        "childPosts":   [
            {
                "displayUrl": m["url"] if m["type"] == "photo" else "",
                "videoUrl":   m["url"] if m["type"] == "video" else "",
                "type":       m["type"],
            }
            for m in media_urls
        ] if len(media_urls) > 1 else [],
        # Автор — нужен для фильтра STEP4_ALLOWED_ACCOUNTS
        "ownerUsername": owner_username,
        "username":      owner_username,
        "owner": {"username": owner_username},
    }


def _extract_media_urls(item: dict, out: list) -> None:
    """Извлекает URL медиа из объекта поста Instagram API v1."""
    media_type = item.get("media_type", 1)  # 1=фото, 2=видео, 8=карусель

    if media_type == 2:
        # Видео: берём версию наибольшего битрейта
        versions = item.get("video_versions") or []
        if versions:
            best = max(versions, key=lambda v: v.get("width", 0) * v.get("height", 0))
            out.append({"url": best["url"], "type": "video"})
            return

    # Фото: берём версию наибольшего разрешения
    candidates = item.get("image_versions2", {}).get("candidates") or []
    if candidates:
        best = max(candidates, key=lambda c: c.get("width", 0) * c.get("height", 0))
        out.append({"url": best["url"], "type": "photo"})


# ── HTTP-хелперы с ретраями ───────────────────────────────────────────────────

def _get_json(
    session: requests.Session,
    url: str,
    params: Optional[dict] = None,
    on_auth_error=None,
) -> Optional[dict]:
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code in (401, 403):
                logger.error(f"IG: сессия недействительна ({r.status_code}) → {url}")
                if on_auth_error:
                    on_auth_error(r.status_code)
                return None
            if r.status_code == 429:
                wait = random.uniform(*RETRY_DELAY) * (attempt + 1)
                logger.warning(f"IG: 429, ждём {wait:.0f}с")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            logger.warning(f"IG: timeout GET {url}, попытка {attempt+1}")
            time.sleep(random.uniform(*RETRY_DELAY))
        except Exception as e:
            logger.warning(f"IG: ошибка GET {url}: {e}")
            return None
    return None


def _post_json(
    session: requests.Session,
    url: str,
    data: Optional[dict] = None,
    on_auth_error=None,
) -> Optional[dict]:
    for attempt in range(MAX_RETRIES):
        try:
            r = session.post(url, data=data, timeout=20)
            if r.status_code in (401, 403):
                logger.error(f"IG: сессия недействительна ({r.status_code}) → {url}")
                if on_auth_error:
                    on_auth_error(r.status_code)
                return None
            if r.status_code == 429:
                wait = random.uniform(*RETRY_DELAY) * (attempt + 1)
                logger.warning(f"IG: 429, ждём {wait:.0f}с")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            logger.warning(f"IG: timeout POST {url}, попытка {attempt+1}")
            time.sleep(random.uniform(*RETRY_DELAY))
        except Exception as e:
            logger.warning(f"IG: ошибка POST {url}: {e}")
            return None
    return None
