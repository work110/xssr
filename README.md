# X → Discord 完整容錯版 v3

這版專門處理你遇到的：

429 Too Many Requests

---

## 主要改動

### 1. 429 自動重試

收到 429 時：

- 優先讀 Retry-After
- 沒有 Retry-After 就使用指數退避
- 每次加少量隨機等待
- 最多重試 4 次

### 2. 多公開入口 fallback

會依序嘗試：

- /srv/timeline-profile/screen-name/...
- /timeline/profile?screen_name=...

任一成功就繼續。

### 3. GitHub Actions 執行前隨機延遲

每次先隨機等待：

10～50 秒

避免所有 GitHub Actions 在固定分鐘一起撞 X。

### 4. 降低排程頻率

原本：

每 30 分鐘

現在：

每小時 07 分
每小時 52 分

約每 45 分鐘。

這是為了降低 429 機率。

如果後面證明很穩，可以再改回 30 分鐘。

### 5. state.json

只在成功解析後才更新。

抓 X 失敗不會破壞之前的去重資料。

---

## GitHub Secret

只需要：

DISCORD_WEBHOOK_URL

---

## 要覆蓋的檔案

main.py
requirements.txt
.github/workflows/x-to-discord.yml
README.md

---

## 測試

Push 後：

Actions
→ X to Discord
→ Run workflow

正常情況：

請求 ...
✓ 解析到 X 條正式貼文
✓ 本次判定最新：...
沒有新貼文。

如果遇到 429：

⚠️ 收到 429 Too Many Requests
等待 xx 秒後重試

程式會自己處理。
