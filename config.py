# =========================
# 主要設定集中在這裡
# =========================

import os

# GitHub Actions repository Variables (also usable as local environment variables).
X_USERNAME = os.environ.get("X_USERNAME", "").strip() or "WhereWindsMeet_"
YOUTUBE_CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
OFFICIAL_NEWS_ENABLED = os.environ.get("OFFICIAL_NEWS_ENABLED", "true").strip().lower() not in ("false", "0", "no")
OFFICIAL_NEWS_MAX_PAGES = 10

TWITTER_USER_INFO_URL = "https://api.twitterapi.io/twitter/user/info"
TWITTER_TIMELINE_URL = "https://api.twitterapi.io/twitter/user/tweet_timeline"
TWITTER_LAST_TWEETS_URL = "https://api.twitterapi.io/twitter/user/last_tweets"

INCLUDE_REPLIES = False
EXCLUDE_RETWEETS = True

DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_TARGET_LANG = "ZH-HANT"

DISCORD_USERNAME = "官方動態通知"
DISCORD_TITLE = "X 更新"
DISCORD_FOOTER = "來源：X"

SEND_LATEST_ON_FIRST_RUN = True
MAX_POSTS_PER_RUN = 5
MAX_SEEN_IDS = 200

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
