import os
import json
import re
import html
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


def save_state(seen):
    STATE_FILE.write_text(
        json.dumps(
            {
                "seen": seen[-MAX_SEEN:],
                "last_source": "syndication-profile-direct-entries",
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

    value = str(value)
    soup = BeautifulSoup(value, "html.parser")
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_image(tweet):
    # Twitter syndication timeline 常見媒體位置
    for container_name in ("extended_entities", "entities"):
        container = tweet.get(container_name)
        if not isinstance(container, dict):
            continue

        media = container.get("media")
        if not isinstance(media, list):
            continue

        for item in media:
            if not isinstance(item, dict):
                continue

            url = (
                item.get("media_url_https")
                or item.get("media_url")
            )
            if url:
                return str(url)

    return None


def normalize_entry(entry):
    """
    只處理 timeline.entries 裡的「頂層 tweet」。
    不再遞迴掃描整個 JSON，避免把：
    - 置頂 tweet
    - quoted tweet 內層作者
    - user id
    誤判成最新貼文。
    """
    if not isinstance(entry, dict):
        return None

    content = entry.get("content")
    if not isinstance(content, dict):
        return None

    tweet = content.get("tweet")
    if not isinstance(tweet, dict):
        return None

    tweet_id = tweet.get("id_str") or tweet.get("id")
    if tweet_id is None:
        return None

    tweet_id = str(tweet_id)
    if not tweet_id.isdigit():
        return None

    user = tweet.get("user")
    if not isinstance(user, dict):
        return None

    screen_name = str(user.get("screen_name", ""))
    if screen_name.lower() != X_HANDLE.lower():
        return None

    # 排除 Reply
    if (
        tweet.get("in_reply_to_status_id_str")
        or tweet.get("in_reply_to_screen_name")
    ):
        return None

    text = clean_text(
        tweet.get("full_text")
        or tweet.get("text")
        or ""
    )

    if not text:
        return None

    permalink = tweet.get("permalink")
    if not permalink:
        permalink = f"https://x.com/{X_HANDLE}/status/{tweet_id}"
    elif str(permalink).startswith("/"):
        permalink = "https://x.com" + str(permalink)
    else:
        permalink = str(permalink).replace(
            "https://twitter.com/",
            "https://x.com/",
        )

    return {
        "id": tweet_id,
        "text": text,
        "url": permalink,
        "created_at": tweet.get("created_at"),
        "image_url": get_image(tweet),
        "sort_index": str(entry.get("sort_index", "")),
        "entry_id": str(entry.get("entry_id", "")),
    }


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
        )

    payload = json.loads(script.string)

    page_props = (
        payload.get("props", {})
        .get("pageProps", {})
    )

    timeline = page_props.get("timeline", {})
    entries = timeline.get("entries", [])

    latest_tweet_id = page_props.get("latest_tweet_id")
    if latest_tweet_id:
        latest_tweet_id = str(latest_tweet_id)
        print(f"X 提供的 latest_tweet_id：{latest_tweet_id}")
    else:
        print("⚠️ 頁面沒有 latest_tweet_id，改用 tweet ID 排序判斷")

    tweets = []

    for entry in entries:
        tweet = normalize_entry(entry)
        if tweet:
            tweets.append(tweet)

    # 去重
    unique = {}
    for tweet in tweets:
        unique[tweet["id"]] = tweet

    tweets = list(unique.values())

    if not tweets:
        raise RuntimeError(
            "成功讀到 timeline.entries，但沒有解析出目標帳號正式貼文。"
        )

    # Tweet Snowflake ID 可用數值大小判斷先後。
    tweets.sort(
        key=lambda x: int(x["id"]),
        reverse=True,
    )

    # 如果 latest_tweet_id 存在，優先用它驗證真正最新貼文。
    # 置頂貼文不會覆蓋 latest_tweet_id。
    if latest_tweet_id:
        match = next(
            (t for t in tweets if t["id"] == latest_tweet_id),
            None,
        )
        if match:
            # 放到第一位
            tweets = [match] + [
                t for t in tweets
                if t["id"] != latest_tweet_id
            ]
            print(
                "✓ 已依 X 的 latest_tweet_id 鎖定真正最新貼文："
                f"{match['url']}"
            )
        else:
            print(
                "⚠️ latest_tweet_id 不在目前 entries 中；"
                "使用頂層 tweet ID 排序。"
            )

    print(f"✓ 解析到 {len(tweets)} 條正式貼文")
    print(f"✓ 本次判定最新：{tweets[0]['url']}")

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
    seen_set = set(str(x) for x in seen)

    # 如果 state 是舊版本留下來的，也能繼續使用。
    if not seen:
        all_ids = [tweet["id"] for tweet in tweets]

        if SEND_LATEST_ON_FIRST_RUN:
            print("第一次執行：發送真正最新貼文。")
            send_discord(tweets[0])
        else:
            print("第一次執行：只建立基準，不發送舊貼文。")

        save_state(all_ids)
        return

    # 只找尚未發過的正式貼文。
    new_tweets = [
        tweet for tweet in tweets
        if tweet["id"] not in seen_set
    ]

    if not new_tweets:
        print("沒有新貼文。")
        save_state(seen)
        return

    # 依 ID 從舊到新發送。
    # 即使 30 分鐘內官方連發幾條，也不會只漏剩最後一條。
    new_tweets.sort(key=lambda x: int(x["id"]))

    for tweet in new_tweets:
        print(f"發送：{tweet['url']}")
        send_discord(tweet)

        if tweet["id"] not in seen:
            seen.append(tweet["id"])

        save_state(seen)

    print(f"完成，共發送 {len(new_tweets)} 條。")


if __name__ == "__main__":
    main()
