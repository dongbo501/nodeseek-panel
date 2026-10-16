"""按需启动可视化 Chromium，在用户手工登录后导入 NodeSeek Cookie。"""
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import websocket

log = logging.getLogger("nodeseek")


def extract_cookie(cookies):
    selected = [c for c in cookies if c.get("domain", "").lstrip(".") in ("nodeseek.com", "www.nodeseek.com")]
    # session 是 NodeSeek 登录会话；cf_clearance 等验证 Cookie 一并保留。
    if not any(c.get("name") == "session" and c.get("value") for c in selected):
        return None
    selected.sort(key=lambda c: len(c.get("path", "/")), reverse=True)
    values = {}
    for cookie in selected:
        values.setdefault(cookie["name"], cookie["value"])
    return "; ".join(f"{k}={v}" for k, v in values.items())


class BrowserManager:
    def __init__(self, config, service):
        self.config, self.service = config, service
        self.lock = threading.RLock()
        self.processes = []
        self.profile = None
        self.deadline = 0
        self.generation = 0
        self.message = "浏览器尚未启动"
        self.imported_at = None
        self.ready = False

    def status(self):
        with self.lock:
            running = bool(self.processes) and all(p.poll() is None for p in self.processes)
            return {"running": running, "ready": self.ready and running, "message": self.message,
                    "imported_at": self.imported_at, "remaining_seconds": max(0, int(self.deadline - time.monotonic())) if running else 0}

    def start(self):
        with self.lock:
            if self.status()["running"]:
                return self.status()
            for binary in ("Xvfb", "chromium", "x11vnc"):
                if not shutil.which(binary):
                    raise ValueError("容器浏览器未安装，请使用完整 Docker 镜像")
            self._cleanup()
            self.profile = tempfile.mkdtemp(prefix="nodeseek-browser-")
            default = Path(self.profile, "Default")
            default.mkdir()
            (default / "Preferences").write_text(json.dumps({"credentials_enable_service": False, "profile": {"password_manager_enabled": False}}))
            env = {**os.environ, "DISPLAY": ":99"}
            env["HOME"] = self.profile
            env["XDG_CONFIG_HOME"] = self.profile + "/.config"
            # 不把管理密码或 NodeSeek Cookie 传入浏览器子进程。
            for key in ("ADMIN_PASSWORD", "NODESEEK_COOKIE"):
                env.pop(key, None)
            commands = [
                ["Xvfb", ":99", "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-ac"],
                ["chromium", "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-background-networking", "--disable-extensions", "--disable-breakpad", "--disable-crash-reporter", "--password-store=basic", "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + self.profile, "--window-size=1280,800", "--start-maximized", "--app=https://www.nodeseek.com/"],
                ["x11vnc", "-display", ":99", "-localhost", "-rfbport", "5900", "-forever", "-shared", "-nopw", "-quiet"],
            ]
            self.generation += 1
            generation = self.generation
            self.deadline = time.monotonic() + 600
            self.imported_at = None
            self.message = "正在启动浏览器…"
            try:
                self.processes.append(subprocess.Popen(commands[0], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                for _ in range(50):
                    if Path("/tmp/.X11-unix/X99").exists():
                        break
                    time.sleep(0.1)
                for cmd in commands[1:]:
                    self.processes.append(subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            except Exception:
                self._cleanup()
                raise ValueError("容器浏览器启动失败") from None
            threading.Thread(target=self._watch, args=(generation,), daemon=True, name="browser-capture").start()
            return self.status()

    def _cdp(self):
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3) as r:
            info = json.load(r)
        ws = websocket.create_connection(info["webSocketDebuggerUrl"], timeout=3, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                result = json.loads(ws.recv())
                if result.get("id") == 1:
                    if "error" in result:
                        raise ValueError("浏览器尚未准备好")
                    return extract_cookie(result["result"]["cookies"]), info["User-Agent"]
        finally:
            ws.close()

    @staticmethod
    def _command(ws, command_id, method, params=None):
        ws.send(json.dumps({"id": command_id, "method": method, "params": params or {}}))
        while True:
            response = json.loads(ws.recv())
            if response.get("id") == command_id:
                if "error" in response:
                    raise ValueError("浏览器暂时无法接收粘贴，请重新点击输入框后再试")
                return response.get("result", {})

    def paste_text(self, text):
        """将临时文本插入远程页面当前输入框，不经过系统剪贴板或持久化存储。"""
        if not isinstance(text, str) or not text or len(text) > 4096:
            raise ValueError("请输入 1～4096 个字符的文本")
        if any(char in text for char in ("\x00", "\r", "\n")):
            raise ValueError("请粘贴单行账号、密码或验证码")
        with self.lock:
            if not self.status()["ready"]:
                raise ValueError("请先打开容器浏览器并等待连接")
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=3) as response:
                    pages = json.load(response)
                for page in pages:
                    if page.get("type") != "page" or not page.get("webSocketDebuggerUrl"):
                        continue
                    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=3, suppress_origin=True)
                    try:
                        focus = self._command(ws, 1, "Runtime.evaluate", {
                            "expression": """(() => {
                                let element = document.activeElement;
                                while (element?.shadowRoot?.activeElement) element = element.shadowRoot.activeElement;
                                const textInput = element?.tagName === 'INPUT' && ['text','password','email','search','tel','url','number'].includes(element.type);
                                return document.hasFocus() && !!element && !element.disabled && !element.readOnly &&
                                    (textInput || element.tagName === 'TEXTAREA' || element.isContentEditable);
                            })()""", "returnByValue": True,
                        })
                        if focus.get("result", {}).get("value") is not True:
                            continue
                        self._command(ws, 2, "Input.insertText", {"text": text})
                        return
                    finally:
                        ws.close()
            except ValueError:
                raise
            except Exception:
                raise ValueError("浏览器暂时无法接收粘贴，请重新点击输入框后再试") from None
            raise ValueError("请先在容器浏览器中点击账号、密码或验证码输入框，再粘贴")

    def capture(self):
        with self.lock:
            if not self.status()["running"]:
                raise ValueError("请先打开容器浏览器")
            if os.environ.get("NODESEEK_COOKIE"):
                raise ValueError("NODESEEK_COOKIE 环境变量正在覆盖配置，请移除此变量后重新部署")
            try:
                cookie, ua = self._cdp()
            except Exception:
                raise ValueError("浏览器尚未准备好，请稍后重试") from None
            if not cookie:
                raise ValueError("尚未检测到登录会话，请先在下方浏览器中完成登录")
            self.config.update({"cookie": cookie, "user_agent": ua})
            self.service.refresh_schedule()
            from .service import utcnow
            self.imported_at = utcnow().isoformat()
            self.message = "已自动导入登录 Cookie 和 User-Agent，可立即签到验证"
            log.info("已从容器浏览器导入 NodeSeek Cookie")
            self._cleanup()
            return self.status()

    def _watch(self, generation):
        while True:
            time.sleep(2)
            with self.lock:
                if generation != self.generation or not self.processes:
                    return
                if time.monotonic() >= self.deadline:
                    self.message = "浏览器会话已超时关闭，请重新打开"
                    self._cleanup()
                    return
                if not self.status()["running"]:
                    self.message = "浏览器进程已退出，请重新打开"
                    self._cleanup()
                    return
                try:
                    cookie, _ = self._cdp()
                    self.ready = True
                    self.message = "请在浏览器中手工登录，检测到会话后将自动导入"
                    if cookie:
                        self.capture()
                        return
                except Exception:
                    pass

    def _cleanup(self):
        self.ready = False
        for process in reversed(self.processes):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
        self.processes = []
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = None

    def close(self):
        with self.lock:
            self.generation += 1
            self._cleanup()
            self.message = "浏览器已关闭，临时登录资料已清理"
