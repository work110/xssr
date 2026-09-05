import puppeteer from "@cloudflare/puppeteer";

const X_HANDLE = "WhereWindsMeet_";
const PROFILE_URL = `https://x.com/${X_HANDLE}`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/run") {
      const diagnostics = {
        hasBrowser: !!env.BROWSER,
        hasState: !!env.STATE,
        hasWebhook: !!env.DISCORD_WEBHOOK_URL,
      };

      try {
        const result = await checkAndPost(env, true);

        return Response.json({
          ok: true,
          diagnostics,
          result,
        });
      } catch (e) {
        console.error("RUN ERROR:", e);

        return Response.json(
          {
            ok: false,
            diagnostics,
            errorName: e?.name || null,
            errorMessage: e?.message || String(e),
            errorStack: e?.stack || null,
          },
          { status: 500 }
        );
      }
    }

    if (url.pathname === "/diagnostics") {
      return Response.json({
        ok: true,
        hasBrowser: !!env.BROWSER,
        hasState: !!env.STATE,
        hasWebhook: !!env.DISCORD_WEBHOOK_URL,
        profile: PROFILE_URL,
      });
    }

    return new Response(
      "Where Winds Meet X → Discord Browser Run Worker is running.\n" +
      "Use /run to execute once.\n" +
      "Use /diagnostics to check bindings."
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
  console.log("checkAndPost: start");

  if (!env.BROWSER) {
    throw new Error("尚未設定 BROWSER Browser Run binding");
  }

  if (!env.STATE) {
    throw new Error("尚未設定 STATE KV binding");
  }

  if (!env.DISCORD_WEBHOOK_URL) {
    throw new Error("尚未設定 DISCORD_WEBHOOK_URL Secret");
  }

  console.log("checkAndPost: bindings OK");

  const tweets = await fetchTweetsWithBrowser(env);

  console.log(`checkAndPost: fetched ${tweets.length} tweets`);

  if (!tweets.length) {
    throw new Error("瀏覽器成功打開 X，但沒有解析到任何正式貼文");
  }

  const latest = tweets[0];

  console.log(
    "Latest:",
    latest.id,
    latest.datetime,
    latest.url
  );

  console.log(
    "Latest text:",
    latest.text.slice(0, 180)
  );

  const oldId = await env.STATE.get("last_tweet_id");

  console.log("Stored last_tweet_id:", oldId);

  // 第一次執行
  if (!oldId) {
    await env.STATE.put("last_tweet_id", latest.id);

    if (manual) {
      console.log("First run: sending latest tweet");
      await sendTweet(env, latest);

      return `First run. Sent latest tweet: ${latest.id}`;
    }

    return `First scheduled run. Baseline saved: ${latest.id}`;
  }

  // 沒有更新
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

  console.log(
    "New candidate IDs:",
    newTweets.map((t) => t.id)
  );

  const oldVisible = tweets.some(
    (t) => t.id === oldId
  );

  /*
   * 從舊 syndication 版本遷移時，
   * KV 可能保存的是置頂舊貼文 ID。
   * 手動測試避免一次刷很多條。
   */
  if (!oldVisible && newTweets.length > 3) {
    console.log(
      `Old state ${oldId} not visible; ` +
      `${newTweets.length} newer items found.`
    );

    if (manual) {
      newTweets = [latest];
    } else {
      newTweets = newTweets.slice(0, 3);
    }
  }

  if (!newTweets.length) {
    console.log(
      "State does not match current timeline; resetting baseline."
    );

    await env.STATE.put(
      "last_tweet_id",
      latest.id
    );

    return `State corrected. Latest baseline: ${latest.id}`;
  }

  // Discord 按舊 → 新發送
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
    console.log("Sending:", tweet.url);
    await sendTweet(env, tweet);
  }

  await env.STATE.put(
    "last_tweet_id",
    latest.id
  );

  return `Sent ${newTweets.length} new tweet(s). Latest: ${latest.id}`;
}


async function fetchTweetsWithBrowser(env) {
  console.log("Browser: launching");

  let browser = null;

  try {
    browser = await puppeteer.launch(env.BROWSER);

    console.log("Browser: launched");

    const page = await browser.newPage();

    console.log("Browser: new page");

    await page.setViewport({
      width: 1280,
      height: 1600,
      deviceScaleFactor: 1,
    });

    await page.setExtraHTTPHeaders({
      "Accept-Language": "en-US,en;q=0.9",
    });

    console.log("Browser: opening", PROFILE_URL);

    const response = await page.goto(
      PROFILE_URL,
      {
        waitUntil: "domcontentloaded",
        timeout: 30000,
      }
    );

    const status =
      response?.status?.() ?? "unknown";

    console.log("X page HTTP:", status);

    try {
      await page.waitForSelector(
        'article[data-testid="tweet"]',
        {
          timeout: 15000,
        }
      );

      console.log("Browser: tweet selector found");
    } catch (e) {
      console.log(
        "Browser: tweet selector timeout:",
        e?.message || String(e)
      );

      await new Promise(
        (r) => setTimeout(r, 5000)
      );
    }

    // 稍微滾動，讓 timeline 多載入幾條
    try {
      await page.evaluate(
        () => window.scrollBy(0, 700)
      );
      await new Promise(
        (r) => setTimeout(r, 1800)
      );

      await page.evaluate(
        () => window.scrollTo(0, 0)
      );
      await new Promise(
        (r) => setTimeout(r, 800)
      );
    } catch (e) {
      console.log(
        "Browser: scroll warning:",
        e?.message || String(e)
      );
    }

    const raw = await page.evaluate(
      (handle) => {
        const articles = [
          ...document.querySelectorAll(
            'article[data-testid="tweet"]'
          ),
        ];

        return articles
          .map((article) => {
            const statusLinks = [
              ...article.querySelectorAll(
                `a[href*="/${handle}/status/"]`
              ),
            ];

            const statusLink =
              statusLinks.find((a) =>
                /\/status\/\d+/.test(
                  a.getAttribute("href") || ""
                )
              );

            if (!statusLink) {
              return null;
            }

            const href =
              statusLink.getAttribute("href") || "";

            const match =
              href.match(/\/status\/(\d+)/);

            if (!match) {
              return null;
            }

            const id = match[1];

            const timeEl =
              article.querySelector("time");

            const datetime =
              timeEl?.getAttribute("datetime") ||
              null;

            const textEl =
              article.querySelector(
                '[data-testid="tweetText"]'
              );

            const text =
              (textEl?.innerText || "").trim();

            const fullText =
              article.innerText || "";

            const isReply =
              /Replying to/i.test(fullText) ||
              /回覆給|正在回覆/i.test(
                fullText
              );

            const images = [
              ...article.querySelectorAll(
                "img"
              ),
            ]
              .map((img) => img.src)
              .filter((src) =>
                src &&
                (
                  src.includes(
                    "pbs.twimg.com/media/"
                  ) ||
                  src.includes(
                    "pbs.twimg.com/ext_tw_video_thumb/"
                  ) ||
                  src.includes(
                    "pbs.twimg.com/amplify_video_thumb/"
                  )
                )
              );

            return {
              id,
              text,
              datetime,
              isReply,
              image: images[0] || null,
              url:
                `https://x.com/${handle}/status/${id}`,
            };
          })
          .filter(Boolean);
      },
      X_HANDLE
    );

    console.log(
      "DOM candidate tweets:",
      raw.length
    );

    const map = new Map();

    for (const tweet of raw) {
      if (!tweet.id) {
        continue;
      }

      if (tweet.isReply) {
        continue;
      }

      if (!tweet.text) {
        continue;
      }

      const timestamp =
        tweet.datetime
          ? Date.parse(tweet.datetime)
          : decodeTweetSnowflake(
              tweet.id
            );

      map.set(
        tweet.id,
        {
          ...tweet,
          timestamp:
            Number.isFinite(timestamp)
              ? timestamp
              : 0,
        }
      );
    }

    const tweets =
      [...map.values()];

    // 真正按時間排序，不按 DOM/pinned 順序
    tweets.sort((a, b) => {
      if (
        a.timestamp !==
        b.timestamp
      ) {
        return (
          b.timestamp -
          a.timestamp
        );
      }

      const aa =
        BigInt(a.id);

      const bb =
        BigInt(b.id);

      if (aa === bb) {
        return 0;
      }

      return aa > bb
        ? -1
        : 1;
    });

    console.log(
      "Parsed:",
      tweets
        .slice(0, 8)
        .map((t) => ({
          id: t.id,
          datetime: t.datetime,
          text: t.text.slice(0, 80),
        }))
    );

    if (!tweets.length) {
      let title = "";
      let body = "";

      try {
        title =
          await page.title();
      } catch {}

      try {
        body =
          await page.evaluate(
            () =>
              document.body?.innerText?.slice(
                0,
                2000
              ) || ""
          );
      } catch {}

      console.log(
        "Page title:",
        title
      );

      console.log(
        "Page body preview:",
        body
      );

      throw new Error(
        `X 頁面沒有解析到貼文。` +
        ` title=${title}; ` +
        ` body=${body.slice(0, 500)}`
      );
    }

    return tweets;

  } catch (e) {
    console.error(
      "Browser phase failed:",
      e
    );

    throw new Error(
      `Browser Run 階段失敗：` +
      `${e?.message || String(e)}`
    );

  } finally {
    if (browser) {
      try {
        await browser.close();
        console.log(
          "Browser: closed"
        );
      } catch (e) {
        console.log(
          "Browser close warning:",
          e?.message || String(e)
        );
      }
    }
  }
}


function decodeTweetSnowflake(id) {
  try {
    return Number(
      (BigInt(id) >> 22n) +
      1288834974657n
    );
  } catch {
    return 0;
  }
}


async function translateToZhTW(text) {
  console.log(
    "Translation: start"
  );

  // Google 免費入口 1 / 2
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

      const response =
        await fetch(
          url,
          {
            headers: {
              "User-Agent":
                "Mozilla/5.0",
            },
          }
        );

      console.log(
        "Translate:",
        endpoint,
        response.status
      );

      if (!response.ok) {
        continue;
      }

      const data =
        await response.json();

      const result =
        data?.[0]
          ?.map(
            (x) =>
              x?.[0] || ""
          )
          .join("")
          .trim();

      if (
        validTranslation(
          result
        )
      ) {
        console.log(
          "Translation: Google success"
        );

        return result;
      }

    } catch (e) {
      console.log(
        "Google translation error:",
        e?.message || String(e)
      );
    }
  }

  // MyMemory 備援
  try {
    const url =
      "https://api.mymemory.translated.net/get" +
      "?q=" +
      encodeURIComponent(
        text.slice(0, 450)
      ) +
      "&langpair=en|zh-TW";

    const response =
      await fetch(url);

    console.log(
      "MyMemory:",
      response.status
    );

    if (response.ok) {
      const data =
        await response.json();

      const result =
        data?.responseData
          ?.translatedText
          ?.trim();

      if (
        validTranslation(
          result
        )
      ) {
        console.log(
          "Translation: MyMemory success"
        );

        return result;
      }
    }
  } catch (e) {
    console.log(
      "MyMemory error:",
      e?.message || String(e)
    );
  }

  console.log(
    "Translation: all providers failed"
  );

  return (
    "⚠️ 中文翻譯暫時失敗，" +
    "請查看下方原文。"
  );
}


function validTranslation(value) {
  if (!value) {
    return false;
  }

  const lower =
    String(value)
      .toLowerCase();

  return ![
    "error 500",
    "server error",
    "that's an error",
    "bad gateway",
    "service unavailable",
    "<html",
    "<!doctype",
  ].some((x) =>
    lower.includes(x)
  );
}


async function sendTweet(
  env,
  tweet
) {
  console.log(
    "Discord: preparing",
    tweet.id
  );

  const translated =
    await translateToZhTW(
      tweet.text
    );

  const embed = {
    title:
      "燕雲十六聲｜官方 X 更新",

    url:
      tweet.url,

    description:
      translated.slice(
        0,
        3500
      ),

    fields: [
      {
        name: "原文",
        value:
          tweet.text.slice(
            0,
            900
          ),
        inline: false,
      },
    ],

    footer: {
      text:
        "來源：Where Winds Meet 官方 X · Cloudflare Browser Run",
    },
  };

  if (tweet.datetime) {
    embed.timestamp =
      tweet.datetime;
  }

  if (tweet.image) {
    embed.image = {
      url:
        tweet.image,
    };
  }

  const response =
    await fetch(
      env.DISCORD_WEBHOOK_URL,
      {
        method: "POST",

        headers: {
          "Content-Type":
            "application/json",
        },

        body:
          JSON.stringify({
            username:
              "燕雲官方情報",

            embeds:
              [embed],

            allowed_mentions: {
              parse: [],
            },
          }),
      }
    );

  console.log(
    "Discord HTTP:",
    response.status
  );

  if (!response.ok) {
    const body =
      await response.text();

    throw new Error(
      `Discord HTTP ${response.status}: ${body}`
    );
  }

  console.log(
    "Discord: sent",
    tweet.id
  );
}
