import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

STATE_FILE = Path("state.json")

TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "").strip()
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def make_session():
    retry = Retry(
        total=config.MAX_RETRIES,
        connect=config.MAX_RETRIES,
        read=config.MAX_RETRIES,
        status=config.MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


SESSION = make_session()


def validate_secrets():
    missing = []

    if not TWITTER_API_KEY:
        missing.append("TWITTER_API_KEY")
    if not DEEPL_API_KEY:
        missing.append("DEEPL_API_KEY")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")

    if missing:
        raise RuntimeError(
            "缺少 GitHub Secrets: " + ", ".join(missing)
        )


def load_state():
    if not STATE_FILE.exists():
        return {"seen": []}

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        if not isinstance(data, dict):
            return {"seen": []}
        data.setdefault("seen", [])
        return data
    except Exception:
        return {"seen": []}


def save_state(seen):
    data = {
        "seen": seen[-config.MAX_SEEN_IDS:],
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }

    STATE_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def fetch_latest_tweets():
    params = {
        "userName": config.X_USERNAME,
        "includeReplies": str(config.INCLUDE_REPLIES).lower(),
    }

    headers = {
        "X-API-Key": TWITTER_API_KEY,
        "Accept": "application/json",
    }

    print(
        f"TwitterAPI.io: fetching @{config.X_USERNAME}"
    )

    response = SESSION.get(
        config.TWITTER_API_URL,
        params=params,
        headers=headers,
        timeout=config.REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            f"TwitterAPI.io HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            "TwitterAPI.io API error: "
            + str(data.get("message"))
        )

    tweets = data.get("tweets") or []

    cleaned = []

    for tweet in tweets:
        if not isinstance(tweet, dict):
            continue

        tweet_id = str(tweet.get("id") or "").strip()
        text = str(tweet.get("text") or "").strip()

        if not tweet_id or not text:
            continue

        if tweet.get("isReply"):
            continue

        if config.EXCLUDE_RETWEETS:
            # API 可能用 type=retweet 或 retweeted_tweet 表示轉推
            if str(tweet.get("type") or "").lower() == "retweet":
                continue

            retweeted = tweet.get("retweeted_tweet")
            if retweeted not in (None, "", False, {}):
                continue

        url = (
            tweet.get("url")
            or f"https://x.com/{config.X_USERNAME}/status/{tweet_id}"
        )

        created_at = tweet.get("createdAt")

        cleaned.append(
            {
                "id": tweet_id,
                "text": text,
                "url": url,
                "createdAt": created_at,
                "raw": tweet,
            }
        )

    # Twitter Snowflake ID 數值越大通常越新
    cleaned.sort(
        key=lambda x: int(x["id"]),
        reverse=True,
    )

    print(
        f"TwitterAPI.io: got {len(cleaned)} usable tweet(s)"
    )

    if cleaned:
        print(
            "Latest:",
            cleaned[0]["id"],
            cleaned[0]["url"],
        )

    return cleaned


def translate_with_deepl(text):
    headers = {
        "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "WWM-X-Discord-GitHub/1.0",
    }

    payload = {
        "text": [text],
        "target_lang": config.DEEPL_TARGET_LANG,
        "preserve_formatting": True,
    }

    response = SESSION.post(
        config.DEEPL_API_URL,
        headers=headers,
        json=payload,
        timeout=config.REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            f"DeepL HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    translations = data.get("translations") or []

    if not translations:
        raise RuntimeError(
            "DeepL returned no translation."
        )

    translated = str(
        translations[0].get("text") or ""
    ).strip()

    if not translated:
        raise RuntimeError(
            "DeepL returned empty translation."
        )

    return translated


def extract_image(tweet):
    """
    TwitterAPI.io 的不同回傳版本可能有不同媒體欄位。
    這裡用多個常見路徑做兼容。
    """
    raw = tweet.get("raw") or {}

    candidates = []

    for key in (
        "media",
        "medias",
        "mediaList",
        "extendedEntities",
        "extended_entities",
    ):
        value = raw.get(key)

        if isinstance(value, list):
            candidates.extend(value)

        elif isinstance(value, dict):
            inner = value.get("media")
            if isinstance(inner, list):
                candidates.extend(inner)

    entities = raw.get("entities")
    if isinstance(entities, dict):
        media = entities.get("media")
        if isinstance(media, list):
            candidates.extend(media)

    for item in candidates:
        if not isinstance(item, dict):
            continue

        for key in (
            "media_url_https",
            "media_url",
            "url",
            "preview_image_url",
        ):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

    return None


def send_to_discord(tweet):
    try:
        translated = translate_with_deepl(tweet["text"])
        print("DeepL: translation OK")
    except Exception as e:
        print("DeepL: translation failed:", e)
        translated = (
            "⚠️ 中文翻譯暫時失敗，請查看下方原文。"
        )

    embed = {
        "title": config.DISCORD_TITLE,
        "url": tweet["url"],
        "description": translated[:4000],
        "fields": [
            {
                "name": "原文",
                "value": tweet["text"][:1000],
                "inline": False,
            }
        ],
        "footer": {
            "text": config.DISCORD_FOOTER
        },
    }

    if tweet.get("createdAt"):
        # Discord 接受 ISO timestamp；
        # 若 TwitterAPI.io 格式不是 ISO，就不強制塞入。
        raw_ts = str(tweet["createdAt"])
        if "T" in raw_ts:
            embed["timestamp"] = raw_ts

    image = extract_image(tweet)
    if image:
        embed["image"] = {"url": image}

    payload = {
        "username": config.DISCORD_USERNAME,
        "embeds": [embed],
        "allowed_mentions": {
            "parse": []
        },
    }

    response = SESSION.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=config.REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            f"Discord HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    print("Discord: sent", tweet["id"])


def main():
    validate_secrets()

    tweets = fetch_latest_tweets()

    if not tweets:
        print("沒有取得任何可用貼文。")
        return

    state = load_state()

    seen = [
        str(x)
        for x in state.get("seen", [])
    ]

    seen_set = set(seen)

    # 第一次執行
    if not seen:
        if config.SEND_LATEST_ON_FIRST_RUN:
            latest = tweets[0]

            print(
                "First run: sending current latest tweet"
            )

            send_to_discord(latest)

        # 第一次就把目前 API 返回的全部 ID 記為已見
        # 避免下次把歷史貼文全部補發
        initial_ids = [
            tweet["id"]
            for tweet in tweets
        ]

        save_state(initial_ids)
        return

    new_tweets = [
        tweet
        for tweet in tweets
        if tweet["id"] not in seen_set
    ]

    if not new_tweets:
        print("沒有新貼文。")
        return

    # 防止長時間停機後大量刷屏
    new_tweets = new_tweets[:config.MAX_POSTS_PER_RUN]

    # Discord 按舊 → 新
    new_tweets.sort(
        key=lambda x: int(x["id"])
    )

    sent_count = 0

    for tweet in new_tweets:
        send_to_discord(tweet)

        if tweet["id"] not in seen_set:
            seen.append(tweet["id"])
            seen_set.add(tweet["id"])

        # 每成功發一條就保存
        # 防止中途失敗後重複發前面的
        save_state(seen)

        sent_count += 1

    print(
        f"完成，共發送 {sent_count} 條新貼文。"
    )


if __name__ == "__main__":
    main()
