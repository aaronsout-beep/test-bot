#!/usr/bin/env python3
"""
NicoPaz Telegram Bot
STEP 1: Twitter/Nitter по ключевым словам  → DeepL + OpenRouter → Telegram
STEP 2: Instagram через instaloader         → DeepL → Telegram
STEP 3: Twitter-аккаунты                   → DeepL + OpenRouter → Telegram

Запуск: python bot.py --step 1|2|3
"""

import os, sys, json, re, time, argparse, requests, subprocess, shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
CHANNEL_ID       = os.environ["CHANNEL_ID"]
DEEPL_KEY        = os.environ["DEEPL_KEY"]
OPENROUTER_KEY   = os.environ.get("OPENROUTER_KEY", "")

# Шаг 1
STEP1_RSS_URLS   = [u.strip() for u in os.environ.get("STEP1_RSS_URLS","").split(",") if u.strip()]
STEP1_KEYWORDS   = [k.strip().lower() for k in os.environ.get("STEP1_KEYWORDS","kenanyildiz,kenan yildiz").split(",") if k.strip()]

# Шаг 2
INSTAGRAM_USER   = os.environ.get("INSTAGRAM_USER","").strip().lstrip("@")
INSTAGRAM_PREFIX = "Нико Пас в IG:"

# Шаг 3
STEP3_ACCOUNTS   = [a.strip().lstrip("@") for a in os.environ.get("STEP3_ACCOUNTS","").split(",") if a.strip()]

NITTER_MIRRORS   = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

SIGNATURE        = '\n\n#NicoPaz | <a href="https://t.me/+YRPVzgbXbA1kODEy">Follow us</a>'
CAPTION_LIMIT    = 1024
MSG_LIMIT        = 4096
MAX_AGE_DAYS     = 2
PUBLISHED_FILE   = "published_ids.json"
MEDIA_DIR        = Path("media_tmp")


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def load_published() -> set:
    if Path(PUBLISHED_FILE).exists():
        with open(PUBLISHED_FILE) as f:
            return set(json.load(f))
    return set()

def save_published(ids: set):
    with open(PUBLISHED_FILE, "w") as f:
        json.dump(list(ids)[-2000:], f)

def is_too_old(date_str: str) -> bool:
    if not date_str:
        return False
    for fmt in ("%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
        except ValueError:
            continue
    return False

def clean_text(text: str) -> str:
    text = re.sub(r'https?://nitter\.[^\s]+', '', text)
    text = re.sub(r'https?://t\.co/[^\s]+', '', text)
    text = re.sub(r'https?://twitter\.[^\s]+', '', text)
    text = re.sub(r'https?://instagram\.[^\s]+', '', text)
    text = re.sub(r'\n[^\n]+\(@[^\)]+\)\s*$', '', text, flags=re.MULTILINE)
    paras = text.split('\n\n')
    seen, unique = set(), []
    for p in paras:
        key = p.strip().lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(p.strip())
    return '\n\n'.join(unique).strip()

def fetch_rss_items(url: str) -> list:
    """Загружает RSS/JSON Feed и возвращает список постов"""
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"})
        r.raise_for_status()
        if "json" in r.headers.get("Content-Type","") or url.endswith(".json"):
            return r.json().get("items", [])
        # XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        items = []
        for el in root.iter("item"):
            def t(tag): return (el.findtext(tag) or "").strip()
            content_html = t("{http://purl.org/rss/1.0/modules/content/}encoded") or t("description")
            snippet = re.sub(r'<[^>]+>',' ', content_html)
            snippet = re.sub(r'\s+',' ', snippet).strip()
            items.append({
                "id":             t("guid") or t("link"),
                "url":            t("link"),
                "title":          t("title"),
                "content_text":   snippet,
                "content_html":   content_html,
                "date_published": t("pubDate") or t("{http://purl.org/dc/elements/1.1/}date"),
                "image":          ""
            })
        return items
    except Exception as e:
        print(f"  RSS ошибка ({url[:60]}): {e}")
        return []


# ════════════════════════════════════════════════════════════
# ИЗВЛЕЧЕНИЕ МЕДИА
# ════════════════════════════════════════════════════════════

def extract_media_from_nitter_page(post_url: str) -> list:
    """
    РЕШЕНИЕ ПРОБЛЕМЫ 1 для Twitter:
    Парсим HTML страницу поста на Nitter — там есть ВСЕ картинки и видео,
    а не только первое как в RSS.
    """
    media = []
    for mirror in NITTER_MIRRORS:
        # Заменяем домен в URL на текущее зеркало
        page_url = re.sub(r'https?://[^/]+', mirror, post_url)
        try:
            r = requests.get(page_url, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            html = r.text

            # Все картинки из медиа-контейнера
            # Nitter кладёт медиа в <div class="attachments"> или <div class="media-body">
            for m in re.finditer(
                r'<img[^>]+src=["\']([^"\']*(?:pbs\.twimg\.com|nitter)[^"\']*)["\']',
                html, re.I):
                url = m.group(1)
                if '/pic/' in url or 'pbs.twimg' in url:
                    url = url.replace('%2F', '/').replace('%3A', ':')
                    url = re.sub(r'https?://[^/]+/pic/media/', 'https://pbs.twimg.com/media/', url)
                    url = re.sub(r'https?://[^/]+/pic/', 'https://pbs.twimg.com/', url)
                    # Убираем query параметры размера чтобы получить оригинал
                    url = re.sub(r'\?.*$', '', url)
                    if url not in [x["url"] for x in media]:
                        media.append({"url": url, "type": "photo"})

            # Видео
            for m in re.finditer(
                r'<source[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']', html, re.I):
                url = m.group(1)
                if url not in [x["url"] for x in media]:
                    media.append({"url": url, "type": "video"})

            if media:
                print(f"  Найдено {len(media)} медиафайлов на {mirror}")
                return media[:10]

        except Exception as e:
            print(f"  Nitter HTML ошибка ({mirror}): {e}")
            continue

    return media


# ════════════════════════════════════════════════════════════
# INSTAGRAM через instaloader
# ════════════════════════════════════════════════════════════

def fetch_instagram_posts(username: str, published: set) -> list:
    """
    РЕШЕНИЕ ПРОБЛЕМЫ 1 для Instagram:
    instaloader скачивает ВСЕ медиафайлы из поста включая карусели.
    Работает без авторизации для публичных аккаунтов.
    """
    import importlib.util
    if importlib.util.find_spec("instaloader") is None:
        print("  instaloader не установлен — устанавливаем...")
        subprocess.run([sys.executable, "-m", "pip", "install",
                        "instaloader", "--quiet"], check=True)

    import instaloader
    L = instaloader.Instaloader(
        download_pictures=False,   # не скачиваем — только читаем метаданные
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True
    )

    results = []
    try:
        profile = instaloader.Profile.from_username(L.context, username)
        posts   = profile.get_posts()

        for post in posts:
            post_id  = str(post.shortcode)
            pub_date = post.date_utc.replace(tzinfo=timezone.utc)

            # Лимит по дате
            age = datetime.now(timezone.utc) - pub_date
            if age > timedelta(days=MAX_AGE_DAYS):
                print(f"  Пост {post_id} старше {MAX_AGE_DAYS} дней — стоп")
                break  # посты идут от новых к старым, дальше смотреть не нужно

            if post_id in published:
                print(f"  Дубль {post_id} — пропуск")
                continue

            # Собираем URL всех медиафайлов
            media_items = []
            if post.typename == "GraphSidecar":
                # Карусель — несколько медиафайлов
                for node in post.get_sidecar_nodes():
                    if node.is_video:
                        media_items.append({"url": node.video_url, "type": "video"})
                    else:
                        media_items.append({"url": node.display_url, "type": "photo"})
            elif post.is_video:
                media_items.append({"url": post.video_url, "type": "video"})
            else:
                media_items.append({"url": post.url, "type": "photo"})

            results.append({
                "id":       post_id,
                "caption":  post.caption or "",
                "media":    media_items[:10],
                "pub_date": pub_date.isoformat()
            })

            if len(results) >= 10:
                break

    except instaloader.exceptions.ProfileNotExistsException:
        print(f"  Аккаунт @{username} не найден")
    except Exception as e:
        print(f"  Ошибка instaloader: {e}")

    return results


# ════════════════════════════════════════════════════════════
# ПЕРЕВОД И РЕДАКТУРА
# ════════════════════════════════════════════════════════════

def translate_deepl(text: str) -> str:
    if not text or not DEEPL_KEY:
        return text
    try:
        r = requests.post(
            "https://api-free.deepl.com/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_KEY}",
                     "Content-Type": "application/json"},
            json={"text": [text], "target_lang": "RU"},
            timeout=15)
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
        "- Первую строку выдели жирным: <b>текст</b>\n"
        "- Сохрани все факты и эмодзи.\n"
        "- Верни ТОЛЬКО готовый текст.\n\n" + text
    )
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "meta-llama/llama-3.1-8b-instruct:free",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.7},
            timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  OpenRouter ошибка: {e}")
        return text


# ════════════════════════════════════════════════════════════
# ОТПРАВКА В TELEGRAM
# ════════════════════════════════════════════════════════════

def tg(method: str, payload: dict) -> dict:
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload, timeout=20)
    return r.json()

def split_caption(full_text: str, has_media: bool) -> tuple:
    limit    = CAPTION_LIMIT if has_media else MSG_LIMIT
    sig_len  = len(SIGNATURE)
    max_text = limit - sig_len
    if len(full_text) <= max_text:
        return full_text + SIGNATURE, None
    cut = full_text.rfind(" ", 0, max_text)
    if cut < 1: cut = max_text
    return (full_text[:cut].strip() + SIGNATURE,
            full_text[cut:].strip()  + SIGNATURE)

def send_to_telegram(media_items: list, full_text: str) -> bool:
    caption, overflow = split_caption(full_text, bool(media_items))
    sent_ok = False

    if not media_items:
        res     = tg("sendMessage", {"chat_id": CHANNEL_ID,
                                      "text": caption, "parse_mode": "HTML"})
        sent_ok = res.get("ok", False)

    elif len(media_items) == 1:
        m      = media_items[0]
        method = "sendVideo" if m["type"] == "video" else "sendPhoto"
        field  = "video"    if m["type"] == "video" else "photo"
        res    = tg(method, {"chat_id": CHANNEL_ID, field: m["url"],
                              "caption": caption, "parse_mode": "HTML"})
        sent_ok = res.get("ok", False)
        if not sent_ok:
            print(f"  Медиа недоступно ({res.get('description')}) — шлём текст")
            res     = tg("sendMessage", {"chat_id": CHANNEL_ID,
                                          "text": caption, "parse_mode": "HTML"})
            sent_ok = res.get("ok", False)

    else:
        media_arr = []
        for i, m in enumerate(media_items):
            entry = {"type": m["type"], "media": m["url"]}
            if i == 0:
                entry["caption"]    = caption
                entry["parse_mode"] = "HTML"
            media_arr.append(entry)
        res = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup",
            json={"chat_id": CHANNEL_ID, "media": media_arr},
            timeout=20).json()
        sent_ok = res.get("ok", False)
        if not sent_ok:
            print(f"  Медиагруппа не удалась ({res.get('description')}) — шлём текст")
            res     = tg("sendMessage", {"chat_id": CHANNEL_ID,
                                          "text": caption, "parse_mode": "HTML"})
            sent_ok = res.get("ok", False)

    if overflow and sent_ok:
        time.sleep(1.5)
        tg("sendMessage", {"chat_id": CHANNEL_ID,
                            "text": overflow, "parse_mode": "HTML"})
    return sent_ok


# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ
# ════════════════════════════════════════════════════════════

def nitter_rss_url(account: str) -> str:
    for mirror in NITTER_MIRRORS:
        url = f"{mirror}/{account}/rss"
        try:
            r = requests.get(url, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and "<rss" in r.text[:500]:
                return url
        except:
            continue
    return ""


# ════════════════════════════════════════════════════════════
# ШАГИ
# ════════════════════════════════════════════════════════════

def step1_twitter_keywords(published: set) -> set:
    print("\n══════ ШАГ 1: Twitter по ключевым словам ══════")
    if not STEP1_RSS_URLS:
        print("  STEP1_RSS_URLS не задан — пропускаем"); return published

    new_count = 0
    for rss_url in STEP1_RSS_URLS:
        print(f"\n  Источник: {rss_url[:70]}")
        items = fetch_rss_items(rss_url)
        for item in items[:10]:
            post_id  = item.get("id") or item.get("url") or ""
            pub_date = item.get("date_published","")

            if post_id in published: continue
            if is_too_old(pub_date):
                print(f"  Старый пост ({pub_date[:16]}) — пропуск"); continue

            all_text = " ".join([item.get("title",""),
                                  item.get("content_text","")]).lower()
            if not any(kw in all_text for kw in STEP1_KEYWORDS): continue

            raw        = clean_text(item.get("content_text") or item.get("title") or "")
            if not raw: continue

            # Все медиа со страницы поста (не только из RSS)
            post_url   = item.get("url","")
            media_items = extract_media_from_nitter_page(post_url) if post_url else []

            translated = translate_deepl(raw)
            edited     = edit_openrouter(translated)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:60]}")
            ok = send_to_telegram(media_items, edited)
            if ok:
                published.add(post_id); save_published(published)
                new_count += 1; time.sleep(2)

    print(f"\n  Итого опубликовано: {new_count}")
    return published


def step2_instagram(published: set) -> set:
    print("\n══════ ШАГ 2: Instagram ══════")
    if not INSTAGRAM_USER:
        print("  INSTAGRAM_USER не задан — пропускаем"); return published

    posts     = fetch_instagram_posts(INSTAGRAM_USER, published)
    new_count = 0

    for post in posts:
        post_id = post["id"]
        if post_id in published: continue

        media_items = post["media"]   # ВСЕ медиа из карусели
        raw_caption = clean_text(post.get("caption",""))

        if raw_caption:
            translated = translate_deepl(raw_caption)
            full_text  = f"{INSTAGRAM_PREFIX}\n{translated}"
        else:
            full_text  = INSTAGRAM_PREFIX

        print(f"  Публикуем ({len(media_items)} медиа): {post_id}")
        ok = send_to_telegram(media_items, full_text)
        if ok:
            published.add(post_id); save_published(published)
            new_count += 1; time.sleep(2)

    print(f"\n  Итого опубликовано: {new_count}")
    return published


def step3_twitter_accounts(published: set) -> set:
    print("\n══════ ШАГ 3: Twitter-аккаунты ══════")
    if not STEP3_ACCOUNTS:
        print("  STEP3_ACCOUNTS не задан — пропускаем"); return published

    new_count = 0
    for account in STEP3_ACCOUNTS:
        rss_url = nitter_rss_url(account)
        if not rss_url:
            print(f"  Нет RSS для @{account}"); continue

        print(f"\n  @{account}: {rss_url}")
        items = fetch_rss_items(rss_url)

        for item in items[:10]:
            post_id  = item.get("id") or item.get("url") or ""
            pub_date = item.get("date_published","")

            if post_id in published: continue
            if is_too_old(pub_date):
                print(f"  Старый ({pub_date[:16]}) — пропуск"); continue

            raw = clean_text(item.get("content_text") or item.get("title") or "")
            if not raw: continue

            # Все медиа со страницы поста
            post_url    = item.get("url","")
            media_items = extract_media_from_nitter_page(post_url) if post_url else []

            translated  = translate_deepl(raw)
            edited      = edit_openrouter(translated)

            print(f"  Публикуем ({len(media_items)} медиа): {post_id[:60]}")
            ok = send_to_telegram(media_items, edited)
            if ok:
                published.add(post_id); save_published(published)
                new_count += 1; time.sleep(2)

    print(f"\n  Итого опубликовано: {new_count}")
    return published


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, required=True, choices=[1,2,3])
    args = parser.parse_args()

    print(f"▶ Шаг {args.step} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    published = load_published()

    if   args.step == 1: step1_twitter_keywords(published)
    elif args.step == 2: step2_instagram(published)
    elif args.step == 3: step3_twitter_accounts(published)

    print("\n✓ Готово.")

if __name__ == "__main__":
    main()
