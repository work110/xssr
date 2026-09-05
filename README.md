# X → 中文翻譯 → Discord Webhook

用途：每 30 分鐘由 GitHub Actions 讀取一次 RSS，發現新貼文後翻譯為繁體中文並推送到 Discord。

目標帳號：
- https://x.com/WhereWindsMeet_

## 1. 建立 GitHub Repository

建立一個新的 repository，例如：

`where-winds-meet-discord`

把本資料夾全部檔案上傳。

注意 `.github/workflows/x-to-discord.yml` 也必須存在。

## 2. 建立 Discord Webhook

Discord：
頻道設定 → 整合 → Webhooks → 新增 Webhook → 複製 Webhook URL

不要把 Webhook URL 寫進程式碼。

## 3. 設定 GitHub Secrets

GitHub Repository：

Settings → Secrets and variables → Actions → New repository secret

新增：

### DISCORD_WEBHOOK_URL

值：
你的 Discord Webhook URL

### RSS_URL

值：
能提供 @WhereWindsMeet_ 貼文的 RSS Feed URL。

例如你自己的 RSSHub / Nitter / 其他 RSS 服務所提供的 feed。

程式刻意不綁死某個公共服務，因為 X 的第三方 RSS 來源經常變動。

## 4. 第一次測試

GitHub：
Actions → X to Discord → Run workflow

目前設定：

`SEND_LATEST_ON_FIRST_RUN: "true"`

所以第一次執行會把 RSS 裡目前最新一條貼文發送到 Discord，方便測試。

測試完成後，建議改成：

`SEND_LATEST_ON_FIRST_RUN: "false"`

其實只要 state.json 已經建立，之後 true/false 都不影響正常去重。

## 5. 執行頻率

Workflow：

`7,37 * * * *`

代表每小時第 07、37 分執行，即約每 30 分鐘一次。

刻意沒有使用 00、30 分，以減少 GitHub Actions 整點排隊的可能性。

## 6. Discord 顯示

Webhook 顯示名稱預設：

`燕雲官方情報`

每條消息包含：

- 中文翻譯
- 英文原文
- 原帖連結
- RSS 中能解析到的首張圖片（若有）

## 7. 翻譯

目前使用：

`deep-translator`

透過 Google 翻譯的非官方方式，不需要 API Key。

優點：免費、設定簡單。

缺點：不是 Google 官方付費 API，因此未來有可能失效。

如果之後你想換成 OpenAI / DeepL / Google Cloud Translation，只需要替換 main.py 裡的 `translate_zh_tw()`。

## 8. 去重

`state.json` 保存已見過的 feed item ID。

GitHub Actions 執行後會自動 commit 回 repository。

所以：
- 電腦可以關機
- GitHub 負責執行
- 不需要 VPS
- 不會每次重發全部舊貼文

## 安全

請一定把以下內容放 GitHub Secrets：

- Discord Webhook URL
- 任何 RSSHub / X token

不要 commit 到公開 repository。
