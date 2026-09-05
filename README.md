# WWM GitHub TwitterAPI.io fallback version

This version tries:

1. `tweet_timeline?userId=...`
2. If empty: `last_tweets?userId=...`

It also logs top-level API status/message/keys so an empty response can be diagnosed accurately.

Secrets:
- TWITTER_API_KEY
- DEEPL_API_KEY
- DISCORD_WEBHOOK_URL

Schedule:
- every 3 hours
