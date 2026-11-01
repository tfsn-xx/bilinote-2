"""Run inside the Docker image with COOKIE_BROWSER_TEST=true."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "cookie_capture_service", Path(__file__).parents[1] / "app/services/cookie_capture.py"
)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@unittest.skipUnless(os.getenv("COOKIE_BROWSER_TEST") == "true", "requires the Docker login display")
class CookieBrowserTests(unittest.TestCase):
    def test_real_browser_capture_and_persistent_login(self):
        with tempfile.TemporaryDirectory(prefix="bilinote-cookie-test-") as profile:
            capture = service.CookieCapture(profile)
            try:
                for platform, (_, domain, auth_names) in service.PLATFORMS.items():
                    with self.subTest(platform=platform):
                        session = capture.start(platform)
                        self.assertEqual(capture.poll(session["id"])["state"], "waiting")
                        capture.command("Storage.setCookies", {"cookies": [{
                            "name": auth_names[0], "value": "test-" + platform,
                            "domain": "." + domain, "path": "/", "httpOnly": True,
                            "secure": True, "expires": 2000000000,
                        }]})
                        result = capture.poll(session["id"])
                        self.assertEqual(result["state"], "ready")
                        self.assertIn(f"{auth_names[0]}=test-{platform}", result["cookie"])
                        for other in service.PLATFORMS:
                            if other != platform:
                                self.assertNotIn("test-" + other, result["cookie"])
                capture.close()
                capture = service.CookieCapture(profile)
                session = capture.start("bilibili")
                self.assertIn("SESSDATA=test-bilibili", capture.poll(session["id"])["cookie"])
            finally:
                capture.close()


if __name__ == "__main__":
    unittest.main()
