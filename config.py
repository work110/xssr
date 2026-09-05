# =========================
# 主要設定集中在這裡
# =========================

# X 帳號，不要加 @
X_USERNAME = "WhereWindsMeet_"

# TwitterAPI.io
TWITTER_USER_INFO_URL = "https://api.twitterapi.io/twitter/user/info"
TWITTER_TIMELINE_URL = "https://api.twitterapi.io/twitter/user/tweet_timeline"

# 是否包含回覆
INCLUDE_REPLIES = False

# 是否排除 Retweet
EXCLUDE_RETWEETS = True

# DeepL Free
# 如果你用的是 DeepL API Pro，改成：
# https://api.deepl.com/v2/translate
DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_TARGET_LANG = "ZH-HANT"

# Discord 顯示
DISCORD_USERNAME = "燕雲官方情報"
DISCORD_TITLE = "燕雲十六聲｜官方 X 更新"
DISCORD_FOOTER = "來源：Where Winds Meet 官方 X"

# 第一次執行是否發最新一條
SEND_LATEST_ON_FIRST_RUN = True

# 一次最多補發幾條
MAX_POSTS_PER_RUN = 5

# state.json 最多保留多少條 ID
MAX_SEEN_IDS = 200

# HTTP
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
