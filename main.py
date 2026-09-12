import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

STATE_FILE = Path("state.json")

TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "").strip()
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "").strip()
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_WEBHOOK_URL_2 = os.environ.get("DISCORD_WEBHOOK_URL_2", "").strip()


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
    state = load_state()
    if state.get("user_id"):
        state.setdefault("x_accounts", {}).setdefault(str(state["user_id"]), state.get("seen", []))
    state.update({
        "user_id": user_id,
        "seen": seen[-config.MAX_SEEN_IDS:],
    })
    state.setdefault("x_accounts", {})[user_id] = state["seen"]
    write_state(state)


def write_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(
        json.dumps(
            state,
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
            f"@{config.X_USERNAME}｜{config.DISCORD_TITLE}",

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
                f"{config.DISCORD_FOOTER} @{config.X_USERNAME}"
        },
    }

    image = extract_image(tweet)

    if image:
        embed["image"] = {
            "url": image
        }

    send_discord_embed(embed)
    print("Discord sent:", tweet["id"])


def send_discord_embed(embed):
    print("[5/5] Sending Discord")

    payload = {
        "username": config.DISCORD_USERNAME,
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    # Ignore an empty second webhook and avoid sending twice to the same URL.
    webhook_urls = list(dict.fromkeys(
        url for url in (DISCORD_WEBHOOK_URL, DISCORD_WEBHOOK_URL_2) if url
    ))
    failures = []
    for channel, webhook_url in enumerate(webhook_urls, start=1):
        try:
            r = SESSION.post(
                webhook_url,
                json=payload,
                timeout=config.REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            # Request exceptions can contain the secret webhook URL.
            failures.append(f"channel {channel}: request failed")
            continue

        print(f"Discord channel {channel} HTTP {r.status_code}")
        if not r.ok:
            failures.append(f"channel {channel}: HTTP {r.status_code}")

    if failures:
        raise RuntimeError("Discord delivery failed: " + "; ".join(failures))

def normalize_x_username(value):
    value = value.strip()
    if "://" in value or value.startswith(("x.com/", "twitter.com/", "www.x.com/", "www.twitter.com/")):
        parsed = urlparse(value if "://" in value else "https://" + value)
        if parsed.hostname not in ("x.com", "www.x.com", "twitter.com", "www.twitter.com"):
            raise ValueError("X_USERNAME 必須是 X 帳號或 x.com/twitter.com 個人頁面連結")
        value = parsed.path.strip("/")
    value = value.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        raise ValueError("X_USERNAME 必須是 X 帳號或個人頁面連結")
    return value


def run_x():
    config.X_USERNAME = normalize_x_username(config.X_USERNAME)
    validate_secrets()

    state = load_state()

    user_id = resolve_user_id()

    # Migrate the old single-account state only when its user ID matches.
    account_seen = state.get("x_accounts", {}).get(user_id)
    if account_seen is None:
        account_seen = state.get("seen", []) if str(state.get("user_id")) == user_id else []
    state["seen"] = account_seen

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


def normalize_youtube_channel(value):
    value = value.strip()
    if "://" in value or value.startswith(("youtube.com/", "www.youtube.com/")):
        parsed = urlparse(value if "://" in value else "https://" + value)
        if parsed.hostname not in ("youtube.com", "www.youtube.com"):
            raise ValueError("YOUTUBE_CHANNEL_ID 必須是 YouTube 頻道 ID、handle 或頻道連結")
        path = unquote(parsed.path).strip("/").split("/")
        if len(path) == 2 and path[0] == "channel":
            value = path[1]
            if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", value):
                raise ValueError("YouTube /channel/ 連結內的頻道 ID 格式錯誤")
        elif len(path) == 1 and path[0].startswith("@"):
            value = path[0]
        else:
            raise ValueError("請使用 YouTube /@handle 或 /channel/UC... 頻道連結")
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", value):
        return value
    handle = value.removeprefix("@")
    if not re.fullmatch(r"[\w.\-·]{1,100}", handle):
        raise ValueError("YOUTUBE_CHANNEL_ID 請填頻道 ID、handle（例如 WhereWindsMeet）或頻道連結")
    print("YouTube: resolving handle to channel ID")
    data = youtube_api_request("channels", {"part": "id", "forHandle": handle})
    items = data.get("items", [])
    if len(items) != 1 or not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", items[0].get("id", "")):
        raise RuntimeError("YouTube API 找不到該 handle 對應的頻道")
    print("YouTube: channel ID resolved via Data API")
    return items[0]["id"]


def youtube_api_request(resource, params):
    if not YOUTUBE_API_KEY:
        raise RuntimeError("缺少 GitHub Secrets: YOUTUBE_API_KEY")
    try:
        response = SESSION.get(
            f"https://www.googleapis.com/youtube/v3/{resource}",
            params={**params, "key": YOUTUBE_API_KEY},
            timeout=config.REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        # Never include a request URL containing the API key in a traceback.
        raise RuntimeError(f"YouTube Data API {resource}: network request failed") from None
    if not response.ok:
        raise RuntimeError(
            f"YouTube Data API {resource} HTTP {response.status_code}; "
            "請檢查 YOUTUBE_API_KEY、YouTube Data API v3 是否已啟用，以及金鑰限制和配額"
        )
    return response.json()


def fetch_youtube_videos(channel_id):
    print("YouTube: using Data API")
    data = youtube_api_request("channels", {"part": "contentDetails", "id": channel_id})
    channels = data.get("items", [])
    if len(channels) != 1 or channels[0].get("id") != channel_id:
        raise RuntimeError("YouTube API 找不到指定頻道")
    uploads = channels[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads:
        raise RuntimeError("YouTube API 未返回頻道的上傳播放清單")
    data = youtube_api_request("playlistItems", {
        "part": "snippet,contentDetails,status", "playlistId": uploads, "maxResults": 50,
    })
    videos = {}
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        details = item.get("contentDetails", {})
        video_id = details.get("videoId", "")
        published = details.get("videoPublishedAt", "")
        title = snippet.get("title", "")
        if (item.get("status", {}).get("privacyStatus") != "public"
                or snippet.get("videoOwnerChannelId") != channel_id
                or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
                or not published or not title):
            continue
        videos[video_id] = {
            "id": video_id, "title": title,
            "published": datetime.fromisoformat(published.replace("Z", "+00:00")),
            "author": snippet.get("videoOwnerChannelTitle") or channel_id,
        }
    return sorted(videos.values(), key=lambda video: video["published"])


def run_youtube():
    if not config.YOUTUBE_CHANNEL_ID:
        print("YouTube tracking disabled: YOUTUBE_CHANNEL_ID is empty")
        return
    if not YOUTUBE_API_KEY:
        raise RuntimeError("缺少 GitHub Secrets: YOUTUBE_API_KEY")
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("缺少 GitHub Secrets: DISCORD_WEBHOOK_URL")
    channel_id = normalize_youtube_channel(config.YOUTUBE_CHANNEL_ID)
    videos = fetch_youtube_videos(channel_id)
    if not videos:
        print("YouTube: no public videos returned by Data API")
        return
    state = load_state()
    accounts = state.setdefault("youtube_channels", {})
    first_run = channel_id not in accounts
    seen = list(accounts.get(channel_id, []))
    if first_run:
        pending = videos[-1:] if config.SEND_LATEST_ON_FIRST_RUN else []
    else:
        pending = [video for video in videos if video["id"] not in set(seen)][:config.MAX_POSTS_PER_RUN]
    for video in pending:
        translated = video["title"]
        if DEEPL_API_KEY:
            try:
                translated = translate_with_deepl(video["title"])
            except Exception:
                print("YouTube title translation failed; using original title")
        send_discord_embed({
            "title": f"{video['author']}｜YouTube 更新"[:256],
            "url": f"https://www.youtube.com/watch?v={video['id']}",
            "description": translated[:4000],
            "fields": [{"name": "原標題", "value": video["title"][:1000], "inline": False}],
            "image": {"url": f"https://i.ytimg.com/vi/{video['id']}/hqdefault.jpg"},
            "footer": {"text": f"來源：YouTube {video['author']}"[:2048]},
        })
        seen.append(video["id"])
        accounts[channel_id] = seen[-config.MAX_SEEN_IDS:]
        write_state(state)
    if first_run:
        accounts[channel_id] = [video["id"] for video in videos][-config.MAX_SEEN_IDS:]
        write_state(state)
    print(f"YouTube: sent {len(pending)} video(s)")


def main():
    failures = []
    for name, track in (("X", run_x), ("YouTube", run_youtube)):
        try:
            track()
        except Exception as exc:
            # Keep one source running even when the other source fails.
            print(f"{name} tracking failed ({type(exc).__name__})")
            if isinstance(exc, (ValueError, RuntimeError)):
                print(str(exc))
            failures.append(name)
    if failures:
        raise RuntimeError("Tracking failed: " + ", ".join(failures))


if __name__ == "__main__":
    main()
