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
        api_patch = patch.object(main, "YOUTUBE_API_KEY", "test-key")
        api_patch.start()
        self.addCleanup(api_patch.stop)

    def run_youtube(self, videos, channel=CHANNEL, failure=None):
        with patch.object(main.config, "YOUTUBE_CHANNEL_ID", channel), \
             patch.object(main.config, "SEND_LATEST_ON_FIRST_RUN", True), \
             patch.object(main, "DISCORD_WEBHOOK_URL", "https://example.test/webhook"), \
             patch.object(main, "DEEPL_API_KEY", "test-key"), \
             patch.object(main, "translate_with_deepl", return_value="影片譯文"), \
             patch.object(main, "fetch_youtube_videos", return_value=videos), \
             patch.object(main, "send_discord_message", side_effect=failure) as send:
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

    def test_youtube_handle_formats_use_api(self):
        for value in ('WhereWindsMeet', '@WhereWindsMeet', 'https://www.youtube.com/@WhereWindsMeet', 'youtube.com/@WhereWindsMeet/'):
            with self.subTest(value=value), patch.object(main.SESSION, 'get', return_value=Mock(ok=True, json=Mock(return_value={'items': [{'id': CHANNEL}]}))) as get:
                self.assertEqual(main.normalize_youtube_channel(value), CHANNEL)
                self.assertEqual(get.call_args.args[0], 'https://www.googleapis.com/youtube/v3/channels')
                self.assertEqual(get.call_args.kwargs['params']['forHandle'], 'WhereWindsMeet')

    def test_youtube_id_needs_no_resolution_request(self):
        with patch.object(main.SESSION, 'get') as get:
            self.assertEqual(main.normalize_youtube_channel(CHANNEL), CHANNEL)
            get.assert_not_called()

    def test_handle_resolution_rejects_missing_channel(self):
        with patch.object(main.SESSION, 'get', return_value=Mock(ok=True, json=Mock(return_value={'items': []}))):
            with self.assertRaises(RuntimeError):
                main.normalize_youtube_channel('WhereWindsMeet')

    def test_handle_and_id_share_seen_history(self):
        self.run_youtube([video(1)])
        with patch.object(main.SESSION, 'get', return_value=Mock(ok=True, json=Mock(return_value={'items': [{'id': CHANNEL}]}))):
            self.assertEqual(self.run_youtube([video(1)], '@WhereWindsMeet').call_count, 0)

    def test_missing_api_key_fails_before_network(self):
        with patch.object(main, 'YOUTUBE_API_KEY', ''), patch.object(main.config, 'YOUTUBE_CHANNEL_ID', CHANNEL), patch.object(main.SESSION, 'get') as get:
            with self.assertRaisesRegex(RuntimeError, 'YOUTUBE_API_KEY'):
                main.run_youtube()
            get.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'YOUTUBE_API_KEY'):
                main.youtube_api_request('channels', {})
            get.assert_not_called()

    def test_api_mode_resolves_handle_without_html(self):
        with patch.object(main, 'YOUTUBE_API_KEY', 'test-key'), patch.object(main.SESSION, 'get', return_value=Mock(ok=True, json=Mock(return_value={'items': [{'id': CHANNEL}]}))) as get:
            self.assertEqual(main.normalize_youtube_channel('WhereWindsMeet'), CHANNEL)
            self.assertEqual(get.call_args.kwargs['params']['forHandle'], 'WhereWindsMeet')
            self.assertIn('googleapis.com/youtube/v3/channels', get.call_args.args[0])

    def test_api_mode_fetches_uploads_filters_and_sorts(self):
        items = [{'snippet': {'title': f'Video {n}', 'videoOwnerChannelId': CHANNEL, 'videoOwnerChannelTitle': 'Official'},
                  'contentDetails': {'videoId': f'{n:011d}', 'videoPublishedAt': f'2026-09-0{n}T00:00:00Z'},
                  'status': {'privacyStatus': 'public' if n != 3 else 'private'}} for n in (2, 1, 3)]
        responses = [Mock(ok=True, json=Mock(return_value={'items': [{'id': CHANNEL, 'contentDetails': {'relatedPlaylists': {'uploads': 'uploads-list'}}}]})),
                     Mock(ok=True, json=Mock(return_value={'items': items}))]
        with patch.object(main, 'YOUTUBE_API_KEY', 'test-key'), patch.object(main.SESSION, 'get', side_effect=responses) as get:
            videos = main.fetch_youtube_videos(CHANNEL)
            self.assertEqual([v['id'] for v in videos], [video(1)['id'], video(2)['id']])
            self.assertEqual(get.call_args.kwargs['params']['playlistId'], 'uploads-list')
            self.assertEqual(get.call_count, 2)
            self.assertTrue(all('googleapis.com' in call.args[0] for call in get.call_args_list))

    def test_api_error_does_not_leak_key(self):
        with patch.object(main, 'YOUTUBE_API_KEY', 'private-key'), patch.object(main.SESSION, 'get', side_effect=main.requests.ConnectionError('https://example.test/?key=private-key')):
            with self.assertRaises(RuntimeError) as caught:
                main.youtube_api_request('channels', {})
            self.assertNotIn('private-key', str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_first_run_then_new_videos_and_no_duplicates(self):
        send = self.run_youtube([video(1), video(2)])
        self.assertEqual(send.call_count, 1)
        self.assertTrue(send.call_args.kwargs['content'].endswith(video(2)['id']))
        self.assertEqual(self.run_youtube([video(1), video(2)]).call_count, 0)
        self.assertEqual(self.run_youtube([video(1), video(2), video(3)]).call_count, 1)

    def test_channel_switch_keeps_records(self):
        self.run_youtube([video(1)])
        self.run_youtube([video(2)], OTHER_CHANNEL)
        self.assertEqual(self.run_youtube([video(1)]).call_count, 0)
        self.assertEqual(len(main.load_state()['youtube_channels']), 2)

    def test_youtube_native_player_uses_bare_url_and_only_translation(self):
        send = self.run_youtube([video(1)])
        content = send.call_args.kwargs['content']
        self.assertIn('影片譯文', content)
        self.assertNotIn('Video 1', content)
        self.assertNotIn('embeds', send.call_args.kwargs)
        self.assertEqual(content.splitlines()[-1], 'https://www.youtube.com/watch?v=00000000001')

    def test_youtube_translation_failure_leaves_video_pending(self):
        with patch.object(main.config, 'YOUTUBE_CHANNEL_ID', CHANNEL), \
             patch.object(main, 'DISCORD_WEBHOOK_URL', 'https://example.test/webhook'), \
             patch.object(main, 'DEEPL_API_KEY', 'test-key'), \
             patch.object(main.config, 'SEND_LATEST_ON_FIRST_RUN', True), \
             patch.object(main, 'fetch_youtube_videos', return_value=[video(1)]), \
             patch.object(main, 'translate_with_deepl', side_effect=RuntimeError('unavailable')), \
             patch.object(main, 'send_discord_message') as send:
            with self.assertRaises(RuntimeError):
                main.run_youtube()
            send.assert_not_called()
            self.assertNotIn(CHANNEL, main.load_state().get('youtube_channels', {}))

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
        with patch.object(main, 'run_x', side_effect=RuntimeError('X unavailable')), patch.object(main, 'run_youtube') as youtube, \
             patch.object(main, 'run_official_news') as news:
            with self.assertRaises(RuntimeError):
                main.main()
            youtube.assert_called_once()
            news.assert_called_once()

    def test_two_discord_channels_receive_identical_payload(self):
        with patch.object(main, 'DISCORD_WEBHOOK_URL', 'https://example.test/one'), \
             patch.object(main, 'DISCORD_WEBHOOK_URL_2', 'https://example.test/two'), \
             patch.object(main.SESSION, 'post', return_value=Mock(ok=True, status_code=204)) as post:
            main.send_discord_embed({'title': 'YouTube update'})
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args_list[0].kwargs['json'], post.call_args_list[1].kwargs['json'])

    def test_unconfigured_youtube_skips_requests(self):
        with patch.object(main, 'YOUTUBE_API_KEY', ''), patch.object(main.config, 'YOUTUBE_CHANNEL_ID', ''), patch.object(main.SESSION, 'get') as get:
            main.run_youtube()
            get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
