import os
import json
import re
import html
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator, MyMemoryTranslator

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

RSS_URLS_RAW = os.environ.get("RSS_URLS", "").strip()
SINGLE_RSS_URL = os.environ.get("RSS_URL", "").strip()

WEBHOOK_NAME = os.environ.get("WEBHOOK_NAME", "燕雲官方情報")
WEBHOOK_AVATAR = os.environ.get("WEBHOOK_AVATAR", "")

STATE_FILE = Path("state.json")
MAX_SEEN = 100

SEND_LATEST_ON_FIRST_RUN = os.environ.get(
    "SEND_LATEST_ON_FIRST_RUN", "false"
).lower() == "true"

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))

# RSS 最新內容超過多少天，就視為「可訪問但內容可能已停更」
MAX_FEED_AGE_DAYS = int(os.environ.get("MAX_FEED_AGE_DAYS", "14"))


def get_rss_urls():
    urls = []

    if RSS_URLS_RAW:
        chunks = []
        for line in RSS_URLS_RAW.splitlines():
            chunks.extend(line.split(","))

        for item in chunks:
            item = item.strip()
            if item and item not in urls:
                urls.append(item)

    if SINGLE_RSS_URL and SINGLE_RSS_URL not in urls:
        urls.append(SINGLE_RSS_URL)

    if not urls:
        raise RuntimeError("沒有設定 RSS_URLS 或 RSS_URL。")

    return urls


def safe_source_name(url):
    try:
        parsed = urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


def parse_entry_datetime(entry):
    # feedparser 常見欄位：
    # published_parsed / updated_parsed
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime(
                    value.tm_year,
                    value.tm_mon,
                    value.tm_mday,
                    value.tm_hour,
                    value.tm_min,
                    value.tm_sec,
                    tzinfo=timezone.utc,
                )
            except Exception:
                pass

    # 備援：嘗試 ISO 字串
    for key in ("published", "updated"):
        value = entry.get(key)
        if not value:
            continue

        try:
            normalized = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    return None


def is_feed_fresh(feed):
    if not feed.entries:
        return False, "沒有任何項目"

    latest = feed.entries[0]
    latest_dt = parse_entry_datetime(latest)

    # 有些第三方 RSS 不提供可靠日期。
    # 這種情況不直接判死刑，避免誤殺正常 feed。
    if latest_dt is None:
        return True, "無法解析最新貼文日期，暫時接受"

    age = datetime.now(timezone.utc) - latest_dt

    if age > timedelta(days=MAX_FEED_AGE_DAYS):
        return (
            False,
            f"最新項目距今約 {age.days} 天，超過 "
            f"{MAX_FEED_AGE_DAYS} 天門檻"
        )

    return True, f"最新項目距今約 {age.days} 天"


def fetch_feed_with_fallback(urls):
    errors = []

    for index, url in enumerate(urls, start=1):
        source_name = safe_source_name(url)
        print(f"[{index}/{len(urls)}] 嘗試 RSS：{source_name}")

        try:
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; "
                        "WhereWindsMeetDiscordRSSBot/2.0)"
                    )
                },
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo and not feed.entries:
                raise RuntimeError(f"RSS 解析失敗：{feed.bozo_exception}")

            if not feed.entries:
                raise RuntimeError("RSS 回傳成功，但沒有任何項目。")

            fresh, reason = is_feed_fresh(feed)

            if not fresh:
                raise RuntimeError(f"RSS 內容疑似過期：{reason}")

            print(
                f"✓ RSS 可用：{source_name}，"
                f"讀取到 {len(feed.entries)} 個項目；{reason}"
            )

            return feed, url

        except Exception as e:
            msg = f"{source_name}: {type(e).__name__}: {e}"
            errors.append(msg)
            print(f"✗ RSS 失敗：{msg}")

    raise RuntimeError(
        "所有 RSS 來源都失敗。\n" + "\n".join(errors)
    )


def load_seen():
    if not STATE_FILE.exists():
        return []

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("seen", [])
    except Exception:
        return []


def save_seen(seen, active_rss_url=None):
    state = {
        "seen": seen[-MAX_SEEN:],
    }

    if active_rss_url:
        state["last_working_rss"] = active_rss_url

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clean_html(value):
    if not value:
        return ""

    soup = BeautifulSoup(value, "html.parser")
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def item_id(entry):
    raw = (
        entry.get("id")
        or entry.get("guid")
        or entry.get("link")
        or (entry.get("title", "") + entry.get("published", ""))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def entry_text(entry):
    candidates = [
        entry.get("summary"),
        entry.get("description"),
        entry.get("content", [{}])[0].get("value")
        if entry.get("content")
        else None,
        entry.get("title"),
    ]

    for value in candidates:
        text = clean_html(value)
        if text:
            return text

    return "(無文字內容)"


def translation_is_bad(result):
    if not result:
        return True

    lower = result.lower()

    bad_words = [
        "error 500",
        "server error",
        "that's an error",
        "please try again later",
        "<html",
        "<!doctype",
        "service unavailable",
        "bad gateway",
    ]

    return any(word in lower for word in bad_words)


def translate_zh_tw(text):
    if not text or text == "(無文字內容)":
        return text

    # 第一順位：Google
    try:
        result = GoogleTranslator(
            source="auto",
            target="zh-TW",
        ).translate(text)

        if not translation_is_bad(result):
            print("✓ Google 翻譯成功")
            return result

        print("✗ Google 返回疑似錯誤頁，改用 MyMemory")

    except Exception as e:
        print(f"✗ Google 翻譯失敗：{e}")

    # 第二順位：MyMemory
    try:
        # MyMemory 對語言代碼通常用 zh-TW 也可，
        # 若服務端不接受，會被 except 捕捉。
        result = MyMemoryTranslator(
            source="auto",
            target="zh-TW",
        ).translate(text)

        if not translation_is_bad(result):
            print("✓ MyMemory 翻譯成功")
            return result

        print("✗ MyMemory 返回異常結果")

    except Exception as e:
        print(f"✗ MyMemory 翻譯失敗：{e}")

    return "⚠️ 中文翻譯暫時失敗，請查看下方原文。"


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def find_image(entry):
    if entry.get("media_content"):
        for media in entry.media_content:
            url = media.get("url")
            if url:
                return url

    raw_html = ""

    if entry.get("content"):
        raw_html = entry.get("content", [{}])[0].get("value", "")

    raw_html = raw_html or entry.get("summary", "")

    soup = BeautifulSoup(raw_html, "html.parser")
    img = soup.find("img")

    if img and img.get("src"):
        return img["src"]

    return None


def send_discord(entry, active_rss_url):
    original = entry_text(entry)
    translated = translate_zh_tw(original)
    link = entry.get("link", "")

    embed = {
        "title": "燕雲十六聲｜官方 X 更新",
        "description": truncate(translated, 3500),
        "fields": [
            {
                "name": "原文",
                "value": truncate(original, 900),
                "inline": False,
            }
        ],
        "footer": {
            "text": (
                "來源：Where Winds Meet 官方 X"
                f" · RSS：{safe_source_name(active_rss_url)}"
            )
        },
    }

    if link:
        embed["url"] = link

    image_url = find_image(entry)
    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "username": WEBHOOK_NAME,
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    if WEBHOOK_AVATAR:
        payload["avatar_url"] = WEBHOOK_AVATAR

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def main():
    rss_urls = get_rss_urls()
    feed, active_rss_url = fetch_feed_with_fallback(rss_urls)

    entries = list(feed.entries)
    seen = load_seen()
    seen_set = set(seen)

    if not seen:
        current_ids = [item_id(e) for e in entries]

        if SEND_LATEST_ON_FIRST_RUN:
            latest = entries[0]
            print("第一次執行：發送目前最新貼文。")
            send_discord(latest, active_rss_url)
        else:
            print("第一次執行：只建立去重基準，不發送舊貼文。")

        save_seen(current_ids[-MAX_SEEN:], active_rss_url)
        return

    new_entries = [
        entry
        for entry in entries
        if item_id(entry) not in seen_set
    ]

    if not new_entries:
        print("沒有新貼文。")
        save_seen(seen, active_rss_url)
        return

    sent_count = 0

    for entry in reversed(new_entries):
        print(
            "發送：",
            entry.get(
                "title",
                entry.get("link", "(無標題)")
            ),
        )

        send_discord(entry, active_rss_url)

        eid = item_id(entry)
        if eid not in seen:
            seen.append(eid)

        save_seen(seen, active_rss_url)
        sent_count += 1

    print(
        f"完成，共發送 {sent_count} 條；"
        f"本次使用 RSS：{safe_source_name(active_rss_url)}"
    )


if __name__ == "__main__":
    main()
