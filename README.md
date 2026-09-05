# X → 中文翻譯 → Discord Webhook（多 RSS 容錯版）

用途：

每 30 分鐘由 GitHub Actions 自動執行一次。

流程：

X 官方帳號
↓
RSS 來源 1
↓ 失敗
RSS 來源 2
↓ 失敗
RSS 來源 3
↓
成功讀取
↓
判斷是否新貼文
↓
繁體中文翻譯
↓
Discord Webhook

目標帳號：

https://x.com/WhereWindsMeet_

---

# 1. GitHub Secrets

Repository：

Settings
→ Secrets and variables
→ Actions
→ New repository secret

建立兩個 Secret。

## DISCORD_WEBHOOK_URL

填入 Discord Webhook URL。

例如：

https://discord.com/api/webhooks/xxxxxxxx/xxxxxxxx

注意：不要把它直接寫進程式碼。

---

## RSS_URLS

建立名為：

RSS_URLS

的 Secret。

內容直接貼下面這幾行：

https://rsshub.stsecurity.moe/twitter/user/WhereWindsMeet_/exclude_rts_replies
https://rsshub.edwardcc.com/twitter/user/WhereWindsMeet_/exclude_rts_replies
https://rsshub.isrss.com/twitter/user/WhereWindsMeet_/exclude_rts_replies
https://rsshub.yfi.moe/twitter/user/WhereWindsMeet_/exclude_rts_replies

一行一個網址即可。

程式會從第一個開始測試：

1. stsecurity
2. edwardcc
3. isrss
4. yfi

只要其中一個成功，就會使用該來源。

如果全部失敗，這一次 GitHub Actions 會報錯，但不會破壞去重資料。

---

# 2. RSS 路由

目前使用：

/twitter/user/WhereWindsMeet_/exclude_rts_replies

意思：

- 抓取 @WhereWindsMeet_
- 排除 Retweet
- 排除 Reply

所以 Discord 裡主要留下官方自己的正式貼文。

---

# 3. 每 30 分鐘執行

Workflow 使用：

7,37 * * * *

也就是：

每小時 07 分
每小時 37 分

執行一次。

沒有使用 00 / 30，是為了避開 GitHub Actions 整點較容易排隊的時間。

---

# 4. 第一次測試

GitHub：

Actions
→ X to Discord
→ Run workflow

目前：

SEND_LATEST_ON_FIRST_RUN: "true"

因此第一次會把 RSS 裡的最新一條直接發到 Discord。

這方便確認：

- RSS 是否成功
- 翻譯是否成功
- Webhook 是否成功
- Discord 排版是否正常

成功之後可以把 workflow 裡：

SEND_LATEST_ON_FIRST_RUN: "true"

改為：

SEND_LATEST_ON_FIRST_RUN: "false"

不改其實也沒關係，因為 state.json 建立後就會正常去重。

---

# 5. 自動容錯

main.py 會依序測試 RSS_URLS。

成功條件：

- HTTP 狀態正常
- RSS 可以解析
- 至少存在 1 個 feed item

例如：

rsshub.stsecurity.moe
↓ 503
rsshub.edwardcc.com
↓ 502
rsshub.isrss.com
↓ 200 + RSS 正常
✓ 使用 isrss

所以不會因為單一公共 RSSHub 掛掉就立即停止工作。

---

# 6. 去重

程式使用：

state.json

記錄最近 100 個項目的 hash。

即使不同 RSSHub 對同一條貼文的 RSS 格式略有差別，
程式會優先依照：

id
guid
link

生成唯一 ID。

每成功推送一條消息，就立刻保存一次 state。

這可以避免：

例如一次抓到 3 條新貼文，
成功發了前 2 條，
第 3 條發送失敗，

下一次執行時把前 2 條再發一次。

---

# 7. Discord 顯示

預設 Webhook 名稱：

燕雲官方情報

消息包含：

- 中文翻譯
- 英文原文
- 原帖連結
- RSS 能取得時顯示圖片
- 底部顯示本次使用哪個 RSSHub

例如：

燕雲十六聲｜官方 X 更新

【中文】
全新的活動即將開始……

原文
A new event is coming...

來源：Where Winds Meet 官方 X · RSS：rsshub.isrss.com

---

# 8. 翻譯

目前使用：

deep-translator

透過 Google Translate 的非官方方式翻譯為：

繁體中文 zh-TW

不需要 API Key。

優點：

免費、方便。

缺點：

不是 Google Cloud Translation 官方 API，
因此未來可能受限制。

---

# 9. 電腦可以關機

整個流程全部在 GitHub 執行：

GitHub Actions
→ RSS
→ Google 翻譯
→ Discord

你的 Windows 電腦不需要開機。

---

# 10. 安全提醒

以下資料只放 GitHub Secrets：

- DISCORD_WEBHOOK_URL
- RSS_URLS
- 未來如果使用私人 RSSHub Token，也放 Secrets

不要把 Discord Webhook URL 提交到公開 GitHub Repository。
