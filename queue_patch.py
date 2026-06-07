"""
queue_patch.py — Модуль очереди публикаций

Вставить в bot.py после раздела «НАСТРОЙКИ» (после строки с PUBLISHED_FILE = ...).

Логика:
- Шаги 1–4 публикуют максимум MAIN_POST_LIMIT постов (по умолчанию 2).
  Остальные прошедшие проверку посты кладутся в queue.json.
- При добавлении в очередь пост немедленно помечается в published_ids.json,
  чтобы следующий запуск шагов 1–4 не публиковал его повторно.
- Шаг 5 (--step 5) запускается планировщиком каждые 30 минут.
  Он извлекает до QUEUE_BATCH_SIZE постов (по умолчанию 2) из очереди
  и публикует каждый, применяя полный pipeline (перевод → редактура → верстка).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# ────────────────────────────────────────────────────────────
# НАСТРОЙКИ ОЧЕРЕДИ
# ────────────────────────────────────────────────────────────

QUEUE_FILE = "queue.json"

# Максимум постов, которые шаги 1–4 публикуют немедленно.
# Всё что сверх — уходит в очередь.
MAIN_POST_LIMIT = int((os.environ.get("MAIN_POST_LIMIT") or "2").strip())

# Максимум постов, которые шаг 5 публикует за один запуск.
QUEUE_BATCH_SIZE = int((os.environ.get("QUEUE_BATCH_SIZE") or "2").strip())


# ────────────────────────────────────────────────────────────
# ОПЕРАЦИИ С ОЧЕРЕДЬЮ
# ────────────────────────────────────────────────────────────

def load_queue() -> list:
    """Загрузить очередь из queue.json. Возвращает список dict."""
    path = Path(QUEUE_FILE)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  Не удалось прочитать {QUEUE_FILE}: {e}")
        return []


def save_queue(queue: list):
    """Сохранить очередь в queue.json."""
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def enqueue_post(
    published: set,
    *,
    step: int,
    post_id: str = "",
    post_url: str = "",
    raw_text: str = "",
    translated: str = "",
    edited: str = "",
    full_text: str = "",
    media_items: list | None = None,
    source: str = "",
    extra_keys: list | set | None = None,
    step4_custom_sig: bool = False,
):
    """
    Добавить пост в очередь и сразу пометить его как «уже отработан» в published.

    Параметры хранятся как есть — шаг 5 заново прогонит полный pipeline,
    если в записи не сохранён full_text (т.е. если вы хотите перегенерировать
    верстку в момент публикации, просто не передавайте full_text).

    Если full_text передан — шаг 5 сразу отправит готовый текст,
    применив только ai_check_telegram_layout (последний контроль вёрстки).
    """
    # 1. Немедленно помечаем в published чтобы основной процесс не взял снова
    mark_published(
        published,
        post_id=post_id,
        url=post_url,
        text=raw_text,
        source=source,
        media_items=media_items or [],
        extra_keys=extra_keys,
    )
    save_published(published)

    # 2. Кладём запись в очередь
    entry = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "post_id": post_id,
        "post_url": post_url,
        "source": source,
        "raw_text": raw_text,
        "translated": translated,
        "edited": edited,
        "full_text": full_text,          # готовый HTML-текст, если уже есть
        "media_items": media_items or [],
        "extra_keys": list(extra_keys or []),
        "step4_custom_sig": step4_custom_sig,
    }
    queue = load_queue()
    queue.append(entry)
    save_queue(queue)
    print(f"  → Пост добавлен в очередь (в очереди: {len(queue)}): {post_id[:60] or post_url[:60]}")


# ────────────────────────────────────────────────────────────
# ШАГ 5: Публикация из очереди
# ────────────────────────────────────────────────────────────

def step5_publish_queue(published: set) -> set:
    """
    Шаг 5 — запускается планировщиком каждые 30 минут.
    Публикует до QUEUE_BATCH_SIZE постов из очереди.
    """
    print("\n══════ ШАГ 5: Публикация из очереди ══════")

    queue = load_queue()
    if not queue:
        print("  Очередь пуста — нечего публиковать")
        return published

    print(f"  Постов в очереди: {len(queue)}")

    batch = queue[:QUEUE_BATCH_SIZE]
    remaining = queue[QUEUE_BATCH_SIZE:]

    published_count = 0
    failed_entries = []

    for entry in batch:
        post_id     = entry.get("post_id", "")
        post_url    = entry.get("post_url", "")
        raw_text    = entry.get("raw_text", "")
        translated  = entry.get("translated", "")
        edited_text = entry.get("edited", "")
        full_text   = entry.get("full_text", "")
        media_items = entry.get("media_items", [])
        source      = entry.get("source", "")
        extra_keys  = set(entry.get("extra_keys", []))
        step_num    = entry.get("step", 0)
        step4_sig   = entry.get("step4_custom_sig", False)

        print(f"\n  Публикуем из очереди: {post_id[:60] or post_url[:60]}")

        # ── Восстановить pipeline если full_text не сохранён ──
        if not full_text:
            # Перевод — если не был сохранён
            if not translated and raw_text:
                translated = translate_deepl(raw_text)

            # Редактура — если не была сохранена
            if not edited_text and translated:
                edited_text = edit_openrouter(translated)

            text_for_html = edited_text or translated or raw_text

            # Форматирование по типу шага
            if step_num == 2:
                full_text = make_instagram_telegram_html(text_for_html)
            elif step_num == 4:
                # Для шага 4 full_text должен быть сохранён с подписью.
                # Если его нет — используем базовый html.
                full_text = make_telegram_html(text_for_html, bold_first_line=False)
                step4_sig = False
            else:
                full_text = make_telegram_html(text_for_html, bold_first_line=False)

        # ── Финальный AI-контроль вёрстки ──
        # Для step4 с кастомной подписью layout-check уже был выполнен при записи.
        # Для остальных — запускаем снова (может пройти время, модель может дать лучший результат).
        if not step4_sig:
            full_text = ai_check_telegram_layout(full_text)

        # ── Отправка ──
        print(f"  Отправка ({len(media_items)} медиа): {post_id[:60]}")
        ok = send_to_telegram(media_items, full_text, step4_custom_sig=step4_sig)

        if ok:
            # Перепометить в published (на случай если файл сбросился)
            mark_published(
                published,
                post_id=post_id,
                url=post_url,
                text=raw_text,
                source=source,
                media_items=media_items,
                extra_keys=extra_keys,
            )
            save_published(published)
            if raw_text:
                record_source_news(raw_text, step=step_num, source=source, post_id=post_id, url=post_url)
            published_count += 1
        elif ok is False:
            # Временная ошибка Telegram — вернём в начало очереди
            print("  Временная ошибка Telegram — возвращаем пост в очередь")
            failed_entries.append(entry)
        else:
            # ok is None — медиа недоступны, пост выбрасываем
            print("  Медиа недоступны — пост удалён из очереди без публикации")

    # Сохраняем очередь: сначала те, что не удалось отправить, потом остальные
    save_queue(failed_entries + remaining)
    print(f"\n  Итого опубликовано: {published_count}, осталось в очереди: {len(failed_entries + remaining)}")
    return published
