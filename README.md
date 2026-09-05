# X → Discord（無 RSS v2：修正置頂貼文）

這版專門修正「把置頂貼文當成最新貼文」的問題。

核心改動：

1. 不再遞迴掃描整個 __NEXT_DATA__
2. 直接讀：
   props.pageProps.timeline.entries
3. 每個 entry 只取：
   content.tweet
4. 使用 X 自己提供的：
   props.pageProps.latest_tweet_id
   驗證真正最新貼文
5. 置頂貼文不會再覆蓋真正最新貼文
6. Quote Tweet 會保留官方帳號的頂層評論文字
7. 30 分鐘內如果連發多條，會全部依時間順序推送

GitHub Secret 仍只需要：

DISCORD_WEBHOOK_URL

舊 RSS_URL / RSS_URLS 不使用。

## 關於 state.json

如果你想立刻重新測試「第一次執行」：

可以刪除 repository 根目錄的 state.json，
然後再手動 Run workflow。

如果不刪也可以，程式會把 timeline 中未出現在 state.json 的新 tweet 找出來並發送。
