# X → 中文翻譯 → Discord Webhook（無 RSS 版）

這一版完全移除 RSSHub。

流程：

X 公開嵌入時間線
↓
GitHub Actions
↓
解析最新貼文
↓
state.json 去重
↓
Google / MyMemory 翻譯
↓
Discord Webhook

目標帳號：

@WhereWindsMeet_

---

## GitHub Secret

現在只需要：

DISCORD_WEBHOOK_URL

舊的 RSS_URL / RSS_URLS 可以刪掉，不再使用。

---

## 抓取方式

程式會讀取 X/Twitter 的公開 profile syndication 頁：

https://syndication.twitter.com/srv/timeline-profile/screen-name/WhereWindsMeet_

從頁面中的 __NEXT_DATA__ JSON 抽取貼文。

這不需要：

- X API key
- OAuth
- X 登入
- RSSHub

注意：

這屬於 X 的公開嵌入/展示端點，不是正式的開發者 timeline API。
如果 X 未來修改頁面結構，程式仍可能需要更新。

---

## 過濾

目前：

- 只保留 @WhereWindsMeet_ 自己的貼文
- 排除 Reply
- 不依賴 RSS 的 retweet/filter 路由

---

## 翻譯

第一順位：

GoogleTranslator

失敗時自動：

MyMemoryTranslator

如果兩個都失敗：

Discord 會顯示：

⚠️ 中文翻譯暫時失敗，請查看下方原文。

不會再把 Error 500 HTML 當成翻譯內容。

---

## 執行時間

每小時：

07 分
37 分

約每 30 分鐘一次。

---

## 第一次測試

GitHub：

Actions
→ X to Discord
→ Run workflow

目前：

SEND_LATEST_ON_FIRST_RUN=true

第一次成功抓取時會把目前最新一條發到 Discord，
方便確認整條鏈路。

之後 state.json 會防止重複發送。

---

## 你需要覆蓋的檔案

main.py
requirements.txt
.github/workflows/x-to-discord.yml
README.md

舊 RSS Secret 已經不再需要。
