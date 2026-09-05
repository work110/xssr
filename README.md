# WWM X → DeepL → Discord

這版改用 TwitterAPI.io 官方建議的兩步：

1. `/twitter/user/info?userName=...`
2. `/twitter/user/tweet_timeline?userId=...`

比直接 `last_tweets` 更容易診斷。

## GitHub Secrets

需要：

- TWITTER_API_KEY
- DEEPL_API_KEY
- DISCORD_WEBHOOK_URL

## 可修改設定

集中在：

`config.py`

## 排程

每 3 小時一次：

`7 */3 * * *`

## 第一次測試

如果你希望強制重新測試最新一條：

把 `state.json` 改成：

```json
{
  "seen": [],
  "user_id": null
}
```

然後手動 Run workflow。

## 日誌

正常會看到：

- Resolved user
- Raw tweets returned
- Replies removed
- Retweets removed
- Usable tweets
- Latest usable tweet
- DeepL HTTP
- Discord HTTP
