import json
import os
from datetime import datetime, timezone
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


def twitter_headers():
    return {
        "X-API-Key": TWITTER_API_KEY,
        "Accept": "application/json",
    }


def load_state():
    if not STATE_FILE.exists():
        return {"seen": [], "user_id": None}

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        if not isinstance(data, dict):
            return {"seen": [], "user_id": None}

        data.setdefault("seen", [])
        data.setdefault("user_id", None)
        return data

    except Exception:
        return {"seen": [], "user_id": None}


def save_state(seen, user_id):
    STATE_FILE.write_text(
        json.dumps(
            {
                "user_id": user_id,
                "seen": seen[-config.MAX_SEEN_IDS:],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def resolve_user_id():
    print(f"[1/4] Resolving user: @{config.X_USERNAME}")

    response = SESSION.get(
        config.TWITTER_USER_INFO_URL,
        params={"userName": config.X_USERNAME},
        headers=twitter_headers(),
        timeout=config.REQUEST_TIMEOUT,
    )

    print(
        f"TwitterAPI.io user/info HTTP {response.status_code}"
    )

    if not response.ok:
        raise RuntimeError(
            f"user/info HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            "user/info API error: "
            + str(data.get("msg") or data.get("message"))
        )

    user = data.get("data") or {}

    user_id = str(user.get("id") or "").strip()
    username = str(user.get("userName") or "").strip()

    if not user_id:
        raise RuntimeError(
            "user/info 成功，但回傳中沒有 user id。"
        )

    print(
        f"Resolved user: @{username or config.X_USERNAME} -> {user_id}"
    )

    pinned = user.get("pinnedTweetIds") or []
    if pinned:
        print(
            f"Pinned tweet IDs from profile: {pinned}"
        )

    return user_id


def fetch_timeline(user_id):
    print(
        f"[2/4] Fetching timeline by userId={user_id}"
    )

    response = SESSION.get(
        config.TWITTER_TIMELINE_URL,
        params={
            "userId": user_id,
            "includeReplies": str(
                config.INCLUDE_REPLIES
            ).lower(),
        },
        headers=twitter_headers(),
        timeout=config.REQUEST_TIMEOUT,
    )

    print(
        f"TwitterAPI.io tweet_timeline HTTP {response.status_code}"
    )

    if not response.ok:
        raise RuntimeError(
            f"tweet_timeline HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(
            "tweet_timeline API error: "
            + str(data.get("message") or data.get("msg"))
        )

    tweets = data.get("tweets") or []

    print(f"Raw tweets returned: {len(tweets)}")

    replies_removed = 0
    retweets_removed = 0
    invalid_removed = 0

    cleaned = []

    for tweet in tweets:
        if not isinstance(tweet, dict):
            invalid_removed += 1
            continue

        tweet_id = str(tweet.get("id") or "").strip()
        text = str(tweet.get("text") or "").strip()

        if not tweet_id or not text:
            invalid_removed += 1
            continue

        if tweet.get("isReply"):
            replies_removed += 1
            continue

        if config.EXCLUDE_RETWEETS:
            tweet_type = str(
                tweet.get("type") or ""
            ).lower()

            retweeted = tweet.get("retweeted_tweet")

            if (
                tweet_type == "retweet"
                or retweeted not in (None, "", False, {})
            ):
                retweets_removed += 1
                continue

        author = tweet.get("author") or {}
        author_username = str(
            author.get("userName") or ""
        ).strip()

        # timeline 理論上已是該帳號，但再做一次保護
        if (
            author_username
            and author_username.lower()
            != config.X_USERNAME.lower()
        ):
            invalid_removed += 1
            continue

        url = (
            tweet.get("url")
            or f"https://x.com/{config.X_USERNAME}/status/{tweet_id}"
        )

        cleaned.append(
            {
                "id": tweet_id,
                "text": text,
                "url": url,
                "createdAt": tweet.get("createdAt"),
                "raw": tweet,
            }
        )

    # Snowflake ID 新 -> 舊
    cleaned.sort(
        key=lambda x: int(x["id"]),
        reverse=True,
    )

    print(f"Replies removed: {replies_removed}")
    print(f"Retweets removed: {retweets_removed}")
    print(f"Invalid removed: {invalid_removed}")
    print(f"Usable tweets: {len(cleaned)}")

    if cleaned:
        print(
            f"Latest usable tweet: {cleaned[0]['id']} "
            f"{cleaned[0]['url']}"
        )
        print(
            "Latest text preview:",
            cleaned[0]["text"][:180].replace("\n", " "),
        )

    return cleaned


def translate_with_deepl(text):
    print("[3/4] Translating with DeepL")

    response = SESSION.post(
        config.DEEPL_API_URL,
        headers={
            "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "WWM-X-Discord-GitHub/2.0",
        },
        json={
            "text": [text],
            "target_lang": config.DEEPL_TARGET_LANG,
            "preserve_formatting": True,
        },
        timeout=config.REQUEST_TIMEOUT,
    )

    print(f"DeepL HTTP {response.status_code}")

    if not response.ok:
        raise RuntimeError(
            f"DeepL HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    translations = data.get("translations") or []

    if not translations:
        raise RuntimeError(
            "DeepL 沒有返回 translation。"
        )

    translated = str(
        translations[0].get("text") or ""
    ).strip()

    if not translated:
        raise RuntimeError(
            "DeepL 返回空翻譯。"
        )

    return translated


def extract_image(tweet):
    raw = tweet.get("raw") or {}

    candidates = []

    # 常見直接 media 欄位
    for key in (
        "media",
        "medias",
        "mediaList",
    ):
        value = raw.get(key)
        if isinstance(value, list):
            candidates.extend(value)

    # entities / extended entities
    for key in (
        "entities",
        "extended_entities",
        "extendedEntities",
    ):
        container = raw.get(key)

        if isinstance(container, dict):
            media = container.get("media")
            if isinstance(media, list):
                candidates.extend(media)

    # TwitterAPI.io 某些版本可能把媒體放在 article / extendedEntities 類欄位
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

            if (
                isinstance(value, str)
                and value.startswith("http")
            ):
                return value

    return None


def send_to_discord(tweet):
    try:
        translated = translate_with_deepl(
            tweet["text"]
        )
        print("DeepL translation: OK")

    except Exception as e:
        print("DeepL translation failed:", e)

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

    created_at = tweet.get("createdAt")
    if created_at:
        raw_ts = str(created_at)

        # ISO 日期才送給 Discord timestamp
        if "T" in raw_ts:
            embed["timestamp"] = raw_ts

    image = extract_image(tweet)
    if image:
        embed["image"] = {"url": image}

    print("[4/4] Sending Discord")

    response = SESSION.post(
        DISCORD_WEBHOOK_URL,
        json={
            "username": config.DISCORD_USERNAME,
            "embeds": [embed],
            "allowed_mentions": {
                "parse": []
            },
        },
        timeout=config.REQUEST_TIMEOUT,
    )

    print(f"Discord HTTP {response.status_code}")

    if not response.ok:
        raise RuntimeError(
            f"Discord HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    print(
        f"Discord sent tweet {tweet['id']}"
    )


def main():
    validate_secrets()

    state = load_state()

    # 每次重新解析 user id。
    # 成本很低，也避免帳號狀態改變時 state 卡住。
    user_id = resolve_user_id()

    tweets = fetch_timeline(user_id)

    if not tweets:
        print(
            "沒有取得任何可用貼文。"
        )
        return

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

        # 首次把目前 timeline 的 ID 全記為已見
        initial_ids = [
            tweet["id"]
            for tweet in tweets
        ]

        save_state(
            initial_ids,
            user_id,
        )

        print(
            f"Initial state saved with "
            f"{len(initial_ids)} tweet IDs."
        )
        return

    new_tweets = [
        tweet
        for tweet in tweets
        if tweet["id"] not in seen_set
    ]

    if not new_tweets:
        print("沒有新貼文。")
        save_state(seen, user_id)
        return

    # 防止長時間停機刷屏
    new_tweets = new_tweets[
        :config.MAX_POSTS_PER_RUN
    ]

    # 舊 -> 新
    new_tweets.sort(
        key=lambda x: int(x["id"])
    )

    sent = 0

    for tweet in new_tweets:
        send_to_discord(tweet)

        if tweet["id"] not in seen_set:
            seen.append(tweet["id"])
            seen_set.add(tweet["id"])

        # 每成功一條就存，避免重發
        save_state(seen, user_id)
        sent += 1

    print(
        f"完成，共發送 {sent} 條新貼文。"
    )


if __name__ == "__main__":
    main()
