#!/usr/bin/env python3
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
    call_openrouter_layout_checker,
    load_examples,
    select_similar_examples,
)
from format_model.hard_cases import save_hard_case

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
DEEPL_KEY = os.environ.get("DEEPL_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

VK_ACCESS_TOKEN = os.environ.get("VK_ACCESS_TOKEN", "").strip()
VK_OWNER_ID = os.environ.get("VK_OWNER_ID", "").strip()
VK_API_VERSION = os.environ.get("VK_API_VERSION", "5.199").strip()
VK_FROM_GROUP = os.environ.get("VK_FROM_GROUP", "1").strip()
MAX_TOKEN = os.environ.get("MAX_TOKEN", "").strip()
MAX_CHAT_ID = os.environ.get("MAX_CHAT_ID", "").strip()
MAX_USER_ID = os.environ.get("MAX_USER_ID", "").strip()

STEP1_KEYWORDS = [k.strip().lower() for k in os.environ.get("STEP1_KEYWORDS", "").split(",") if k.strip()]
STEP1_ACCOUNTS = [re.sub(r"^@|/status.*", "", a.strip()) for a in os.environ.get("STEP1_ACCOUNTS", "").split(",") if a.strip()]
SCWEET_AUTH_TOKEN = os.environ.get("SCWEET_AUTH_TOKEN", "").strip()
SCWEET_PROXY = os.environ.get("SCWEET_PROXY", "").strip()
STEP1_LIMIT = int((os.environ.get("STEP1_LIMIT") or "50").strip())
STEP3_LIMIT = int((os.environ.get("STEP3_LIMIT") or "20").strip())

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_BASE_URL = (os.environ.get("APIFY_BASE_URL") or "https://api.apify.com/v2").rstrip("/")
APIFY_INSTAGRAM_ACTOR = os.environ.get("APIFY_INSTAGRAM_ACTOR", "apify~instagram-post-scraper").strip().replace("/", "~")
APIFY_INSTAGRAM_ACCOUNTS = [a.strip() for a in os.environ.get("STEP2_INSTAGRAM_ACCOUNTS", "").split(",") if a.strip()]
APIFY_INSTAGRAM_RESULTS_LIMIT = int((os.environ.get("APIFY_INSTAGRAM_RESULTS_LIMIT") or "10").strip())
APIFY_INSTAGRAM_ONLY_NEWER_THAN = (os.environ.get("APIFY_INSTAGRAM_ONLY_NEWER_THAN") or "2 days").strip()
APIFY_INSTAGRAM_DATA_DETAIL_LEVEL = (os.environ.get("APIFY_INSTAGRAM_DATA_DETAIL_LEVEL") or "detailedData").strip()
APIFY_INSTAGRAM_SKIP_PINNED = os.environ.get("APIFY_INSTAGRAM_SKIP_PINNED", "1").strip().lower() not in {"0", "false"}
APIFY_INSTAGRAM_INPUT_JSON = os.environ.get("APIFY_INSTAGRAM_INPUT_JSON", "").strip()
APIFY_RUN_TIMEOUT = int((os.environ.get("APIFY_RUN_TIMEOUT") or "180").strip())
APIFY_MAX_ITEMS = os.environ.get("APIFY_MAX_ITEMS", "").strip()
APIFY_MAX_TOTAL_CHARGE_USD = os.environ.get("APIFY_MAX_TOTAL_CHARGE_USD", "").strip()
INSTAGRAM_PREFIX = " "

STEP3_ACCOUNTS = [re.sub(r"^@|/status.*", "", a.strip()) for a in os.environ.get("STEP3_ACCOUNTS", "").split(",") if a.strip()]
STEP4_HASHTAGS = [h.strip().lstrip("#") for h in os.environ.get("STEP4_HASHTAGS", "").split(",") if h.strip()]
APIFY_HASHTAG_ACTOR = os.environ.get("APIFY_HASHTAG_ACTOR", "apify~instagram-hashtag-scraper").strip().replace("/", "~")
APIFY_HASHTAG_RESULTS_LIMIT = int((os.environ.get("APIFY_HASHTAG_RESULTS_LIMIT") or "10").strip())
APIFY_HASHTAG_ONLY_NEWER_THAN = (os.environ.get("APIFY_HASHTAG_ONLY_NEWER_THAN") or "2 days").strip()
STEP4_SIGNATURE = os.environ.get("STEP4_SIGNATURE", "").strip()
STEP4_PREFIX = os.environ.get("STEP4_PREFIX", "").strip()
STEP4_ALLOWED_ACCOUNTS = {a.strip().lstrip("@").lower() for a in os.environ.get("STEP4_ALLOWED_ACCOUNTS", "").split(",") if a.strip()}

NITTER_MIRRORS = ["https://nitter.net", "https://nitter.privacydev.net", "https://nitter.poast.org"]
SIGNATURE = '\n\n#MaxDowman | <a href="https://t.me/+tsMRdsXtjaE5OTAy">Follow us</a>'
CROSSPOST_SIGNATURE = "\n\n#NicoPaz"
CAPTION_LIMIT = 1024
MSG_LIMIT = 4096
MAX_TEXT_LIMIT = 4000
TELEGRAM_MEDIA_GROUP_LIMIT = 10
MAX_AGE_DAYS = 2
PUBLISHED_FILE = "published_ids.json"
REQUIRE_PUBLISHED_CACHE = os.environ.get("REQUIRE_PUBLISHED_CACHE", "0").strip().lower() not in {"", "0", "false", "no"}
SOURCE_NEWS_FILE = "source_news_cache.json"
SOURCE_NEWS_CACHE_DAYS = 2
SOURCE_NEWS_CACHE_LIMIT = 500
SEMANTIC_CANDIDATE_LIMIT = 3
SEMANTIC_LOCAL_DUPLICATE_THRESHOLD = 0.86
SEMANTIC_AI_PREFILTER_THRESHOLD = 0.24
LAYOUT_AI_MODEL = os.environ.get("LAYOUT_AI_MODEL", "gemini-2.5-flash-lite").strip()
LAYOUT_AI_MAX_CHARS = int(os.environ.get("LAYOUT_AI_MAX_CHARS", "3200"))
LAYOUT_AI_TIMEOUT = int(os.environ.get("LAYOUT_AI_TIMEOUT", "12"))
FORMAT_RAG_ENABLED = os.environ.get("FORMAT_RAG_ENABLED", "1").strip().lower() not in {"0", "false"}
FORMAT_RAG_MODEL = (os.environ.get("FORMAT_RAG_MODEL") or OPENROUTER_MODEL).strip()
MEDIA_DIR = Path("media_tmp")
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# --- НОВЫЕ ПРАВИЛА И ОЧИСТКИ (РЕГУЛЯРНЫЕ ВЫРАЖЕНИЯ) ---

def strip_unwanted_emojis(text: str) -> str:
    text = re.sub(r"[👽👀]", "", text)
    text = re.sub(r"🗣", "", text)
    text = re.sub(r"🚨\s*(?=📊)", "", text)
    text = re.sub(r"🎙\s*🚨\s*🎙", "🎙", text)
    return text

def strip_excessive_bold(text: str) -> str:
    def replace_bold(match: re.Match) -> str:
        content = match.group(1)
        if len(content) > 100 or len(content.split()) > 15:
            return content
        return f"<b>{content}</b>"
    return re.sub(r"<b>(.*?)</b>", replace_bold, text, flags=re.S)

def fix_split_time_and_photos(text: str) -> str:
    # Merge split times/scores around colons or hyphens (only in times/scores context)
    text = re.sub(r"(\d+)\s*:\s*\n\s*(\d+)", r"\1:\2", text)
    text = re.sub(r"(\d+)\s*-\s*\n\s*(\d+)", r"\1-\2", text)
    
    # Pull back orphaned periods
    text = re.sub(r"\s*\n\s*\.(?=(?:\s|$))", r".", text)
    
    # Merge split photos
    text = re.sub(r"\(\s*(?:Фото|Photo)\s*:\s*\n\s*([^\)]+)\)", r"(Фото:\1)", text, flags=re.I)
    return text

def strip_intro_phrases(text: str) -> str:
    # Matches !!️последние новости:, ❗️подтверждено:, подтверждено:, новости:, новость: etc. at start of lines.
    pattern = re.compile(
        r"^\s*(?:[!‼️❗🚨✨]*\s*)*"
        r"(?:последние\s+новости|последняя\s+новость|новости|новость|подтверждено)"
        r"\s*:\s*",
        re.IGNORECASE
    )
    lines = text.split("\n")
    cleaned_lines = []
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if i < 3 and pattern.match(line_stripped):
            line_cleaned = pattern.sub("", line_stripped).strip()
            if line_cleaned:
                cleaned_lines.append(line_cleaned)
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()

def strip_unwanted_hashtags(text: str) -> str:
    # Collect allowed hashtags from system/signature environments
    allowed_tags = set()
    for sig in [SIGNATURE, CROSSPOST_SIGNATURE, STEP4_SIGNATURE]:
        if sig:
            found = re.findall(r"#([\wА-Яа-яA-Za-zÀ-ÿ_]+)", sig)
            for t in found:
                allowed_tags.add(t.lower())
                
    # Direct custom safety safeguards
    allowed_tags.add("desiredoue")
    allowed_tags.add("maxdowman")
    allowed_tags.add("nicopaz")
    
    def replace_hashtag(match: re.Match) -> str:
        full_tag = match.group(0)
        tag_content = match.group(1)
        if tag_content.lower() in allowed_tags:
            return full_tag
        return ""
        
    text = re.sub(r"#([\wА-Яа-яA-Za-zÀ-ÿ_]+)", replace_hashtag, text)
    
    # Process remaining spacing and layout cleanly
    lines = []
    for line in text.split("\n"):
        line_stripped = line.strip()
        line_stripped = re.sub(r"\s*\|\s*(?=\s|$)", "", line_stripped)
        line_stripped = re.sub(r" +", " ", line_stripped)
        lines.append(line_stripped)
    return "\n".join(lines).strip()

def fix_extra_hashtags_and_titles(text: str) -> str:
    match = re.search(r"^[«\"“]([^»\"”]+)[»\"”]\s*\n\s*([а-яёa-zА-Яа-яЁёA-Za-z].+)", text)
    if match:
        name = match.group(1).strip()
        body = match.group(2).strip()
        if body and body[0].isupper() and not body.split()[0] in ["Эндрик", "Нико", "Ламин", "Лео", "Килиан", "Хави"]:
            body = body[0].lower() + body[1:]
        text = f"{name} {body}"
        
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        tags = re.findall(r"#[\wА-Яа-яA-Za-zÀ-ÿ_]+", line)
        if len(tags) >= 3 and len(text.split()) < 30:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()

def is_list_or_stat_line(line: str) -> bool:
    line_stripped = line.strip()
    if not line_stripped:
        return False
    # Check country flag (Unicode regional indicators range 1F1E6-1F1FF)
    if re.match(r"^[\U0001F1E6-\U0001F1FF]{2}", line_stripped):
        return True
    # Check numeric numbering like 1), 2), etc.
    if re.match(r"^\d+\)", line_stripped):
        return True
    # Check bullet point •
    if line_stripped.startswith("•"):
        return True
    # Check sports and standard lists emojis
    list_emojis = [
        "✅", "☑", "☑️", "⚽️", "⚽", "🅰️", "🅰", "🥅", "🔑", "🎯", "⚡️", "⚡",
        "🏆", "🥇", "🥈", "🥉", "🏃", "👟", "📊", "📈", "📉", "⏱️", "⏱"
    ]
    for emoji in list_emojis:
        if line_stripped.startswith(emoji):
            return True
    return False

def normalize_stats_lists(text: str) -> str:
    lines = text.split("\n")
    processed_lines = []
    
    # Split indicators when written inline
    STATS_EMOJIS_RE = re.compile(
        r"(?<!^)\s*(?=(?:[✅☑•🥅🔑🎯⚡🏆🥇🥈🥉⚽🅰🏃👟📊📈📉⏱]|☑️|⚽️|🅰️|⚡️|⏱️|[\U0001F1E6-\U0001F1FF]{2}|\b\d+\)))"
    )
    
    indicators_pattern = re.compile(
        r"[✅☑•🥅🔑🎯⚡🏆🥇🥈🥉⚽🅰🏃👟📊📈📉⏱]|☑️|⚽️|🅰️|⚡️|⏱️|[\U0001F1E6-\U0001F1FF]{2}|\b\d+\)"
    )
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            processed_lines.append(line)
            continue
            
        if len(indicators_pattern.findall(line_stripped)) >= 2:
            parts = re.split(STATS_EMOJIS_RE, line_stripped)
            for part in parts:
                if part.strip():
                    processed_lines.append(part.strip())
        else:
            processed_lines.append(line)
            
    final_lines = []
    in_stats_block = False
    for line in processed_lines:
        line_stripped = line.strip()
        is_stat = is_list_or_stat_line(line_stripped)
        
        if is_stat:
            if not in_stats_block:
                if final_lines and final_lines[-1] != "":
                    final_lines.append("")
                in_stats_block = True
            final_lines.append(line)
        else:
            if in_stats_block:
                if final_lines and final_lines[-1] != "":
                    final_lines.append("")
                in_stats_block = False
            final_lines.append(line)
    return "\n".join(final_lines)

def handle_inline_speaker_quotes(text: str) -> str:
    lines = text.split("\n")
    processed = []
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            processed.append(line)
            continue
        match = re.match(r"^([🗣🎙]️?\s*[^:\n]+):\s*([\"'«“„][^\"'»”“]+[\"'»”“])", line_stripped)
        if match:
            speaker = match.group(1).strip()
            quote = extract_quote_text(match.group(2))
            processed.append(f"{render_colon_heading(speaker)}\n<blockquote>«{quote}»</blockquote>")
        else:
            processed.append(line)
    return "\n".join(processed)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И ДУБЛИКАТЫ ---

def twitter_status_ids_from_text(text: str) -> set:
    if not text: return set()
    text = html.unescape(str(text)).strip()
    ids = set()
    if re.fullmatch(r"\d{8,25}", text): ids.add(text)
    for match in re.finditer(r"\b(?:id|tweet):(\d{8,25})\b", text, flags=re.I): ids.add(match.group(1))
    for match in re.finditer(r"(?:twitter\.com|x\.com|nitter\.[^/\s]+|/status(?:es)?)/[^\s\"'<>]*?(\d{8,25})", text, flags=re.I): ids.add(match.group(1))
    for match in re.finditer(r"/status(?:es)?/(\d{8,25})", text, flags=re.I): ids.add(match.group(1))
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
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)

def ensure_state_files(published: set):
    if not Path(PUBLISHED_FILE).exists(): save_published(published)
    if not Path(SOURCE_NEWS_FILE).exists(): save_source_news_cache([])

def canonical_url(url: str) -> str:
    if not url: return ""
    url = html.unescape(str(url)).strip()
    try: parsed = urlparse(url)
    except Exception: return url.lower()
    host = parsed.netloc.lower()
    path = unquote(parsed.path or "").rstrip("/")
    if host.startswith("www."): host = host[4:]
    if "nitter." in host or host == "x.com": host = "twitter.com"
    query_pairs = [(k.lower(), v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "igshid", "s", "t"}]
    return urlunparse(("", host, path, "", urlencode(sorted(query_pairs)), "")).lower()

def canonical_media_url(url: str) -> str:
    if not url: return ""
    url = html.unescape(str(url)).strip()
    try: parsed = urlparse(url)
    except Exception: return url.lower()
    host = parsed.netloc.lower()
    path = unquote(parsed.path or "").rstrip("/")
    if host.startswith("www."): host = host[4:]
    if any(marker in host for marker in ("cdninstagram", "fbcdn", "scontent")):
        return urlunparse(("", host, path, "", "", "")).lower()
    return canonical_url(url)

def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:20]

def text_fingerprint(text: str) -> str:
    text = strip_html_tags(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return short_hash(text[:500]) if len(text) >= 20 else ""

def media_fingerprint(media_items: list) -> str:
    urls = sorted(u for u in [canonical_media_url(item.get("url", "")) for item in media_items or [] if item.get("url")] if u)
    return short_hash("|".join(urls[:10])) if urls else ""

def collect_string_values(value, out: list):
    if isinstance(value, dict):
        for nested in value.values(): collect_string_values(nested, out)
    elif isinstance(value, list):
        for nested in value: collect_string_values(nested, out)
    elif isinstance(value, str):
        out.append(value)

def instagram_shortcodes_from_post(post: dict) -> set:
    candidates = []
    collect_string_values(post, candidates)
    shortcodes = set()
    patterns = [r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)"]
    for cap in candidates:
        for pat in patterns:
            for match in re.finditer(pat, cap):
                shortcodes.add(match.group(1).strip().lower())
    return shortcodes

def instagram_extra_duplicate_keys(post: dict) -> set:
    keys = set()
    for f in ("shortCode", "shortcode", "code"):
        sc = str(post.get(f) or "").strip().lower()
        if sc: keys.add(f"ig:{sc}")
    for sc in instagram_shortcodes_from_post(post): keys.add(f"ig:{sc}")
    candidates = []
    collect_string_values(post, candidates)
    for cand in candidates:
        for match in re.finditer(r"https?://[^\s\"'<>]+", html.unescape(cand)):
            url = match.group(0).rstrip(").,;]")
            norm = canonical_url(url)
            if "instagram.com/" in norm: keys.add(f"igurl:{norm}")
    return keys

def duplicate_keys(post_id="", url="", text="", media_items=None, extra_keys=None) -> set:
    keys = {str(k).strip().lower() for k in extra_keys or [] if str(k).strip()}
    if post_id: keys.add(f"id:{canonical_url(post_id) or str(post_id).strip().lower()}")
    if url: keys.add(f"url:{canonical_url(url)}")
    for val in (post_id, url):
        for sid in twitter_status_ids_from_text(val):
            keys.add(f"id:{sid}")
            keys.add(f"tweet:{sid}")
    tk = text_fingerprint(text)
    if tk: keys.add(f"text:{tk}")
    mk = media_fingerprint(media_items)
    if mk: keys.add(f"media:{mk}")
    return {k for k in keys if k and not k.endswith(":")}

def find_duplicate(published: set, **kwargs) -> str:
    pid = str(kwargs.get("post_id") or "").strip()
    url = str(kwargs.get("url") or "").strip()
    if pid and pid in published: return pid
    if url and url in published: return url
    for key in duplicate_keys(**kwargs):
        if key in published: return key
    return ""

def mark_published(published: set, **kwargs):
    published.update(duplicate_keys(**kwargs))

def parse_cache_datetime(value: str) -> datetime | None:
    if not value: return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError: return None

def prune_source_news_cache(records: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SOURCE_NEWS_CACHE_DAYS)
    fresh = [r for r in records if parse_cache_datetime(str(r.get("created_at", ""))) and parse_cache_datetime(str(r.get("created_at", ""))) >= cutoff]
    return fresh[-SOURCE_NEWS_CACHE_LIMIT:]

def load_source_news_cache() -> list:
    if not Path(SOURCE_NEWS_FILE).exists(): return []
    try:
        with open(SOURCE_NEWS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return prune_source_news_cache(data) if isinstance(data, list) else []
    except Exception: return []

def save_source_news_cache(records: list):
    with open(SOURCE_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(prune_source_news_cache(records), f, ensure_ascii=False, indent=2)

def normalize_news_for_similarity(text: str) -> str:
    text = strip_html_tags(text or "")
    text = html.unescape(text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[@#][\w_]+", " ", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()

def news_tokens(text: str) -> set:
    norm = normalize_news_for_similarity(text)
    return {t for t in re.findall(r"[\w-]{3,}", norm) if t not in {"the", "and", "for", "with", "this", "that"}}

def token_similarity(left: str, right: str) -> float:
    l_tokens = news_tokens(left)
    r_tokens = news_tokens(right)
    if not l_tokens or not r_tokens: return 0.0
    common = len(l_tokens & r_tokens)
    return max(common / min(len(l_tokens), len(r_tokens)), common / ((len(l_tokens) * len(r_tokens)) ** 0.5))

def is_semantic_duplicate(text: str, source: str = "", post_id: str = "", url: str = "") -> bool:
    if len(re.findall(r"\w+", text)) < 6: return False
    candidates = []
    curr_url = canonical_url(url)
    for rec in load_source_news_cache():
        if curr_url and canonical_url(rec.get("url", "")) == curr_url: continue
        score = token_similarity(text, rec.get("text", ""))
        if score >= SEMANTIC_AI_PREFILTER_THRESHOLD: candidates.append((score, rec))
    if not candidates: return False
    best_score, best_rec = max(candidates, key=lambda x: x[0])
    if best_score >= SEMANTIC_LOCAL_DUPLICATE_THRESHOLD: return True
    if not OPENROUTER_KEY: return False
    prev = [{"score": round(sc, 3), "source": r.get("source", ""), "text": r.get("text", "")[:800]} for sc, r in candidates[:3]]
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": f"You check football news duplicates.\nReturn ONLY valid JSON: {{\"duplicate\": true|false}}\n\nNEW:\n{text[:1200]}\n\nPREV:\n{json.dumps(prev, ensure_ascii=False)}"}], "temperature": 0}, timeout=15)
        data = json.loads(re.search(r"\{.*\}", r.json()["choices"][0]["message"]["content"].strip(), flags=re.S).group(0))
        return bool(data.get("duplicate"))
    except Exception: return False

def record_source_news(text: str, step: int, source: str = "", post_id: str = "", url: str = ""):
    if len(re.findall(r"\w+", text)) < 6: return
    cache = load_source_news_cache()
    key = short_hash(canonical_url(url) or canonical_url(post_id) or text_fingerprint(text) or text[:250])
    if not any(r.get("key") == key for r in cache):
        cache.append({"key": key, "created_at": datetime.now(timezone.utc).isoformat(), "step": step, "source": source, "post_id": str(post_id), "url": str(url), "text": normalize_news_for_similarity(text)[:1500]})
        save_source_news_cache(cache)

def is_too_old(date_str: str) -> bool:
    if not date_str: return False
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt) if "%" in fmt else datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if not dt.tzinfo: dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
        except ValueError: continue
    return False

# --- ТЕКСТОВЫЕ КОРРЕКТОРЫ И HTML-ВЕРСТКА ---

def strip_decorative_pipes(text: str) -> str:
    text = re.sub(r"([🚨❗🔥📢❓🎙🗣])\s*\|\s*", r"\1 ", text or "")
    return re.sub(r"\s*\|\s*(?=\s|$)", " ", text).strip()

def strip_follow_us_tail(text: str) -> str:
    text = re.sub(r"\s*\|\s*(?:Follow us|Подпишись)\s*(?=\n|$)", "", text or "", flags=re.I)
    return re.sub(r"\s*(?:Follow us|Подпишись)\s*(?=\n|$)", "", text or "", flags=re.I)

def normalize_source_brackets(text: str) -> str:
    text = re.sub(r"🚨\s*\|\s*\[\s*-\s*([^\]]+?)\s*\]", r"🚨 [\1]", text or "")
    def fix(match: re.Match) -> str:
        inner = re.sub(r"^[-–—]\s*", "", match.group(1).strip())
        return f"[{inner}]" if inner else "[]"
    text = re.sub(r"\[\s*([^\]]+?)\s*\]", fix, text)
    return re.sub(r"(🚨\s*\[[A-Za-z0-9_.-]+\])\s*\n*", r"\1\n\n", text)

def strip_noise_only_lines(text: str) -> str:
    lines = [l for l in (text or "").splitlines() if l.strip() and not re.fullmatch(r"[.\"'“”«»`´·•…]+", l.strip())]
    return re.sub(r"(?:\n\s*){3,}", "\n\n", "\n".join(lines)).strip()

def strip_unwanted_source_tags(text: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text or "", flags=re.I)
    text = re.sub(r"</\s*(?:p|div|li|tr)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*(?:p|div|li|tr|td|span|strong|em|i|u|ul|ol)\b[^>]*>", "", text, flags=re.I)
    return re.sub(r"<(?!/?(?:b|blockquote)\s*>)[^>]+>", "", text)

def clean_text(text: str) -> str:
    text = strip_unwanted_source_tags(text or "")
    text = re.sub(r"https?://(?:nitter|t\.co|twitter|x|instagram)\.[^\s]+", "", text)
    text = text.replace("@", "-")
    paras = text.split("\n\n")
    unique = []
    seen = set()
    for p in paras:
        p_clean = preprocess_post_text(p.strip())
        if p_clean.lower() in {"video", "photo", "pic", "видео", "фото"}: continue
        key = p_clean.lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(p_clean)
    return "\n\n".join(unique).strip()

def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")

QUOTE_PAIRS = {'"': '"', "«": "»", "“": "”", "„": "“"}

def normalize_angle_quotes(text: str) -> str:
    text = re.sub(r'"([^"\n]+)"', r"«\1»", text or "")
    text = re.sub(r"“([^”\n]+)”", r"«\1»", text)
    text = re.sub(r"„([^“\n]+)“", r"«\1»", text)
    return text

def word_count(text: str) -> int:
    return len(re.findall(r"[\w-]+", text))

def speaker_name_from_heading(heading: str) -> str:
    return re.sub(r"^\s*[🗣🎙]️?\s*:?\s*", "", strip_html_tags(heading or "")).strip(" *:")

def looks_like_speaker_heading(heading: str) -> bool:
    plain = strip_html_tags(heading or "").strip()
    name = speaker_name_from_heading(plain)
    if not name or "🗣" in plain or "🎙" in plain: return True
    if re.search(r"\d|https?://", name): return False
    words = re.findall(r"\w+", name)
    return 1 <= len(words) <= 5 and name[0].isupper()

def render_colon_heading(heading: str) -> str:
    name = speaker_name_from_heading(heading)
    return f"🎙️ <b>{html.escape(name, quote=False)}</b>:"

def should_blockquote_quote(text: str, speaker_context: bool = False) -> bool:
    return word_count(text) >= (1 if speaker_context else 3)

def lowercase_continuation_start(text: str) -> str:
    if not text or text.strip().startswith(("#", "@", "http", "«", "\"")): return text
    def replace(match: re.Match) -> str:
        pref, word = match.group(1), match.group(2)
        return pref + word[:1].lower() + word[1:] if not word.isupper() else match.group(0)
    return re.sub(r"^(\s*[^А-Яа-яA-Za-z#@<«\"]*)([А-ЯЁA-Z][а-яёa-z]*)", replace, text, count=1)

def split_quoted_segments(line: str) -> list:
    segments, buf, i = [], [], 0
    while i < len(line):
        op = line[i]
        cl = QUOTE_PAIRS.get(op)
        if cl:
            end = line.find(cl, i + 1)
            if end != -1:
                if buf: segments.append(("text", "".join(buf)))
                buf = []
                q = line[i + 1:end].strip()
                if q: segments.append(("quote", q))
                i = end + 1
                continue
        buf.append(op)
        i += 1
    if buf: segments.append(("text", "".join(buf)))
    return segments

def split_colon_heading(line: str) -> tuple[str, str]:
    if ":" not in line: return "", ""
    b, a = line.split(":", 1)
    return b.strip(), a.strip()

def join_orphan_lines(text: str) -> str:
    lines = (text or "").split("\n")
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            if merged and merged[-1] != "": merged.append("")
            i += 1
            continue
        while i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if not nxt or nxt.startswith(("#", "✅", "☑", "•", "❓")): break
            if ":" in line and looks_like_speaker_heading(split_colon_heading(line)[0] or line): break
            ends_orphan = line and (line.split()[-1].lower() in {"в", "на", "за", "с", "и", "но", "или"} or not re.search(r"[.!?…:»\"]$", line))
            if ends_orphan and (nxt[:1].islower() or len(nxt.split()) <= 4):
                line = f"{line} {nxt}"
                i += 1
                continue
            break
        merged.append(line)
        i += 1
    return "\n".join(merged)

def merge_country_player_list_lines(text: str) -> str:
    lines = (text or "").split("\n")
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if i + 1 < len(lines) and re.search(r"[—–]\s*$", line):
            player = extract_quote_text(lines[i + 1])
            if player and len(player.split()) <= 6:
                merged.append(f"{line.rstrip('—– ')} — {player}")
                i += 2
                continue
        merged.append(line)
        i += 1
    return "\n".join(merged)

def looks_like_country_player_item(line: str) -> bool:
    plain = strip_html_tags(line).strip()
    return bool(re.search(r"(—|–)", plain) and len(plain.split("—")[0].split()) <= 6)

def extract_quote_text(text: str) -> str:
    match = re.search(r"«([^»]+)»", text or "")
    if match: return match.group(1).strip()
    match = re.search(r'"([^"]+)"', text or "")
    if match: return match.group(1).strip()
    return (text or "").strip("«»\"' ")

def normalize_paragraph_breaks(text: str) -> str:
    lines = (text or "").split("\n")
    res = []
    for i, line in enumerate(lines):
        l_strip = line.strip()
        if not l_strip:
            if res and res[-1] != "": res.append("")
            continue
        res.append(l_strip)
        if i + 1 < len(lines):
            nxt = lines[i+1].strip()
            if nxt and re.search(r'[.!?…]["»\s]*$', l_strip) and nxt[:1].isupper():
                if res[-1] != "": res.append("")
    return "\n".join(res)

def ensure_hashtag_spacing(text: str) -> str:
    lines = (text or "").split("\n")
    out = []
    for l in lines:
        if l.strip().startswith("#") and out and out[-1] != "": out.append("")
        out.append(l)
    return "\n".join(out).strip()

def preprocess_post_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = strip_unwanted_source_tags(text)
    text = strip_noise_only_lines(text)
    text = strip_decorative_pipes(text)
    text = strip_follow_us_tail(text)
    text = normalize_source_brackets(text)
    text = join_orphan_lines(text)
    text = merge_country_player_list_lines(text)
    text = normalize_paragraph_breaks(text)
    return text.strip()

def clean_interview_question_line(line: str) -> str:
    line = re.sub(r"[🚨❗🔥📢🎙🗣❓]+", "", strip_html_tags(line))
    return f"❓ {line.strip()}"

def try_format_interview_qa(text: str) -> str | None:
    text = strip_html_tags(text or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 2: return None
    hashtags = []
    while lines and lines[-1].startswith("#"): hashtags.insert(0, lines.pop())
    if len(lines) < 2: return None
    
    question = clean_interview_question_line(lines[0])
    if "?" not in question: return None
    
    speaker_line = lines[1]
    sp_head, sp_tail = split_colon_heading(speaker_line)
    if not sp_head or not looks_like_speaker_heading(sp_head): return None
    
    quote = extract_quote_text(" ".join([sp_tail] + lines[2:]).strip())
    if not quote or len(quote.split()) < 2: return None
    
    blocks = [
        f"❓ <b>{html.escape(question.lstrip('❓ ').strip(), quote=False)}</b>",
        "",
        render_colon_heading(sp_head),
        f"<blockquote>«{html.escape(quote, quote=False)}»</blockquote>"
    ]
    if hashtags: blocks.extend(["", "\n".join(hashtags)])
    return "\n".join(blocks).strip()

def render_telegram_line(line: str, bold_line: bool, apply_colon_rule: bool = True, speaker_context: bool = False) -> list:
    if apply_colon_rule:
        h, b = split_colon_heading(line)
        if h:
            h_is_sp = looks_like_speaker_heading(h)
            rendered = [render_colon_heading(h)]
            if b: rendered.extend(render_telegram_line(b, False, apply_colon_rule=False, speaker_context=speaker_context or h_is_sp))
            return rendered
    rendered = []
    for k, v in split_quoted_segments(line):
        v = v.strip()
        if not v: continue
        escaped = html.escape(v, quote=False)
        if k == "quote":
            if looks_like_country_player_item(line): rendered.append(escaped)
            elif should_blockquote_quote(v, speaker_context): rendered.append(f"<blockquote>«{escaped}»</blockquote>")
            else: rendered.append(f"«{escaped}»")
        elif bold_line: rendered.append(f"<b>{escaped}</b>")
        else: rendered.append(escaped)
    return rendered

def make_telegram_html(text: str, bold_first_line: bool = True) -> str:
    text = strip_html_tags(text).replace("@", "-").strip()
    text = preprocess_post_text(text)
    interview = try_format_interview_qa(text)
    if interview: return ensure_hashtag_spacing(interview)
    
    output, first, sp_ctx = [], True, False
    for rl in text.splitlines():
        line = rl.strip()
        if not line:
            if output and output[-1] != "": output.append("")
            continue
        output.extend(render_telegram_line(line, bool(bold_first_line and first), speaker_context=sp_ctx))
        first = False
        sp_ctx = looks_like_speaker_heading(split_colon_heading(line)[0] or line)
    return ensure_hashtag_spacing("\n".join(output).strip())

def make_instagram_telegram_html(caption: str) -> str:
    caption = strip_html_tags(caption or "").replace("@", "-").strip()
    return f"<b>{INSTAGRAM_PREFIX}</b>\n{make_telegram_html(caption, bold_first_line=False)}" if caption else f"<b>{INSTAGRAM_PREFIX}</b>"

# --- ИСПРАВЛЕНИЯ ВЕРСТКИ (LOCAL & AI) ---

def local_layout_repair(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # 1. Clean introductory news phrases
    text = strip_intro_phrases(text)
    # 2. Keep only the signature-related hashtags, stripping other hashtags
    text = strip_unwanted_hashtags(text)
    # 3. Strip unwanted emojis
    text = strip_unwanted_emojis(text)
    # 4. Correct split times/scores and pull back orphaned dots
    text = fix_split_time_and_photos(text)
    # 5. Fix titles/headers formatting
    text = fix_extra_hashtags_and_titles(text)
    
    plain = preprocess_post_text(strip_html_tags(text))
    interview = try_format_interview_qa(plain)
    if interview: return ensure_hashtag_spacing(interview)
    
    plain = handle_inline_speaker_quotes(plain)
    plain = normalize_stats_lists(plain)
    
    repaired, sp_mode, sp_buf = [], False, []
    def flush():
        nonlocal sp_mode, sp_buf
        if not sp_buf: return
        t_block = "\n".join(sp_buf).strip()
        sp_buf, sp_mode = [], False
        # Разбиваем на blockquote с абзацами
        paras = [p.strip() for p in t_block.split("\n\n") if p.strip()]
        quoted = "\n\n".join(p if "«" in p else f"«{p}»" for p in paras)
        repaired.append(f"<blockquote>{html.escape(quoted, quote=False)}</blockquote>")

    for line in plain.splitlines():
        l_strip = line.strip()
        if not l_strip:
            flush()
            if repaired and repaired[-1] != "": repaired.append("")
            continue
            
        if sp_mode:
            if l_strip.startswith(("#", "❓")) or (":" in l_strip and looks_like_speaker_heading(split_colon_heading(l_strip)[0])):
                flush()
            else:
                sp_buf.append(l_strip)
                continue
                
        if ":" in l_strip and "<b>" not in l_strip.lower() and not re.search(r"https?://", l_strip):
            h, b = split_colon_heading(strip_html_tags(l_strip))
            if h:
                repaired.append(render_colon_heading(h))
                if not b and looks_like_speaker_heading(h):
                    sp_mode, sp_buf = True, []
                elif b:
                    repaired.append(html.escape(b, quote=False))
                continue
                
        if looks_like_country_player_item(l_strip):
            left, _, right = re.split(r"(—|–)", l_strip, maxsplit=1)
            player = extract_quote_text(right)
            if player: repaired.append(f"{left.strip()} — {player}")
            continue
            
        repaired.append(l_strip)
    flush()
    
    res = strip_excessive_bold("\n".join(repaired).strip())
    return ensure_hashtag_spacing(normalize_angle_quotes(res))

def ai_check_telegram_layout(full_text: str) -> str:
    locally_repaired = local_layout_repair(full_text)
    rag_candidate = rag_check_telegram_layout(locally_repaired)
    if rag_candidate != locally_repaired: locally_repaired = rag_candidate
    
    if not GEMINI_API_KEY or len(locally_repaired) > LAYOUT_AI_MAX_CHARS:
        return locally_repaired

    prompt = (
        "Поправь только расстановку строк и абзацев в новости для Telegram. Не переписывай слова!\n"
        "Правила:\n"
        "1. Вопрос-ответ: строка ❓ <b>Вопрос?</b>, пустая строка, далее 🎙️ <b>Имя</b>:, далее на новой строке цитата в blockquote <blockquote>«цитата»</blockquote>.\n"
        "2. Статистика: каждый показатель с эмодзи строго с новой строки.\n"
        "3. Длинные цитаты делай с абзацами внутри blockquote.\n"
        "4. Кавычки всегда ёлочками «».\n"
        "5. Выведи строго только готовый HTML-текст (разрешены лишь <b>, <blockquote>).\n\n"
        f"ТЕКСТ:\n{locally_repaired}"
    )
    try:
        r = requests.post(f"{GEMINI_BASE_URL}/models/{LAYOUT_AI_MODEL}:generateContent", params={"key": GEMINI_API_KEY}, headers={"Content-Type": "application/json"}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0, "maxOutputTokens": 1400}}, timeout=LAYOUT_AI_TIMEOUT)
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        cleaned = local_layout_repair(strip_unwanted_source_tags(cand))
        if cleaned and telegram_html_tags_balanced(cleaned) and content_preserved(locally_repaired, cleaned):
            return cleaned
    except Exception: pass
    return locally_repaired

def rag_check_telegram_layout(text: str) -> str:
    # RAG module is offline / bypassed as requested because of usage limits
    return text

def telegram_html_tags_balanced(text: str) -> bool:
    stack = []
    for m in re.finditer(r"<\s*(/?)\s*(b|blockquote)(?:\s+[^>]*)?>", text or "", re.I):
        if m.group(1):
            if not stack or stack[-1] != m.group(2).lower(): return False
            stack.pop()
        else: stack.append(m.group(2).lower())
    return len(stack) == 0

def content_preserved(original: str, candidate: str) -> bool:
    o_plain = strip_html_tags(original).strip()
    c_plain = strip_html_tags(candidate).strip()
    if not o_plain or not c_plain: return False
    return 0.7 <= (len(c_plain) / len(o_plain)) <= 1.35 and len(news_tokens(o_plain) & news_tokens(c_plain)) / max(1, len(news_tokens(o_plain))) >= 0.7

# --- FETCH & DOWNLOADS ---

def fetch_rss_items(url: str) -> list:
    try:
        r = requests.get(url, timeout=15, headers=REQUEST_HEADERS)
        if "json" in r.headers.get("Content-Type", "") or url.endswith(".json"):
            return r.json().get("items", [])
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        items = []
        for el in root.iter("item"):
            def t(tag): return (el.findtext(tag) or "").strip()
            ch = t("{http://purl.org/rss/1.0/modules/content/}encoded") or t("description")
            items.append({"id": t("guid") or t("link"), "url": t("link"), "title": t("title"), "content_text": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ch)).strip(), "content_html": ch, "date_published": t("pubDate"), "image": ""})
        return items
    except Exception: return []

def download_media(url: str, media_type: str, index: int) -> Path | None:
    MEDIA_DIR.mkdir(exist_ok=True)
    try:
        r = requests.get(url, timeout=45, headers=REQUEST_HEADERS, stream=True, allow_redirects=True)
        r.raise_for_status()
        ext = ".mp4" if media_type == "video" else (".png" if "png" in r.headers.get("Content-Type", "") else ".jpg")
        path = MEDIA_DIR / f"med_{int(time.time())}_{index}{ext}"
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk: f.write(chunk)
        if path.stat().st_size < 1024:
            path.unlink()
            return None
        return path
    except Exception: return None

# --- TELEGRAM SENDING ---

def split_caption(full_text: str, has_media: bool) -> tuple:
    limit = (CAPTION_LIMIT if has_media else MSG_LIMIT) - len(SIGNATURE) - 10
    if len(full_text) <= limit: return full_text + SIGNATURE, None
    cut = 0
    for sep in ("\n\n", "\n", " "):
        probe = full_text.rfind(sep, 0, limit)
        while probe > 0:
            if telegram_html_tags_balanced(full_text[:probe]) and telegram_html_tags_balanced(full_text[probe:]):
                cut = probe
                break
            probe = full_text.rfind(sep, 0, probe)
        if cut: break
    if not cut: cut = limit
    return full_text[:cut].strip() + SIGNATURE, full_text[cut:].strip() + SIGNATURE

def tg_post(method: str, payload: dict) -> dict:
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=25)
        return r.json()
    except Exception as e: return {"ok": False, "description": str(e)}

def crosspost(text: str):
    p_text = text.replace(SIGNATURE, CROSSPOST_SIGNATURE)
    p_text = re.sub(r"[ \t]+\n", "\n", strip_html_tags(html.unescape(p_text)))
    if VK_ACCESS_TOKEN and VK_OWNER_ID:
        try: requests.post("https://api.vk.com/method/wall.post", data={"access_token": VK_ACCESS_TOKEN, "v": VK_API_VERSION, "owner_id": VK_OWNER_ID, "from_group": VK_FROM_GROUP, "message": p_text}, timeout=20)
        except Exception: pass
    if MAX_TOKEN and (MAX_CHAT_ID or MAX_USER_ID):
        try: requests.post("https://platform-api.max.ru/messages", params={"chat_id": MAX_CHAT_ID} if MAX_CHAT_ID else {"user_id": MAX_USER_ID}, headers={"Authorization": MAX_TOKEN, "Content-Type": "application/json"}, json={"text": p_text[:MAX_TEXT_LIMIT], "notify": True}, timeout=20)
        except Exception: pass

def send_to_telegram(media_items: list, full_text: str, step4_custom_sig: bool = False):
    if not media_items: return None
    downloaded = []
    for i, item in enumerate(media_items):
        p = download_media(item["url"], item["type"], i)
        if p: downloaded.append({"path": p, "type": item["type"]})
    if not downloaded: return None
    
    final_text = full_text if step4_custom_sig else ai_check_telegram_layout(full_text)
    caption, overflow = split_caption(final_text, True)
    
    chunks = [downloaded[i:i + TELEGRAM_MEDIA_GROUP_LIMIT] for i in range(0, len(downloaded), TELEGRAM_MEDIA_GROUP_LIMIT)]
    sent_ok = True
    
    for idx, chunk in enumerate(chunks):
        c_cap = caption if idx == 0 else ""
        if len(chunk) == 1:
            method = "sendVideo" if chunk[0]["type"] == "video" else "sendPhoto"
            field = "video" if chunk[0]["type"] == "video" else "photo"
            try:
                with open(chunk[0]["path"], "rb") as f:
                    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", data={"chat_id": CHANNEL_ID, "caption": c_cap, "parse_mode": "HTML"}, files={field: f}, timeout=90).json()
                    if not r.get("ok"): sent_ok = False
            except Exception: sent_ok = False
        else:
            files = {f"file{i}": open(item["path"], "rb") for i, item in enumerate(chunk)}
            media_arr = [{"type": item["type"], "media": f"attach://file{i}"} for i, item in enumerate(chunk)]
            if media_arr:
                media_arr[0]["caption"] = c_cap
                media_arr[0]["parse_mode"] = "HTML"
            try:
                r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup", data={"chat_id": CHANNEL_ID, "media": json.dumps(media_arr)}, files=files, timeout=120).json()
                if not r.get("ok"): sent_ok = False
            except Exception: sent_ok = False
            finally:
                for f in files.values(): f.close()
                
        if not sent_ok: break
        if idx < len(chunks) - 1: time.sleep(1.5)
        
    if overflow and sent_ok:
        time.sleep(1.5)
        tg_post("sendMessage", {"chat_id": CHANNEL_ID, "text": overflow, "parse_mode": "HTML"})
        
    if sent_ok: crosspost(final_text)
    shutil.rmtree(MEDIA_DIR, ignore_errors=True)
    return sent_ok

# --- NITTER & SCWEET ---

def fetch_scweet_tweets(keywords, from_accounts=None, limit=50) -> list:
    if not SCWEET_AUTH_TOKEN: return []
    try:
        from Scweet import Scweet as ScweetClient
    except ImportError: return []
    try:
        kwargs = {"auth_token": SCWEET_AUTH_TOKEN}
        if SCWEET_PROXY: kwargs["proxy"] = SCWEET_PROXY
        s = ScweetClient(**kwargs)
    except Exception: return []
    
    q_parts = []
    if keywords:
        q_parts.append(f"({' OR '.join(keywords)})")
    if from_accounts:
        q_parts.append(f"({' OR '.join(f'from:{acc}' for acc in from_accounts)})")
    q_parts.extend(["filter:media", "-filter:retweets", "-filter:replies"])
    query = " ".join(q_parts).strip()
    
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=48)).strftime("%Y-%m-%d")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        tweets = s.search(query, since=since, until=until, display_type="Latest", limit=limit, save=False)
    except Exception: return []
    
    results = []
    items = tweets.values() if isinstance(tweets, dict) else tweets
    for tw in items:
        if isinstance(tw, list): tw = tw[0] if tw else {}
        if not isinstance(tw, dict): continue
        
        post_id = str(tw.get("tweet_id") or tw.get("id") or "")
        url = str(tw.get("tweet_url") or "")
        raw = str(tw.get("text") or tw.get("full_text") or "")
        pub = str(tw.get("timestamp") or tw.get("created_at") or "")
        
        # Проверим репосты
        is_repost = False
        for flag in ("is_retweet", "isretweet", "retweeted", "retweeted_status"):
            if tw.get(flag): is_repost = True
        if is_repost: continue
        
        # Извлекаем медиа
        media_items = []
        m_block = tw.get("media") or {}
        for img in m_block.get("image_links", []):
            media_items.append({"url": img, "type": "photo"})
        
        raw_graph = tw.get("raw", {})
        if isinstance(raw_graph, dict):
            ext = raw_graph.get("legacy", {}).get("extended_entities", {})
            for m_obj in ext.get("media", []):
                if m_obj.get("type") in ("video", "animated_gif"):
                    vars_list = (m_obj.get("video_info") or {}).get("variants") or []
                    video_vars = [v for v in vars_list if "video" in str(v.get("content_type"))]
                    if video_vars:
                        best = max(video_vars, key=lambda v: int(v.get("bitrate") or 0))
                        media_items = [{"url": best["url"], "type": "video"}]
                        break
                        
        if not media_items: continue
        results.append({"post_id": post_id, "post_url": url, "author": tw.get("user_screen_name", "twitter"), "raw": raw, "pub_date": pub, "media_items": media_items})
    return results

# --- ШАГИ БОТА ---

def step1_twitter_keywords(published: set):
    print("\n══════ ШАГ 1: Twitter по ключевым словам (Scweet) ══════")
    if not STEP1_KEYWORDS or not SCWEET_AUTH_TOKEN: return published
    tweets = fetch_scweet_tweets(keywords=STEP1_KEYWORDS, from_accounts=STEP1_ACCOUNTS or None, limit=STEP1_LIMIT)
    new_count = 0
    for tw in tweets:
        pid, r_caption = tw["post_id"], clean_text(tw["raw"])
        if not r_caption or find_duplicate(published, post_id=pid, url=tw["post_url"]): continue
        if is_too_old(tw["pub_date"]): continue
        if is_semantic_duplicate(r_caption, source="step1_twitter", post_id=pid, url=tw["post_url"]): continue
        
        edited = edit_openrouter(translate_deepl(r_caption))
        full = make_telegram_html(edited, bold_first_line=False)
        if send_to_telegram(tw["media_items"], full):
            mark_published(published, post_id=pid, url=tw["post_url"], text=r_caption, media_items=tw["media_items"])
            save_published(published)
            record_source_news(r_caption, step=1, source="step1_twitter", post_id=pid, url=tw["post_url"])
            new_count += 1
    print(f"  Итого опубликовано: {new_count}")
    return published

# --- INSTAGRAM APIFY ---

def fetch_instagram_posts() -> list:
    if not APIFY_TOKEN or (not APIFY_INSTAGRAM_ACCOUNTS and not APIFY_INSTAGRAM_INPUT_JSON): return []
    usernames = [a.lstrip("@").strip("/") for a in APIFY_INSTAGRAM_ACCOUNTS if not a.startswith("http")]
    actor_input = {"username": usernames, "resultsLimit": APIFY_INSTAGRAM_RESULTS_LIMIT, "skipPinnedPosts": APIFY_INSTAGRAM_SKIP_PINNED}
    if APIFY_INSTAGRAM_ONLY_NEWER_THAN: actor_input["onlyPostsNewerThan"] = APIFY_INSTAGRAM_ONLY_NEWER_THAN
    if APIFY_INSTAGRAM_INPUT_JSON:
        try: actor_input.update(json.loads(APIFY_INSTAGRAM_INPUT_JSON))
        except Exception: pass
        
    url = f"{APIFY_BASE_URL}/acts/{APIFY_INSTAGRAM_ACTOR}/run-sync-get-dataset-items"
    params = {"format": "json", "clean": "1", "timeout": str(APIFY_RUN_TIMEOUT)}
    try:
        r = requests.post(url, params=params, json=actor_input, headers={"Authorization": f"Bearer {APIFY_TOKEN}", "Content-Type": "application/json"}, timeout=APIFY_RUN_TIMEOUT + 30)
        return r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    except Exception: return []

def instagram_media_from_apify_post(post: dict) -> list:
    media = []
    seen = set()
    def add(u, t):
        if u and not u.startswith("data:") and "instagram.com" not in u:
            k = canonical_media_url(u)
            if k not in seen:
                seen.add(k)
                media.append({"url": u, "type": t})
                
    def extract(node):
        if not node or not isinstance(node, dict): return
        for child in (node.get("childPosts") or node.get("children") or node.get("sidecarChildren") or []):
            if isinstance(child, dict): extract(child)
        for vk in ("videoUrl", "video_url", "video"):
            if node.get(vk): add(node[vk], "video")
        for ik in ("displayUrl", "display_url", "imageUrl", "image_url", "mediaUrl"):
            if node.get(ik): add(node[ik], "photo")
            
    extract(post)
    return media

def step2_instagram(published: set):
    print("\n══════ ШАГ 2: Instagram (Apify) ══════")
    posts = fetch_instagram_posts()
    new_count = 0
    for post in posts[:10]:
        pid = str(post.get("id") or post.get("shortCode") or "")
        purl = post.get("url") or f"https://www.instagram.com/p/{post.get('shortCode')}/" if post.get("shortCode") else ""
        ig_keys = instagram_extra_duplicate_keys(post)
        if not pid or find_duplicate(published, post_id=pid, url=purl, extra_keys=ig_keys): continue
        if is_too_old(post.get("timestamp") or ""): continue
        
        media = instagram_media_from_apify_post(post)
        if not media: continue
        caption = clean_text(post.get("caption") or "")
        if caption and is_semantic_duplicate(caption, source="instagram", post_id=pid, url=purl): continue
        
        full_text = make_instagram_telegram_html(translate_deepl(caption)) if caption else make_instagram_telegram_html("")
        if send_to_telegram(media, full_text):
            mark_published(published, post_id=pid, url=purl, text=caption, media_items=media, extra_keys=ig_keys)
            save_published(published)
            record_source_news(caption, step=2, source="instagram", post_id=pid, url=purl)
            new_count += 1
    print(f"  Итого опубликовано: {new_count}")
    return published

# --- STEP 3: TWITTER ACCOUNTS ---

def step3_twitter_accounts(published: set):
    print("\n══════ ШАГ 3: Twitter-аккаунты (Scweet) ══════")
    if not STEP3_ACCOUNTS or not SCWEET_AUTH_TOKEN: return published
    tweets = fetch_scweet_tweets(keywords=[], from_accounts=STEP3_ACCOUNTS, limit=STEP3_LIMIT)
    new_count = 0
    for tw in tweets:
        pid, r_caption = tw["post_id"], clean_text(tw["raw"])
        if not r_caption or find_duplicate(published, post_id=pid, url=tw["post_url"]): continue
        if is_too_old(tw["pub_date"]): continue
        if is_semantic_duplicate(r_caption, source="step3_twitter", post_id=pid, url=tw["post_url"]): continue
        
        edited = edit_openrouter(translate_deepl(r_caption))
        full = make_telegram_html(edited, bold_first_line=False)
        if send_to_telegram(tw["media_items"], full):
            mark_published(published, post_id=pid, url=tw["post_url"], text=r_caption, media_items=tw["media_items"])
            save_published(published)
            record_source_news(r_caption, step=3, source="step3_twitter", post_id=pid, url=tw["post_url"])
            new_count += 1
    print(f"  Итого опубликовано: {new_count}")
    return published

# --- STEP 4: HASHTAGS ---

def fetch_hashtag_posts(hashtag: str) -> list:
    if not APIFY_TOKEN: return []
    actor_input = {"hashtags": [hashtag], "resultsLimit": APIFY_HASHTAG_RESULTS_LIMIT}
    if APIFY_HASHTAG_ONLY_NEWER_THAN: actor_input["onlyPostsNewerThan"] = APIFY_HASHTAG_ONLY_NEWER_THAN
    
    url = f"{APIFY_BASE_URL}/acts/{APIFY_HASHTAG_ACTOR}/run-sync-get-dataset-items"
    try:
        r = requests.post(url, params={"format": "json", "clean": "1", "timeout": str(APIFY_RUN_TIMEOUT)}, json=actor_input, headers={"Authorization": f"Bearer {APIFY_TOKEN}", "Content-Type": "application/json"}, timeout=APIFY_RUN_TIMEOUT + 30)
        return r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    except Exception: return []

def step4_instagram_hashtags(published: set):
    print("\n══════ ШАГ 4: Instagram Hashtags ══════")
    if not STEP4_HASHTAGS or not APIFY_TOKEN: return published
    new_count = 0
    for tag in STEP4_HASHTAGS:
        posts = fetch_hashtag_posts(tag)
        for post in posts[:APIFY_HASHTAG_RESULTS_LIMIT]:
            owner = str(post.get("ownerUsername") or post.get("authorUsername") or post.get("user", {}).get("username") or "").strip().lower()
            if STEP4_ALLOWED_ACCOUNTS and owner not in STEP4_ALLOWED_ACCOUNTS: continue
            
            pid = str(post.get("id") or post.get("shortCode") or "")
            purl = post.get("url") or f"https://www.instagram.com/p/{post.get('shortCode')}/" if post.get("shortCode") else ""
            ig_keys = instagram_extra_duplicate_keys(post)
            if not pid or find_duplicate(published, post_id=pid, url=purl, extra_keys=ig_keys): continue
            
            media = instagram_media_from_apify_post(post)
            if not media: continue
            caption = clean_text(post.get("caption") or "")
            if caption and is_semantic_duplicate(caption, source=f"hashtag_{tag}", post_id=pid, url=purl): continue
            
            translated = translate_deepl(caption) if caption else ""
            header = f"<b>{STEP4_PREFIX}</b>" if STEP4_PREFIX else f"<b>#{tag}</b>"
            body = f"{header}\n{make_telegram_html(translated, bold_first_line=False)}" if translated else header
            sig = f"\n\n{STEP4_SIGNATURE}" if STEP4_SIGNATURE else ""
            full = ai_check_telegram_layout(body + sig)
            
            if send_to_telegram(media, full, step4_custom_sig=True):
                mark_published(published, post_id=pid, url=purl, text=caption, media_items=media, extra_keys=ig_keys)
                save_published(published)
                record_source_news(caption, step=4, source=f"hashtag_{tag}", post_id=pid, url=purl)
                new_count += 1
    print(f"  Итого опубликовано: {new_count}")
    return published

# --- TRANSLATE & OPENROUTER ---

def translate_deepl(text: str) -> str:
    if not text or not DEEPL_KEY: return text
    try:
        r = requests.post("https://api-free.deepl.com/v2/translate", headers={"Authorization": f"DeepL-Auth-Key {DEEPL_KEY}", "Content-Type": "application/json"}, json={"text": [text], "target_lang": "RU"}, timeout=15)
        return r.json()["translations"][0]["text"].strip()
    except Exception: return text

def edit_openrouter(text: str) -> str:
    if not text or not OPENROUTER_KEY: return text
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
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}, json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.5}, timeout=20)
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception: return text

# --- MAIN ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, required=True, choices=[1, 2, 3, 4])
    args = parser.parse_args()
    print(f"▶ Шаг {args.step} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    
    published = load_published()
    print(f"  Загружено ID в кэше: {len(published)}")
    if REQUIRE_PUBLISHED_CACHE and not Path(PUBLISHED_FILE).exists():
        print("  published_ids.json не найден — запуск пропущен")
        return
        
    if args.step == 1: step1_twitter_keywords(published)
    elif args.step == 2: step2_instagram(published)
    elif args.step == 3: step3_twitter_accounts(published)
    elif args.step == 4: step4_instagram_hashtags(published)
    
    ensure_state_files(published)
    print("✓ Готово.")

if __name__ == "__main__":
    main()