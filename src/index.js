import puppeteer from "@cloudflare/puppeteer";

const X_HANDLE = "WhereWindsMeet_";
const PROFILE_URL = `https://x.com/${X_HANDLE}`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/run") {
      try {
        const result = await checkAndPost(env, true);
        return Response.json({ ok: true, result });
      } catch (e) {
        console.error(e);
        return Response.json(
          { ok: false, error: String(e?.stack || e) },
          { status: 500 }
        );
      }
    }

    return new Response(
      "Where Winds Meet X → Discord Browser Run Worker is running.\nUse /run to test."
    );
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(
      checkAndPost(env, false).catch((e) => {
        console.error("Scheduled task failed:", e);
      })
    );
  },
};


async function checkAndPost(env, manual = false) {
  if (!env.BROWSER) {
    throw new Error("尚未設定 BROWSER Browser Run binding");
  }

  if (!env.STATE) {
    throw new Error("尚未設定 STATE KV binding");
  }

  if (!env.DISCORD_WEBHOOK_URL) {
    throw new Error("尚未設定 DISCORD_WEBHOOK_URL Secret");
  }

  const tweets = await fetchTweetsWithBrowser(env);

  if (!tweets.length) {
    throw new Error("瀏覽器成功打開 X，但沒有解析到任何正式貼文");
  }

  const latest = tweets[0];

  console.log("Latest:", latest.id, latest.datetime, latest.url);
  console.log("Text:", latest.text.slice(0, 180));

  const oldId = await env.STATE.get("last_tweet_id");

  if (!oldId) {
    await env.STATE.put("last_tweet_id", latest.id);

    if (manual) {
      await sendTweet(env, latest);
      return `First run. Sent latest tweet: ${latest.id}`;
    }

    return `First scheduled run. Baseline saved: ${latest.id}`;
  }

  if (oldId === latest.id) {
    return `No new tweet. Latest remains ${latest.id}`;
  }

  let newTweets = tweets.filter((t) => {
    try {
      return BigInt(t.id) > BigInt(oldId);
    } catch {
      return t.id !== oldId;
    }
  });

  const oldVisible = tweets.some((t) => t.id === oldId);

  if (!oldVisible && newTweets.length > 3) {
    console.log(
      `Old state ${oldId} not visible; ${newTweets.length} newer items found.`
    );

    if (manual) {
      newTweets = [latest];
    } else {
      newTweets = newTweets.slice(0, 3);
    }
  }

  if (!newTweets.length) {
    await env.STATE.put("last_tweet_id", latest.id);
    return `State corrected. Latest baseline: ${latest.id}`;
  }

  newTweets.sort((a, b) => {
    if (a.timestamp !== b.timestamp) {
      return a.timestamp - b.timestamp;
    }

    const aa = BigInt(a.id);
    const bb = BigInt(b.id);

    if (aa === bb) return 0;
    return aa < bb ? -1 : 1;
  });

  for (const tweet of newTweets) {
    await sendTweet(env, tweet);
  }

  await env.STATE.put("last_tweet_id", latest.id);

  return `Sent ${newTweets.length} new tweet(s). Latest: ${latest.id}`;
}


async function fetchTweetsWithBrowser(env) {
  const browser = await puppeteer.launch(env.BROWSER);

  try {
    const page = await browser.newPage();

    await page.setViewport({
      width: 1280,
      height: 1600,
      deviceScaleFactor: 1,
    });

    await page.setExtraHTTPHeaders({
      "Accept-Language": "en-US,en;q=0.9",
    });

    console.log("Opening:", PROFILE_URL);

    const response = await page.goto(PROFILE_URL, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });

    console.log("X page HTTP:", response?.status?.() ?? "unknown");

    try {
      await page.waitForSelector('article[data-testid="tweet"]', {
        timeout: 15000,
      });
    } catch {
      await new Promise((r) => setTimeout(r, 5000));
    }

    await page.evaluate(() => window.scrollBy(0, 700));
    await new Promise((r) => setTimeout(r, 1800));
    await page.evaluate(() => window.scrollTo(0, 0));
    await new Promise((r) => setTimeout(r, 800));

    const raw = await page.evaluate((handle) => {
      const articles = [...document.querySelectorAll(
        'article[data-testid="tweet"]'
      )];

      return articles.map((article) => {
        const statusLinks = [...article.querySelectorAll(
          `a[href*="/${handle}/status/"]`
        )];

        const statusLink = statusLinks.find(
          (a) => /\/status\/\d+/.test(a.getAttribute("href") || "")
        );

        if (!statusLink) return null;

        const href = statusLink.getAttribute("href") || "";
        const match = href.match(/\/status\/(\d+)/);

        if (!match) return null;

        const id = match[1];

        const timeEl = article.querySelector("time");
        const datetime = timeEl?.getAttribute("datetime") || null;

        const textEl = article.querySelector(
          '[data-testid="tweetText"]'
        );

        const text = (textEl?.innerText || "").trim();

        const fullText = article.innerText || "";
        const isReply =
          /Replying to/i.test(fullText) ||
          /回覆給|正在回覆/i.test(fullText);

        const images = [...article.querySelectorAll("img")]
          .map((img) => img.src)
          .filter((src) =>
            src &&
            (
              src.includes("pbs.twimg.com/media/") ||
              src.includes("pbs.twimg.com/ext_tw_video_thumb/") ||
              src.includes("pbs.twimg.com/amplify_video_thumb/")
            )
          );

        return {
          id,
          text,
          datetime,
          isReply,
          image: images[0] || null,
          url: `https://x.com/${handle}/status/${id}`,
        };
      }).filter(Boolean);
    }, X_HANDLE);

    console.log("DOM candidate tweets:", raw.length);

    const map = new Map();

    for (const tweet of raw) {
      if (!tweet.id) continue;
      if (tweet.isReply) continue;
      if (!tweet.text) continue;

      const timestamp = tweet.datetime
        ? Date.parse(tweet.datetime)
        : decodeTweetSnowflake(tweet.id);

      map.set(tweet.id, {
        ...tweet,
        timestamp: Number.isFinite(timestamp) ? timestamp : 0,
      });
    }

    const tweets = [...map.values()];

    tweets.sort((a, b) => {
      if (a.timestamp !== b.timestamp) {
        return b.timestamp - a.timestamp;
      }

      const aa = BigInt(a.id);
      const bb = BigInt(b.id);

      if (aa === bb) return 0;
      return aa > bb ? -1 : 1;
    });

    console.log(
      "Parsed:",
      tweets.slice(0, 8).map((t) => ({
        id: t.id,
        datetime: t.datetime,
        text: t.text.slice(0, 60),
      }))
    );

    if (!tweets.length) {
      const title = await page.title();
      const body = await page.evaluate(
        () => document.body?.innerText?.slice(0, 1500) || ""
      );

      console.log("Page title:", title);
      console.log("Page body preview:", body);
    }

    return tweets;

  } finally {
    await browser.close();
  }
}


function decodeTweetSnowflake(id) {
  try {
    return Number((BigInt(id) >> 22n) + 1288834974657n);
  } catch {
    return 0;
  }
}


async function translateToZhTW(text) {
  for (const endpoint of [
    "https://translate.googleapis.com/translate_a/single",
    "https://translate.google.com/translate_a/single",
  ]) {
    try {
      const url =
        endpoint +
        "?client=gtx" +
        "&sl=auto" +
        "&tl=zh-TW" +
        "&dt=t" +
        "&q=" +
        encodeURIComponent(text);

      const response = await fetch(url, {
        headers: {
          "User-Agent": "Mozilla/5.0",
        },
      });

      console.log("Translate:", endpoint, response.status);

      if (!response.ok) continue;

      const data = await response.json();

      const result = data?.[0]
        ?.map((x) => x?.[0] || "")
        .join("")
        .trim();

      if (validTranslation(result)) {
        return result;
      }

    } catch (e) {
      console.log("Google translation error:", String(e));
    }
  }

  try {
    const url =
      "https://api.mymemory.translated.net/get" +
      "?q=" + encodeURIComponent(text.slice(0, 450)) +
      "&langpair=en|zh-TW";

    const response = await fetch(url);

    console.log("MyMemory:", response.status);

    if (response.ok) {
      const data = await response.json();
      const result = data?.responseData?.translatedText?.trim();

      if (validTranslation(result)) {
        return result;
      }
    }
  } catch (e) {
    console.log("MyMemory error:", String(e));
  }

  return "⚠️ 中文翻譯暫時失敗，請查看下方原文。";
}


function validTranslation(value) {
  if (!value) return false;

  const lower = String(value).toLowerCase();

  return ![
    "error 500",
    "server error",
    "that's an error",
    "bad gateway",
    "service unavailable",
    "<html",
    "<!doctype",
  ].some((x) => lower.includes(x));
}


async function sendTweet(env, tweet) {
  const translated = await translateToZhTW(tweet.text);

  const embed = {
    title: "燕雲十六聲｜官方 X 更新",
    url: tweet.url,
    description: translated.slice(0, 3500),

    fields: [
      {
        name: "原文",
        value: tweet.text.slice(0, 900),
        inline: false,
      },
    ],

    footer: {
      text: "來源：Where Winds Meet 官方 X · Cloudflare Browser Run",
    },

    timestamp: tweet.datetime || undefined,
  };

  if (tweet.image) {
    embed.image = { url: tweet.image };
  }

  const response = await fetch(
    env.DISCORD_WEBHOOK_URL,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        username: "燕雲官方情報",
        embeds: [embed],
        allowed_mentions: { parse: [] },
      }),
    }
  );

  if (!response.ok) {
    throw new Error(
      `Discord HTTP ${response.status}: ${await response.text()}`
    );
  }
}
