import os
import json
import re
import html
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator, MyMemoryTranslator

X_HANDLE = os.environ.get("X_HANDLE", "WhereWindsMeet_").lstrip("@")
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

WEBHOOK_NAME = os.environ.get("WEBHOOK_NAME", "燕雲官方情報")
WEBHOOK_AVATAR = os.environ.get("WEBHOOK_AVATAR", "")
SEND_LATEST_ON_FIRST_RUN = os.environ.get(
    "SEND_LATEST_ON_FIRST_RUN", "true"
).lower() == "true"

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "25"))
STATE_FILE = Path("state.json")
MAX_SEEN = 100

# X/Twitter 官方嵌入時間線頁。
# 這不是 X 開發者 API，不需要 API key。
TIMELINE_URL = (
    "https://syndication.twitter.com/srv/timeline-profile/"
    f"screen-name/{X_HANDLE}"
)


def request_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/152.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }


def load_state():
    if not STATE_FILE.exists():
        return {"seen": []}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"seen": []}
        data.setdefault("seen", [])
        return data
    except Exception:
        return {"seen": []}


def save_state(seen, last_source="syndication-profile"):
    STATE_FILE.write_text(
        json.dumps(
            {
                "seen": seen[-MAX_SEEN:],
                "last_source": last_source,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def clean_text(value):
    if value is None:
        return ""

    if not isinstance(value, str):
        value = str(value)

    soup = BeautifulSoup(value, "html.parser")
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk(item)


def tweet_url(handle, tweet_id):
    return f"https://x.com/{handle}/status/{tweet_id}"


def extract_tweets_from_next_data(payload):
    """
    X syndication timeline 的 JSON 結構可能會調整。
    這裡不綁死單一路徑，而是遞迴找像 tweet 的物件。
    """
    found = {}

    for obj in walk(payload):
        if not isinstance(obj, dict):
            continue

        tweet_id = (
            obj.get("id_str")
            or obj.get("rest_id")
            or obj.get("tweet_id")
        )

        # 避免把 user id / 其他 id 誤認成 tweet。
        text = (
            obj.get("full_text")
            or obj.get("text")
            or obj.get("tweet_text")
        )

        if tweet_id is None or text is None:
            continue

        tweet_id = str(tweet_id)

        if not tweet_id.isdigit() or len(tweet_id) < 10:
            continue

        # 嘗試讀作者
        author = X_HANDLE

        user = obj.get("user")
        if isinstance(user, dict):
            author = (
                user.get("screen_name")
                or user.get("username")
                or author
            )

        core = obj.get("core")
        if isinstance(core, dict):
            user_results = core.get("user_results")
            if isinstance(user_results, dict):
                result = user_results.get("result")
                if isinstance(result, dict):
                    legacy = result.get("legacy")
                    if isinstance(legacy, dict):
                        author = legacy.get("screen_name", author)

        # legacy 結構
        legacy = obj.get("legacy")
        if isinstance(legacy, dict):
            legacy_text = (
                legacy.get("full_text")
                or legacy.get("text")
            )
            if legacy_text:
                text = legacy_text

        text = clean_text(text)

        if not text:
            continue

        # 只保留目標帳號自己的貼文
        if author and author.lower() != X_HANDLE.lower():
            continue

        # 排除回覆
        in_reply_to = (
            obj.get("in_reply_to_status_id_str")
            or obj.get("in_reply_to_screen_name")
        )
        if isinstance(legacy, dict):
            in_reply_to = (
                in_reply_to
                or legacy.get("in_reply_to_status_id_str")
                or legacy.get("in_reply_to_screen_name")
            )
        if in_reply_to:
            continue

        # 嘗試找日期
        created_at = (
            obj.get("created_at")
            or (legacy.get("created_at") if isinstance(legacy, dict) else None)
        )

        # 嘗試找圖片
        image_url = None

        def inspect_media(container):
            nonlocal image_url
            if not isinstance(container, dict):
                return
            media = container.get("media")
            if isinstance(media, list):
                for item in media:
                    if not isinstance(item, dict):
                        continue
                    candidate = (
                        item.get("media_url_https")
                        or item.get("media_url")
                        or item.get("url")
                    )
                    if candidate and str(candidate).startswith("http"):
                        image_url = str(candidate)
                        return

        entities = obj.get("entities")
        if isinstance(entities, dict):
            inspect_media(entities)

        extended = obj.get("extended_entities")
        if isinstance(extended, dict):
            inspect_media(extended)

        if isinstance(legacy, dict):
            legacy_entities = legacy.get("entities")
            if isinstance(legacy_entities, dict):
                inspect_media(legacy_entities)
            legacy_extended = legacy.get("extended_entities")
            if isinstance(legacy_extended, dict):
                inspect_media(legacy_extended)

        found[tweet_id] = {
            "id": tweet_id,
            "text": text,
            "author": author or X_HANDLE,
            "created_at": created_at,
            "url": tweet_url(X_HANDLE, tweet_id),
            "image_url": image_url,
        }

    # Snowflake tweet id 大體上可按數值大小代表時間先後
    tweets = sorted(
        found.values(),
        key=lambda x: int(x["id"]),
        reverse=True,
    )

    return tweets


def fetch_profile_timeline():
    print(f"抓取 X 公開嵌入時間線：@{X_HANDLE}")

    response = requests.get(
        TIMELINE_URL,
        headers=request_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise RuntimeError(
            "X syndication 頁面沒有找到 __NEXT_DATA__。"
            "可能是 X 調整了嵌入頁結構。"
        )

    try:
        payload = json.loads(script.string)
    except Exception as e:
        raise RuntimeError(f"無法解析 X syndication JSON：{e}")

    tweets = extract_tweets_from_next_data(payload)

    if not tweets:
        raise RuntimeError(
            "成功讀到 X syndication 頁，但沒有解析出任何正式貼文。"
        )

    print(f"✓ 解析到 {len(tweets)} 條候選貼文")
    print(
        f"✓ 最新貼文 ID：{tweets[0]['id']} "
        f"{tweets[0]['url']}"
    )

    return tweets


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


def send_discord(tweet):
    original = tweet["text"]
    translated = translate_zh_tw(original)

    embed = {
        "title": "燕雲十六聲｜官方 X 更新",
        "url": tweet["url"],
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
                " · X syndication"
            )
        },
    }

    if tweet.get("image_url"):
        embed["image"] = {"url": tweet["image_url"]}

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
    tweets = fetch_profile_timeline()

    state = load_state()
    seen = state.get("seen", [])
    seen_set = set(seen)

    if not seen:
        all_ids = [tweet["id"] for tweet in tweets]

        if SEND_LATEST_ON_FIRST_RUN:
            print("第一次執行：發送目前最新貼文。")
            send_discord(tweets[0])
        else:
            print("第一次執行：只建立基準，不發送舊貼文。")

        save_state(all_ids)
        return

    new_tweets = [
        tweet for tweet in tweets
        if tweet["id"] not in seen_set
    ]

    if not new_tweets:
        print("沒有新貼文。")
        save_state(seen)
        return

    # 舊 → 新 發送
    for tweet in reversed(new_tweets):
        print(f"發送：{tweet['url']}")
        send_discord(tweet)

        if tweet["id"] not in seen:
            seen.append(tweet["id"])

        save_state(seen)

    print(f"完成，共發送 {len(new_tweets)} 條。")


if __name__ == "__main__":
    main()
