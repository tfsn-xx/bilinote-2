import importlib.util
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "cookie_capture_service", Path(__file__).parents[1] / "app/services/cookie_capture.py"
)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def cookie(name, value, domain, expires=-1):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": expires}


class CookieCaptureTests(unittest.TestCase):
    def test_only_current_platform_and_live_cookies_are_exported(self):
        cookies = [
            {**cookie("SESSDATA", "session", ".bilibili.com"), "httpOnly": True},
            cookie("SESSDATA", "nested", "www.bilibili.com"),
            cookie("expired", "old", ".bilibili.com", 1),
            cookie("SID", "other-platform", ".youtube.com"),
            cookie("SESSDATA", "unrelated", ".fakebilibili.com"),
            cookie("bili_jct", "csrf", ".bilibili.com"),
        ]
        self.assertEqual(service.platform_cookies(cookies, "bilibili"), "SESSDATA=session; bili_jct=csrf")

    def test_guest_cookies_do_not_finish_login(self):
        self.assertIsNone(service.platform_cookies([cookie("buvid3", "guest", ".bilibili.com")], "bilibili"))
        self.assertIsNone(service.platform_cookies([cookie("userId", "guest", ".kuaishou.com")], "kuaishou"))

    def test_all_platform_login_markers(self):
        for platform, (_, domain, auth) in service.PLATFORMS.items():
            with self.subTest(platform=platform):
                self.assertEqual(service.platform_cookies([cookie(auth[0], "value", "." + domain)], platform), f"{auth[0]}=value")

    def test_sessions_wait_finish_cancel_and_expire(self):
        capture = service.CookieCapture()
        cookies = []
        capture.command = lambda *args, **kwargs: {"cookies": cookies}
        capture.sessions["waiting"] = {"platform": "bilibili", "expires": time.time() + 60}
        self.assertEqual(capture.poll("waiting")["state"], "waiting")
        cookies.append(cookie("SESSDATA", "value", ".bilibili.com"))
        self.assertEqual(capture.poll("waiting")["cookie"], "SESSDATA=value")
        with self.assertRaises(ValueError):
            capture.poll("waiting")
        capture.sessions["cancel"] = {"platform": "youtube", "expires": time.time() + 60}
        self.assertEqual(capture.cancel("cancel")["state"], "cancelled")
        self.assertNotIn("cancel", capture.sessions)
        capture.sessions["expired"] = {"platform": "bilibili", "expires": 1}
        with self.assertRaises(ValueError):
            capture.poll("expired")
        self.assertNotIn("expired", capture.sessions)

    def test_unknown_platform_does_not_launch_browser(self):
        capture = service.CookieCapture()
        with self.assertRaises(ValueError):
            capture.start("unknown")


if __name__ == "__main__":
    unittest.main()
