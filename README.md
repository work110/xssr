# Where Winds Meet X → Discord (Cloudflare Browser Run)

This is a full Wrangler/npm project for deployment from GitHub to Cloudflare Workers.

## Files

- `package.json`
- `wrangler.jsonc`
- `src/index.js`

## Required Cloudflare resources

### Browser binding

Binding name:

`BROWSER`

### Workers KV

Binding name:

`STATE`

Namespace:

your existing `where-winds-meet-state`

In `wrangler.jsonc`, replace:

`REPLACE_WITH_YOUR_KV_NAMESPACE_ID`

with the real namespace ID shown by Cloudflare Workers KV.

### Secret

Create this in the Cloudflare Worker dashboard:

`DISCORD_WEBHOOK_URL`

Do NOT put the Discord webhook URL into GitHub or `wrangler.jsonc`.

## Cron

Current schedule:

`7,37 * * * *`

Runs about every 30 minutes.

## Deploy with GitHub

1. Create a new GitHub repository.
2. Upload all files from this project.
3. In Cloudflare Workers & Pages, connect the repository.
4. Build command:
   `npm run deploy`
5. Cloudflare will install dependencies, including `@cloudflare/puppeteer`.
6. Keep/add your `DISCORD_WEBHOOK_URL` secret.
7. Deploy.

## Manual test

Visit:

`https://where-winds-meet-x.<your-subdomain>.workers.dev/run`

Expected success response:

```json
{"ok":true,"result":"..."}
```

## Important

The first deploy requires the correct KV namespace ID in `wrangler.jsonc`.
