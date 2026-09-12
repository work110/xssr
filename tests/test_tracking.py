import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import main


CHANNEL = "UC" + "a" * 22
OTHER_CHANNEL = "UC" + "b" * 22


def video(number):
    return {"id": f"{number:011d}", "title": f"Video {number}",
            "author": "Official", "published": datetime(2026, 9, number, tzinfo=timezone.utc)}


class TrackingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_patch = patch.object(main, "STATE_FILE", Path(temporary.name) / "state.json")
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def run_youtube(self, videos, channel=CHANNEL, failure=None):
        with patch.object(main.config, "YOUTUBE_CHANNEL_ID", channel), \
             patch.object(main.config, "SEND_LATEST_ON_FIRST_RUN", True), \
             patch.object(main, "DISCORD_WEBHOOK_URL", "https://example.test/webhook"), \
             patch.object(main, "DEEPL_API_KEY", ""), \
             patch.object(main, "fetch_youtube_videos", return_value=videos), \
             patch.object(main, "send_discord_embed", side_effect=failure) as send:
            main.run_youtube()
            return send

    def test_settings(self):
        for value in ("WhereWindsMeet_", "@WhereWindsMeet_", "https://x.com/WhereWindsMeet_", "twitter.com/WhereWindsMeet_/"):
            self.assertEqual(main.normalize_x_username(value), "WhereWindsMeet_")
        for value in (CHANNEL, f"https://www.youtube.com/channel/{CHANNEL}"):
            self.assertEqual(main.normalize_youtube_channel(value), CHANNEL)
        for value in ("@", "https://example.com/channel/" + CHANNEL):
            with self.assertRaises(ValueError):
                main.normalize_youtube_channel(value)
        with self.assertRaises(ValueError):
            main.normalize_x_username("https://x.com/example/status/123")

    def test_youtube_handle_formats_resolve_page_identity(self):
        html = (f'<script>{{"channelId":"{OTHER_CHANNEL}"}}</script>'
                f'<link href="https://www.youtube.com/channel/{CHANNEL}" rel="canonical">'
                f'<meta content="{CHANNEL}" itemprop="identifier">')
        for value in ('WhereWindsMeet', '@WhereWindsMeet', 'https://www.youtube.com/@WhereWindsMeet', 'youtube.com/@WhereWindsMeet/'):
            with self.subTest(value=value), patch.object(main.SESSION, 'get', return_value=Mock(ok=True, text=html)) as get:
                self.assertEqual(main.normalize_youtube_channel(value), CHANNEL)
                self.assertEqual(get.call_args.args[0], 'https://www.youtube.com/@WhereWindsMeet')

    def test_youtube_id_does_not_request_channel_page(self):
        with patch.object(main.SESSION, 'get') as get:
            self.assertEqual(main.normalize_youtube_channel(CHANNEL), CHANNEL)
            get.assert_not_called()

    def test_handle_resolution_fails_without_reliable_identity(self):
        for response in (Mock(ok=False, status_code=404),
                         Mock(ok=True, text=f'<script>{{"channelId":"{CHANNEL}"}}</script>'),
                         Mock(ok=True, text=f'<meta itemprop="identifier" content="{CHANNEL}"><link rel="canonical" href="https://www.youtube.com/channel/{OTHER_CHANNEL}">')):
            with patch.object(main.SESSION, 'get', return_value=response):
                with self.assertRaises(RuntimeError):
                    main.normalize_youtube_channel('WhereWindsMeet')

    def test_handle_and_id_share_seen_history(self):
        self.run_youtube([video(1)])
        with patch.object(main.SESSION, 'get', return_value=Mock(ok=True, text=f'<meta itemprop="identifier" content="{CHANNEL}">')):
            self.assertEqual(self.run_youtube([video(1)], '@WhereWindsMeet').call_count, 0)

    def test_parse_feed_and_sort(self):
        entries = ''.join(f'<entry><yt:videoId>{n:011d}</yt:videoId><title>Video &amp; {n}</title><published>2026-09-0{n}T00:00:00Z</published><author><name>Official</name></author></entry>' for n in (2, 1))
        xml = f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>{CHANNEL}</yt:channelId>{entries}</feed>'
        with patch.object(main.SESSION, "get", return_value=Mock(ok=True, content=xml.encode())):
            videos = main.fetch_youtube_videos(CHANNEL)
            self.assertEqual([v['id'] for v in videos], [video(1)['id'], video(2)['id']])
            self.assertEqual(videos[0]['title'], 'Video & 1')
            with self.assertRaises(RuntimeError):
                main.fetch_youtube_videos(OTHER_CHANNEL)

    def test_first_run_then_new_videos_and_no_duplicates(self):
        send = self.run_youtube([video(1), video(2)])
        self.assertEqual(send.call_count, 1)
        self.assertTrue(send.call_args.args[0]['url'].endswith(video(2)['id']))
        self.assertEqual(self.run_youtube([video(1), video(2)]).call_count, 0)
        self.assertEqual(self.run_youtube([video(1), video(2), video(3)]).call_count, 1)

    def test_channel_switch_keeps_records(self):
        self.run_youtube([video(1)])
        self.run_youtube([video(2)], OTHER_CHANNEL)
        self.assertEqual(self.run_youtube([video(1)]).call_count, 0)
        self.assertEqual(len(main.load_state()['youtube_channels']), 2)

    def test_failure_does_not_mark_video_seen(self):
        self.run_youtube([video(1)])
        with self.assertRaises(RuntimeError):
            self.run_youtube([video(1), video(2)], failure=RuntimeError('failed'))
        self.assertEqual(main.load_state()['youtube_channels'][CHANNEL], [video(1)['id']])
        self.assertEqual(self.run_youtube([video(1), video(2)]).call_count, 1)

    def test_preserve_legacy_x_and_youtube_state(self):
        main.STATE_FILE.write_text(json.dumps({'user_id': 'old', 'seen': ['100']}))
        self.run_youtube([video(1)])
        main.save_state(['200'], 'new')
        state = main.load_state()
        self.assertEqual(state['x_accounts'], {'old': ['100'], 'new': ['200']})
        self.assertEqual(state['youtube_channels'][CHANNEL], [video(1)['id']])

    def test_x_legacy_and_switch_back_deduplication(self):
        main.save_state(['101'], 'old')
        main.save_state(['201'], 'new')
        tweet = {'id': '101', 'text': 'Already sent', 'url': 'https://x.com/example/status/101'}
        with patch.object(main, 'validate_secrets'), patch.object(main, 'resolve_user_id', return_value='old'), \
             patch.object(main, 'fetch_raw_tweets', return_value=([tweet], 'test')), \
             patch.object(main, 'normalize_tweets', return_value=[tweet]), patch.object(main, 'send_to_discord') as send:
            main.run_x()
            send.assert_not_called()

    def test_sources_run_independently(self):
        with patch.object(main, 'run_x', side_effect=RuntimeError('X unavailable')), patch.object(main, 'run_youtube') as youtube:
            with self.assertRaises(RuntimeError):
                main.main()
            youtube.assert_called_once()

    def test_two_discord_channels_receive_identical_payload(self):
        with patch.object(main, 'DISCORD_WEBHOOK_URL', 'https://example.test/one'), \
             patch.object(main, 'DISCORD_WEBHOOK_URL_2', 'https://example.test/two'), \
             patch.object(main.SESSION, 'post', return_value=Mock(ok=True, status_code=204)) as post:
            main.send_discord_embed({'title': 'YouTube update'})
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args_list[0].kwargs['json'], post.call_args_list[1].kwargs['json'])

    def test_unconfigured_youtube_skips_requests(self):
        with patch.object(main.config, 'YOUTUBE_CHANNEL_ID', ''), patch.object(main.SESSION, 'get') as get:
            main.run_youtube()
            get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
