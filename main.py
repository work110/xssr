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
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


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
        raise RuntimeError("缺少 GitHub Secrets: " + ", ".join(missing))


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
    print(f"[1/5] Resolving user: @{config.X_USERNAME}")

    r = SESSION.get(
        config.TWITTER_USER_INFO_URL,
        params={"userName": config.X_USERNAME},
        headers=twitter_headers(),
        timeout=config.REQUEST_TIMEOUT,
    )

    print("user/info HTTP", r.status_code)

    if not r.ok:
        raise RuntimeError(
            f"user/info HTTP {r.status_code}: {r.text[:600]}"
        )

    data = r.json()
    user = data.get("data") or {}

    user_id = str(user.get("id") or "").strip()

    if not user_id:
        raise RuntimeError(
            "user/info 回傳中沒有 user id。"
        )

    print(
        f"Resolved user: @{user.get('userName') or config.X_USERNAME}"
        f" -> {user_id}"
    )

    pinned = user.get("pinnedTweetIds") or []
    print("Pinned tweet IDs:", pinned)

    return user_id


def extract_tweets_from_response(data, label):
    """
    TwitterAPI.io 目前常見格式：
    {
      "status": "success",
      "data": {
        "tweets": [...]
      }
    }

    為兼容不同版本，也保留頂層 tweets fallback。
    """

    # 第一順位：data.tweets
    nested_data = data.get("data")

    if isinstance(nested_data, dict):
        tweets = nested_data.get("tweets")
        if isinstance(tweets, list):
            print(
                f"{label}: found data.tweets = {len(tweets)}"
            )
            return tweets

    # 第二順位：頂層 tweets
    tweets = data.get("tweets")

    if isinstance(tweets, list):
        print(
            f"{label}: found top-level tweets = {len(tweets)}"
        )
        return tweets

    print(
        f"{label}: cannot find tweets array. "
        f"Top-level keys={sorted(list(data.keys()))}"
    )

    return []


def request_tweets(url, params, label):
    r = SESSION.get(
        url,
        params=params,
        headers=twitter_headers(),
        timeout=config.REQUEST_TIMEOUT,
    )

    print(f"{label} HTTP {r.status_code}")

    if not r.ok:
        raise RuntimeError(
            f"{label} HTTP {r.status_code}: {r.text[:600]}"
        )

    data = r.json()

    print(
        f"{label} status={data.get('status')} "
        f"message={data.get('message') or data.get('msg')}"
    )

    tweets = extract_tweets_from_response(
        data,
        label,
    )

    print(
        f"{label}: raw tweets returned = {len(tweets)}"
    )

    return tweets


def fetch_raw_tweets(user_id):
    print("[2/5] Trying tweet_timeline")

    tweets = request_tweets(
        config.TWITTER_TIMELINE_URL,
        {
            "userId": user_id,
            "includeReplies": str(
                config.INCLUDE_REPLIES
            ).lower(),
        },
        "tweet_timeline",
    )

    if tweets:
        print("Using source: tweet_timeline")
        return tweets, "tweet_timeline"

    print(
        "tweet_timeline returned 0; "
        "falling back to last_tweets"
    )

    tweets = request_tweets(
        config.TWITTER_LAST_TWEETS_URL,
        {
            "userId": user_id,
            "includeReplies": str(
                config.INCLUDE_REPLIES
            ).lower(),
        },
        "last_tweets",
    )

    if tweets:
        print("Using source: last_tweets")
        return tweets, "last_tweets"

    print("Both endpoints returned 0 tweets.")
    return [], None


def normalize_tweets(tweets):
    replies_removed = 0
    retweets_removed = 0
    invalid_removed = 0
    cleaned = []

    for tweet in tweets:
        if not isinstance(tweet, dict):
            invalid_removed += 1
            continue

        tweet_id = str(
            tweet.get("id") or ""
        ).strip()

        text = str(
            tweet.get("text") or ""
        ).strip()

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

            retweeted = (
                tweet.get("retweeted_tweet")
                or tweet.get("retweetedTweet")
            )

            if (
                tweet_type == "retweet"
                or retweeted not in (
                    None, "", False, {}
                )
            ):
                retweets_removed += 1
                continue

        author = tweet.get("author") or {}

        author_username = str(
            author.get("userName") or ""
        ).strip()

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

    cleaned.sort(
        key=lambda x: int(x["id"]),
        reverse=True,
    )

    print("[3/5] Normalization")
    print("Replies removed:", replies_removed)
    print("Retweets removed:", retweets_removed)
    print("Invalid removed:", invalid_removed)
    print("Usable tweets:", len(cleaned))

    if cleaned:
        print(
            "Latest usable tweet:",
            cleaned[0]["id"],
            cleaned[0]["url"],
        )

        print(
            "Latest text preview:",
            cleaned[0]["text"][:180]
            .replace("\n", " "),
        )

    return cleaned


def translate_with_deepl(text):
    print("[4/5] DeepL translation")

    r = SESSION.post(
        config.DEEPL_API_URL,
        headers={
            "Authorization":
                f"DeepL-Auth-Key {DEEPL_API_KEY}",
            "Content-Type":
                "application/json",
        },
        json={
            "text": [text],
            "target_lang":
                config.DEEPL_TARGET_LANG,
            "preserve_formatting": True,
        },
        timeout=config.REQUEST_TIMEOUT,
    )

    print("DeepL HTTP", r.status_code)

    if not r.ok:
        raise RuntimeError(
            f"DeepL HTTP {r.status_code}: "
            f"{r.text[:500]}"
        )

    data = r.json()

    translations = (
        data.get("translations")
        or []
    )

    if not translations:
        raise RuntimeError(
            "DeepL 沒有返回 translation。"
        )

    result = str(
        translations[0].get("text")
        or ""
    ).strip()

    if not result:
        raise RuntimeError(
            "DeepL 返回空翻譯。"
        )

    return result


def extract_image(tweet):
    raw = tweet.get("raw") or {}

    # TwitterAPI.io 常見 media 欄位
    candidates = []

    for key in (
        "media",
        "medias",
        "mediaList",
    ):
        value = raw.get(key)

        if isinstance(value, list):
            candidates.extend(value)

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

        print("DeepL: OK")

    except Exception as e:
        print(
            "DeepL failed:",
            e
        )

        translated = (
            "⚠️ 中文翻譯暫時失敗，"
            "請查看下方原文。"
        )

    embed = {
        "title":
            config.DISCORD_TITLE,

        "url":
            tweet["url"],

        "description":
            translated[:4000],

        "fields": [
            {
                "name": "原文",
                "value":
                    tweet["text"][:1000],
                "inline": False,
            }
        ],

        "footer": {
            "text":
                config.DISCORD_FOOTER
        },
    }

    image = extract_image(tweet)

    if image:
        embed["image"] = {
            "url": image
        }

    print("[5/5] Sending Discord")

    r = SESSION.post(
        DISCORD_WEBHOOK_URL,
        json={
            "username":
                config.DISCORD_USERNAME,

            "embeds":
                [embed],

            "allowed_mentions": {
                "parse": []
            },
        },
        timeout=config.REQUEST_TIMEOUT,
    )

    print(
        "Discord HTTP",
        r.status_code
    )

    if not r.ok:
        raise RuntimeError(
            f"Discord HTTP {r.status_code}: "
            f"{r.text[:500]}"
        )

    print(
        "Discord sent:",
        tweet["id"]
    )


def main():
    validate_secrets()

    state = load_state()

    user_id = resolve_user_id()

    raw_tweets, source = (
        fetch_raw_tweets(user_id)
    )

    if not raw_tweets:
        print(
            "沒有取得任何貼文。"
        )
        return

    tweets = normalize_tweets(
        raw_tweets
    )

    if not tweets:
        print(
            "API 有返回貼文，"
            "但全部被本地過濾。"
        )
        return

    seen = [
        str(x)
        for x in state.get(
            "seen",
            []
        )
    ]

    seen_set = set(seen)

    # 第一次執行
    if not seen:
        if (
            config.SEND_LATEST_ON_FIRST_RUN
        ):
            latest = tweets[0]

            print(
                "First run: "
                "sending latest tweet"
            )

            send_to_discord(
                latest
            )

        initial_ids = [
            t["id"]
            for t in tweets
        ]

        save_state(
            initial_ids,
            user_id,
        )

        print(
            f"Initial state saved "
            f"({len(initial_ids)} ids), "
            f"source={source}"
        )

        return

    new_tweets = [
        t
        for t in tweets
        if t["id"]
        not in seen_set
    ]

    if not new_tweets:
        print("沒有新貼文。")
        return

    new_tweets = (
        new_tweets[
            :config.MAX_POSTS_PER_RUN
        ]
    )

    # 舊 → 新
    new_tweets.sort(
        key=lambda x:
            int(x["id"])
    )

    count = 0

    for tweet in new_tweets:
        send_to_discord(
            tweet
        )

        if (
            tweet["id"]
            not in seen_set
        ):
            seen.append(
                tweet["id"]
            )

            seen_set.add(
                tweet["id"]
            )

        save_state(
            seen,
            user_id,
        )

        count += 1

    print(
        f"完成，共發送 "
        f"{count} 條。"
    )


if __name__ == "__main__":
    main()
