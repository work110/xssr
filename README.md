# WWM X → DeepL → Discord

Fix:
TwitterAPI.io returns tweets under:

`data.tweets`

not necessarily top-level:

`tweets`

This version supports both.

Schedule:
every 3 hours.

Secrets:
- TWITTER_API_KEY
- DEEPL_API_KEY
- DISCORD_WEBHOOK_URL
