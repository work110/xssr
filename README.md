# X → 中文翻譯 → Discord Webhook（多 RSS + 翻譯容錯 + 新鮮度檢查）

這一版新增兩層容錯：

1. RSS 多來源容錯
2. 翻譯容錯
3. RSS 新鮮度檢查

---

## RSS 新鮮度檢查

預設：

MAX_FEED_AGE_DAYS=14

如果某個 RSSHub 可以打開，但最新項目已經超過 14 天，
程式會把它視為「可能停更」並自動嘗試下一個來源。

如果 RSS 本身沒有可靠日期，程式不會直接淘汰它，
避免正常來源被誤判。

---

## 翻譯容錯

第一順位：

GoogleTranslator

如果出現：

- Error 500
- Server Error
- HTML 錯誤頁
- Service Unavailable
- Bad Gateway

就自動切換：

MyMemoryTranslator

如果第二個也失敗，就不再把錯誤頁發進 Discord，
而是顯示：

⚠️ 中文翻譯暫時失敗，請查看下方原文。

---

## GitHub Secrets

需要：

DISCORD_WEBHOOK_URL

RSS_URLS

RSS_URLS 內容可以是一行一個來源。

例如：

https://rsshub.stsecurity.moe/twitter/user/WhereWindsMeet_/exclude_rts_replies
https://rsshub.edwardcc.com/twitter/user/WhereWindsMeet_/exclude_rts_replies
https://rsshub.isrss.com/twitter/user/WhereWindsMeet_/exclude_rts_replies
https://rsshub.yfi.moe/twitter/user/WhereWindsMeet_/exclude_rts_replies

---

## GitHub Actions

每小時：

07 分
37 分

執行。

使用：

actions/checkout@v5
actions/setup-python@v6

避免舊版 Node.js 20 warning。
