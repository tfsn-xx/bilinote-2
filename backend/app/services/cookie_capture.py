"""Browser login and platform-scoped Cookie capture inside the Docker container."""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from websockets.sync.client import connect

PLATFORMS = {
    "bilibili": ("https://www.bilibili.com/", "bilibili.com", ("SESSDATA",)),
    "youtube": ("https://www.youtube.com/", "youtube.com", ("SAPISID", "__Secure-3PAPISID", "LOGIN_INFO")),
    "douyin": ("https://www.douyin.com/", "douyin.com", ("sessionid", "sessionid_ss")),
    "kuaishou": ("https://www.kuaishou.com/", "kuaishou.com", ("kuaishou.server.web_st",)),
}


def platform_cookies(cookies, platform, now=None):
    now = time.time() if now is None else now
    _, allowed_domain, auth_names = PLATFORMS[platform]
    selected = []
    for cookie in cookies:
        domain = cookie["domain"].lstrip(".").lower()
        expires = cookie.get("expires", -1)
        if (domain == allowed_domain or domain.endswith("." + allowed_domain)) and (
            expires <= 0 or expires > now
        ) and cookie["value"]:
            selected.append(cookie)
    selected.sort(key=lambda cookie: (len(cookie["domain"]), len(cookie.get("path", "/"))))
    values = {}
    for cookie in selected:
        values.setdefault(cookie["name"], cookie["value"])
    if not any(name in values for name in auth_names):
        return None
    return "; ".join(f"{name}={value}" for name, value in values.items())


class CookieCapture:
    def __init__(self, profile=None):
        self.profile = Path(profile or Path(os.getenv("DATA_DIR", "data")) / "cookie-browser")
        self.process = None
        self.sessions = {}
        self.targets = {}
        self.lock = threading.Lock()
        self.http = build_opener(ProxyHandler({}))

    def _endpoint(self):
        try:
            port, path = (self.profile / "DevToolsActivePort").read_text().strip().splitlines()
            with self.http.open(f"http://127.0.0.1:{int(port)}/json/version", timeout=1) as response:
                info = json.load(response)
            endpoint = info["webSocketDebuggerUrl"]
            return endpoint if endpoint.endswith(path) else None
        except (OSError, ValueError, KeyError):
            return None

    def _ensure_browser(self):
        endpoint = self._endpoint()
        if endpoint:
            return endpoint
        executable = shutil.which("chromium")
        if not executable:
            raise RuntimeError("当前镜像未安装登录浏览器，请更新为包含 Cookie 登录服务的镜像。")
        self.profile.mkdir(parents=True, exist_ok=True)
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        environment = {**os.environ, "DISPLAY": ":99"}
        # The API clients may use a global proxy; localhost CDP never uses it.
        command = [
            executable, f"--user-data-dir={self.profile.resolve()}",
            "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run",
            "--no-default-browser-check", "--password-store=basic",
            "--disable-background-mode", "--start-maximized", "about:blank",
        ]
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if proxy:
            command.insert(-1, f"--proxy-server={proxy}")
        self.process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(75):
            endpoint = self._endpoint()
            if endpoint:
                return endpoint
            if self.process.poll() is not None:
                raise RuntimeError("登录浏览器启动失败，请查看容器 cookie-display 日志。")
            time.sleep(0.2)
        raise RuntimeError("登录浏览器启动超时，请重试。")

    def command(self, method, params=None, endpoint=None):
        endpoint = endpoint or self._endpoint()
        if not endpoint:
            raise RuntimeError("登录浏览器已关闭，请重新获取。")
        with connect(endpoint, proxy=None, open_timeout=5, close_timeout=1, max_size=16 * 1024 * 1024) as socket:
            socket.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                result = json.loads(socket.recv(timeout=5))
                if result.get("id") != 1:
                    continue
                if "error" in result:
                    raise RuntimeError("登录浏览器操作失败，请重新获取。")
                return result.get("result", {})

    def start(self, platform):
        if platform not in PLATFORMS:
            raise ValueError("不支持的平台。")
        with self.lock:
            self._ensure_browser()
            now = time.time()
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now}
            targets = self.command("Target.getTargets")["targetInfos"]
            target = self.targets.get(platform)
            if not any(item["targetId"] == target for item in targets):
                target = self.command("Target.createTarget", {"url": PLATFORMS[platform][0]})["targetId"]
                self.targets[platform] = target
            self.command("Target.activateTarget", {"targetId": target})
            session_id = str(uuid.uuid4())
            self.sessions[session_id] = {"platform": platform, "expires": now + 300}
            return {"id": session_id, "platform": platform, "state": "waiting"}

    def poll(self, session_id):
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session["expires"] < time.time():
                self.sessions.pop(session_id, None)
                raise ValueError("获取已超时或结束，请重新获取。")
            platform = session["platform"]
            cookies = self.command("Storage.getCookies")["cookies"]
            cookie = platform_cookies(cookies, platform)
            if not cookie:
                return {"platform": platform, "state": "waiting"}
            del self.sessions[session_id]
            return {"platform": platform, "state": "ready", "cookie": cookie}

    def cancel(self, session_id):
        with self.lock:
            self.sessions.pop(session_id, None)
        return {"state": "cancelled"}

    def close(self):
        if not self._endpoint():
            return
        try:
            self.command("Browser.close")
            if self.process:
                self.process.wait(timeout=5)
        except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            if self.process and self.process.poll() is None:
                self.process.terminate()
