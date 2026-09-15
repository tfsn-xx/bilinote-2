import unittest
from unittest.mock import Mock, patch

from app.downloaders.bilibili_downloader import BilibiliDownloader
from app.downloaders.bilibili_subtitle import BilibiliSubtitleFetcher


VIDEO_URL = "https://www.bilibili.com/video/BV1MJVb6cETR"


class TestBilibiliSubtitleAuthFallback(unittest.TestCase):
    def test_empty_subtitles_with_logged_out_cookie_is_auth_failure(self):
        fetcher = BilibiliSubtitleFetcher.__new__(BilibiliSubtitleFetcher)
        fetcher.last_failure_reason = None

        with (
            patch.object(fetcher, "_get_cid", return_value=38691474571),
            patch.object(fetcher, "_list_subtitles", return_value=[]),
            patch.object(fetcher, "_check_login_state", return_value=False),
        ):
            result = fetcher.fetch_subtitles(VIDEO_URL)

        self.assertIsNone(result)
        self.assertEqual(fetcher.last_failure_reason, "auth_required")

    def test_empty_subtitles_with_valid_login_is_real_no_subtitle_result(self):
        fetcher = BilibiliSubtitleFetcher.__new__(BilibiliSubtitleFetcher)
        fetcher.last_failure_reason = None

        with (
            patch.object(fetcher, "_get_cid", return_value=38691474571),
            patch.object(fetcher, "_list_subtitles", return_value=[]),
            patch.object(fetcher, "_check_login_state", return_value=True),
        ):
            result = fetcher.fetch_subtitles(VIDEO_URL)

        self.assertIsNone(result)
        self.assertEqual(fetcher.last_failure_reason, "no_subtitles")

    def test_auth_failure_skips_ytdlp_and_returns_for_whisper_fallback(self):
        downloader = BilibiliDownloader.__new__(BilibiliDownloader)
        downloader.subtitle_failure_reason = None
        downloader._subtitle_auth_failed = False

        fetcher = Mock()
        fetcher.fetch_subtitles.return_value = None
        fetcher.last_failure_reason = "auth_required"

        with (
            patch(
                "app.downloaders.bilibili_downloader.BilibiliSubtitleFetcher",
                return_value=fetcher,
            ),
            patch("app.downloaders.bilibili_downloader.yt_dlp.YoutubeDL") as youtube_dl,
        ):
            result = downloader.download_subtitles(VIDEO_URL)

        self.assertIsNone(result)
        self.assertTrue(downloader._subtitle_auth_failed)
        self.assertEqual(downloader.subtitle_failure_reason, "auth_required")
        youtube_dl.assert_not_called()

    def test_repeated_auth_failure_does_not_retry_platform(self):
        downloader = BilibiliDownloader.__new__(BilibiliDownloader)
        downloader.subtitle_failure_reason = None
        downloader._subtitle_auth_failed = True

        with patch("app.downloaders.bilibili_downloader.BilibiliSubtitleFetcher") as fetcher_factory:
            result = downloader.download_subtitles(VIDEO_URL)

        self.assertIsNone(result)
        self.assertEqual(downloader.subtitle_failure_reason, "auth_required")
        fetcher_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
