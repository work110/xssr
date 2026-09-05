import os
import json
import re
import html
import time
import random
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
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "4"))
STATE_FILE = Path("state.json")
MAX_SEEN = 100

# 主要入口：X/Twitter syndication profile timeline
PRIMARY_URL = (
    "https://syndication.twitter.com/srv/timeline-profile/"
    f"screen-name/{X_HANDLE}"
)

# 備用入口：widgets timeline embed。
# X 可能調整任一入口，因此兩個都保留。
FALLBACK_URLS = [
    PRIMARY_URL,
    (
        "https://syndication.twitter.com/timeline/profile"
        f"?screen_name={X_HANDLE}"
    ),
]


def request_headers():
    # 每次用稍不同 UA，避免所有 Actions 請求完全同樣指紋
    uas = [
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/152.0.0.0 Safari/537.36"
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Safari/605.1.15"
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
    ]

    return {
        "User-Agent": random.choice(uas),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
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


def save_state(seen, source=None):
    data = {
        "seen": [str(x) for x in seen[-MAX_SEEN:]],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if source:
        data["last_source"] = source

    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clean_text(value):
    if value is None:
        return ""

    soup = BeautifulSoup(str(value), "html.parser")
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def request_with_retry(url):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # 小幅隨機延遲，降低多個 workflow 同時撞站點的概率
            if attempt == 1:
                time.sleep(random.uniform(1.0, 3.0))

            print(f"請求 {url}（第 {attempt}/{MAX_RETRIES} 次）")

            response = requests.get(
                url,
                headers=request_headers(),
                timeout=REQUEST_TIMEOUT,
            )

            # 429：尊重 Retry-After，否則指數退避
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")

                if retry_after and retry_after.isdigit():
                    wait_seconds = min(int(retry_after), 120)
                else:
                    wait_seconds = min(
                        (2 ** attempt) * 5 + random.uniform(1, 6),
                        90,
                    )

                print(
                    f"⚠️ 收到 429 Too Many Requests，"
                    f"等待 {wait_seconds:.1f} 秒後重試"
                )
                time.sleep(wait_seconds)
                continue

            # 5xx：可重試
            if 500 <= response.status_code < 600:
                wait_seconds = min(
                    (2 ** attempt) * 3 + random.uniform(1, 4),
                    60,
                )
                print(
                    f"⚠️ 伺服器錯誤 {response.status_code}，"
                    f"等待 {wait_seconds:.1f} 秒後重試"
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            return response

        except requests.RequestException as e:
            last_error = e

            if attempt < MAX_RETRIES:
                wait_seconds = min(
                    (2 ** attempt) * 3 + random.uniform(1, 5),
                    60,
                )
                print(
                    f"⚠️ 請求失敗：{e}；"
                    f"{wait_seconds:.1f} 秒後重試"
                )
                time.sleep(wait_seconds)
            else:
                break

    if last_error:
        raise last_error

    raise RuntimeError("請求在多次重試後仍然失敗。")


def get_image(tweet):
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
    }


def parse_syndication_html(response_text):
    soup = BeautifulSoup(response_text, "html.parser")

    # 新版常見
    script = soup.find("script", id="__NEXT_DATA__")

    if script and script.string:
        payload = json.loads(script.string)

        page_props = (
            payload.get("props", {})
            .get("pageProps", {})
        )

        timeline = page_props.get("timeline", {})
        entries = timeline.get("entries", [])
        latest_tweet_id = page_props.get("latest_tweet_id")

        tweets = []

        for entry in entries:
            tweet = normalize_entry(entry)
            if tweet:
                tweets.append(tweet)

        unique = {}
        for tweet in tweets:
            unique[tweet["id"]] = tweet

        tweets = list(unique.values())

        if tweets:
            tweets.sort(
                key=lambda x: int(x["id"]),
                reverse=True,
            )

            if latest_tweet_id:
                latest_tweet_id = str(latest_tweet_id)
                match = next(
                    (
                        tweet
                        for tweet in tweets
                        if tweet["id"] == latest_tweet_id
                    ),
                    None,
                )

                if match:
                    tweets = [match] + [
                        tweet
                        for tweet in tweets
                        if tweet["id"] != latest_tweet_id
                    ]

            return tweets

    # 備援：有時候嵌入頁直接帶 status URL，可先至少抓 ID
    status_ids = re.findall(
        rf"(?:twitter\.com|x\.com)/{re.escape(X_HANDLE)}/status/(\d+)",
        response_text,
        flags=re.I,
    )

    if status_ids:
        ids = sorted(
            {str(x) for x in status_ids},
            key=int,
            reverse=True,
        )

        # 沒有正文時只建立基本資料。
        return [
            {
                "id": tid,
                "text": "(公開嵌入頁未提供完整正文，請查看原帖)",
                "url": f"https://x.com/{X_HANDLE}/status/{tid}",
                "created_at": None,
                "image_url": None,
                "sort_index": "",
            }
            for tid in ids
        ]

    return []


def fetch_profile_timeline():
    errors = []

    for url in FALLBACK_URLS:
        try:
            print(f"嘗試 X 公開入口：{url}")

            response = request_with_retry(url)
            tweets = parse_syndication_html(response.text)

            if not tweets:
                raise RuntimeError(
                    "頁面可讀，但沒有解析出任何正式貼文。"
                )

            print(f"✓ 解析到 {len(tweets)} 條正式貼文")
            print(f"✓ 本次判定最新：{tweets[0]['url']}")

            return tweets, url

        except Exception as e:
            msg = f"{url}: {type(e).__name__}: {e}"
            errors.append(msg)
            print(f"✗ 此入口失敗：{msg}")

    raise RuntimeError(
        "所有 X 公開入口都失敗。\n" + "\n".join(errors)
    )


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
    if text.startswith("(公開嵌入頁未提供完整正文"):
        return "⚠️ 暫時未能讀取完整貼文內容，請點擊標題查看原帖。"

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
            "text": "來源：Where Winds Meet 官方 X"
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
    tweets, source_url = fetch_profile_timeline()

    state = load_state()
    seen = [str(x) for x in state.get("seen", [])]
    seen_set = set(seen)

    if not seen:
        all_ids = [tweet["id"] for tweet in tweets]

        if SEND_LATEST_ON_FIRST_RUN:
            print("第一次執行：發送目前最新貼文。")
            send_discord(tweets[0])
        else:
            print("第一次執行：只建立基準，不發送舊貼文。")

        save_state(all_ids, source_url)
        return

    new_tweets = [
        tweet
        for tweet in tweets
        if tweet["id"] not in seen_set
    ]

    if not new_tweets:
        print("沒有新貼文。")
        save_state(seen, source_url)
        return

    new_tweets.sort(key=lambda x: int(x["id"]))

    for tweet in new_tweets:
        print(f"發送：{tweet['url']}")
        send_discord(tweet)

        if tweet["id"] not in seen:
            seen.append(tweet["id"])

        save_state(seen, source_url)

    print(f"完成，共發送 {len(new_tweets)} 條。")


if __name__ == "__main__":
    main()
