# Where Winds Meet X → DeepL → Discord

GitHub Actions 每 3 小時：

1. TwitterAPI.io 讀取 `@WhereWindsMeet_`
2. 排除 Reply / Retweet
3. `state.json` 去重
4. DeepL 翻譯成繁體中文
5. Discord Webhook 發送

## 需要的 GitHub Secrets

Repository：

Settings → Secrets and variables → Actions → New repository secret

建立：

- `TWITTER_API_KEY`
- `DEEPL_API_KEY`
- `DISCORD_WEBHOOK_URL`

不要把真正 API Key 寫進 `config.py` 或提交到 GitHub。

## 所有常用設定都在 config.py

例如：

- X 帳號
- TwitterAPI.io endpoint
- DeepL endpoint
- DeepL 翻譯語言
- Discord 顯示名稱
- 第一次是否發最新貼文
- 每次最多補發幾條

### DeepL Free

預設：

`https://api-free.deepl.com/v2/translate`

如果你是 DeepL API Pro，改成：

`https://api.deepl.com/v2/translate`

## 排程

`.github/workflows/x-to-discord.yml`

目前：

`7 */3 * * *`

表示 UTC 每 3 小時第 07 分執行一次。

## 第一次測試

GitHub → Actions → WWM X to Discord → Run workflow

第一次成功時會：

- 發目前最新的一條到 Discord
- 把目前 API 返回的貼文 ID 寫進 `state.json`

因此第二次不會重複刷歷史內容。

## 安全

API Key 和 Discord Webhook 一律使用 GitHub Secrets。
