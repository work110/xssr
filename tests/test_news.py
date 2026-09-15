import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from news import NEWS_URL, parse_news_page


def page(number=1, next_page=""):
    return f'''<ul class="news_list"><li>
      <a class="news" href="official/{number}.html" title="公告">
        <img src="https://example.test/news.png">
        <p class="news_tit">中文<b>新聞</b> &amp; 公告 {number}</p>
        <p class="news_text">第一段<br>第二段</p>
        <p class="date_day">09.{number:02d}</p><p class="date_year">2026</p>
      </a></li></ul>{next_page}'''


def article(number):
    return parse_news_page(page(number))[0][0]


class NewsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for target, name, value in (
            (main, "STATE_FILE", Path(temporary.name) / "state.json"),
            (main, "DISCORD_WEBHOOK_URL", "https://example.test/webhook"),
            (main.config, "OFFICIAL_NEWS_ENABLED", True),
            (main.config, "SEND_LATEST_ON_FIRST_RUN", True),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_news(self, articles, failure=None):
        with patch.object(main, "fetch_official_news", return_value=articles), \
             patch.object(main, "send_discord_message", side_effect=failure) as send, \
             patch.object(main, "translate_with_deepl") as translate:
            main.run_official_news()
            translate.assert_not_called()
            return send

    def test_parser_text_links_date_and_pagination(self):
        articles, next_page = parse_news_page(page(1, '<a class="next next-btn" href="index_2.html">下一頁</a>'))
        self.assertEqual(articles[0]["title"], "中文新聞 & 公告 1")
        self.assertEqual(articles[0]["summary"], "第一段\n第二段")
        self.assertEqual(articles[0]["date"], "2026-09-01")
        self.assertEqual(articles[0]["url"], NEWS_URL + "official/1.html")
        self.assertEqual(next_page, NEWS_URL + "index_2.html")

    def test_fetch_all_pages_deduplicates_and_sorts(self):
        responses = [Mock(ok=True, content=page(2, '<a class="next-btn" href="index_2.html"></a>').encode()),
                     Mock(ok=True, content=(page(1) + page(2)).encode())]
        with patch.object(main.SESSION, "get", side_effect=responses) as get:
            self.assertEqual([a["date"] for a in main.fetch_official_news()], ["2026-09-02", "2026-09-01"])
            self.assertEqual(get.call_args.args[0], NEWS_URL + "index_2.html")

    def test_official_activity_link_preserves_language(self):
        url = 'https://www.wherewindsmeetgame.com/jm/activity/example/event?lang=zh-TW'
        articles, _ = parse_news_page(page().replace('official/1.html', url))
        self.assertEqual(articles[0]['url'], url)

    def test_reject_changed_page_bad_date_and_external_link(self):
        for html in ("<html>maintenance</html>", page().replace("09.01", "invalid"),
                     page().replace("official/1.html", "https://example.test/news.html")):
            with self.subTest(html=html), self.assertRaises(RuntimeError):
                parse_news_page(html)

    def test_pagination_loop_fails(self):
        response = Mock(ok=True, content=page(1, f'<a class="next-btn" href="{NEWS_URL}"></a>').encode())
        with patch.object(main.SESSION, "get", return_value=response), self.assertRaises(RuntimeError):
            main.fetch_official_news()

    def test_first_run_latest_then_only_new_with_batch_limit(self):
        send = self.run_news([article(2), article(1)])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs["embeds"][0]["url"], article(2)["url"])
        self.assertEqual(self.run_news([article(2), article(1)]).call_count, 0)
        with patch.object(main.config, "MAX_POSTS_PER_RUN", 1):
            send = self.run_news([article(4), article(3), article(2), article(1)])
            self.assertEqual(send.call_args.kwargs["embeds"][0]["url"], article(3)["url"])
            send = self.run_news([article(4), article(3), article(2), article(1)])
            self.assertEqual(send.call_args.kwargs["embeds"][0]["url"], article(4)["url"])

    def test_first_run_silent_and_preserves_other_sources(self):
        main.write_state({"seen": ["x1"], "youtube_channels": {"channel": ["video"]}})
        with patch.object(main.config, "SEND_LATEST_ON_FIRST_RUN", False):
            self.assertEqual(self.run_news([article(1)]).call_count, 0)
        state = main.load_state()
        self.assertEqual(state["seen"], ["x1"])
        self.assertEqual(state["youtube_channels"], {"channel": ["video"]})
        self.assertEqual(state["official_news"][NEWS_URL], [article(1)["url"]])

    def test_failed_send_retries_and_keeps_successful_progress(self):
        self.run_news([article(1)])
        with self.assertRaises(RuntimeError):
            self.run_news([article(3), article(2), article(1)], failure=[None, RuntimeError("failed")])
        self.assertEqual(main.load_state()["official_news"][NEWS_URL], [article(1)["url"], article(2)["url"]])
        self.assertEqual(self.run_news([article(3), article(2), article(1)]).call_count, 1)

    def test_fetch_failure_does_not_initialize_state(self):
        with patch.object(main.SESSION, "get", return_value=Mock(ok=False, status_code=503)), \
             self.assertRaises(RuntimeError):
            main.run_official_news()
        self.assertNotIn("official_news", main.load_state())

    def test_disabled_skips_network(self):
        with patch.object(main.config, "OFFICIAL_NEWS_ENABLED", False), patch.object(main.SESSION, "get") as get:
            main.run_official_news()
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
