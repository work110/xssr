import unittest
from unittest.mock import Mock, patch

import main


class DiscordTests(unittest.TestCase):
    def tweet(self, count=4):
        media = [{"id_str": str(i), "media_url_https": f"https://pbs.twimg.com/media/{i}.jpg"}
                 for i in range(count)]
        return {"id": "123", "text": "Original text", "url": "https://x.com/example/status/123",
                "raw": {"extended_entities": {"media": media}, "entities": {"media": media[:1]}}}

    def test_all_images_in_order_without_duplicates(self):
        tweet = self.tweet()
        tweet["raw"]["media"] = [{"media_url": "http://pbs.twimg.com/media/0.jpg"}]
        self.assertEqual(main.extract_images(tweet),
                         [f"https://pbs.twimg.com/media/{i}.jpg" for i in range(4)])

    def test_media_variants_and_invalid_links(self):
        for key in ("media", "medias", "mediaList", "extendedEntities"):
            media = [None, {}, {"url": "https://t.co/abc"},
                     {"url": "https://t.co/def", "preview_image_url": "https://example.test/preview.jpg"}]
            raw = {key: {"media": media} if key == "extendedEntities" else media}
            self.assertEqual(main.extract_images({"raw": raw}), ["https://example.test/preview.jpg"])

    def test_x_payload_has_every_image_translation_and_source_link(self):
        for count in (0, 1, 4, 11):
            with self.subTest(count=count), \
                 patch.object(main, "translate_with_deepl", return_value="繁體中文譯文"), \
                 patch.object(main, "DISCORD_WEBHOOK_URL", "https://example.test/one"), \
                 patch.object(main, "DISCORD_WEBHOOK_URL_2", "https://example.test/two"), \
                 patch.object(main.SESSION, "post", return_value=Mock(ok=True, status_code=200)) as post:
                main.send_to_discord(self.tweet(count))
                payloads = [call.kwargs["json"] for call in post.call_args_list[::2]]
                urls = [embed["image"]["url"] for payload in payloads
                        for embed in payload["embeds"] if "image" in embed]
                self.assertEqual(urls, [f"https://pbs.twimg.com/media/{i}.jpg" for i in range(count)])
                self.assertEqual(payloads[0]["embeds"][0]["description"], "繁體中文譯文")
                self.assertIn(self.tweet()["url"], payloads[0]["embeds"][0]["fields"][0]["value"])
                self.assertNotIn("Original text", str(payloads))
                for one, two in zip(post.call_args_list[::2], post.call_args_list[1::2]):
                    self.assertEqual(one.kwargs["json"], two.kwargs["json"])
                    self.assertEqual(one.kwargs["params"], {"wait": "true"})
                    self.assertEqual(one.kwargs["json"]["allowed_mentions"], {"parse": []})

    def test_translation_failure_does_not_send_original(self):
        with patch.object(main, "translate_with_deepl", side_effect=RuntimeError("unavailable")), \
             patch.object(main, "send_discord_message") as send:
            with self.assertRaises(RuntimeError):
                main.send_to_discord(self.tweet())
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
