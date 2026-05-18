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
    if not INSTAGRAM_USER:
        print("  INSTAGRAM_USER не задан — пропускаем")
        return published

    posts = fetch_instagram_posts(INSTAGRAM_USER, published)
    print(f"  Получено Instagram posts: {len(posts)}")
    new_count = 0

    for post in posts:
        post_id = post["id"]
        if post_id in published:
            continue

        media_items = post["media"]
        raw_caption = clean_text(post.get("caption", ""))

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
