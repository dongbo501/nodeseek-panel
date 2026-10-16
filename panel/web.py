import atexit
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import threading
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session, send_from_directory
from flask_sock import Sock
from werkzeug.security import check_password_hash, generate_password_hash

from .config import ConfigStore
from .service import Service
from .browser import BrowserManager


def private_write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(value)


def create_app(data_dir=None, start_scheduler=True, runner=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    directory = Path(data_dir or os.environ.get("DATA_DIR", "/data"))
    directory.mkdir(parents=True, exist_ok=True)
    secret_path = directory / "session.key"
    if not secret_path.exists():
        private_write(secret_path, secrets.token_hex(32))
    password = os.environ.get("ADMIN_PASSWORD", "")
    auth_path = directory / "auth.json"
    if password:
        if len(password) < 12:
            raise ValueError("ADMIN_PASSWORD 至少需要 12 个字符")
        password_hash = generate_password_hash(password)
    elif auth_path.exists():
        password_hash = json.loads(auth_path.read_text())["password_hash"]
    else:
        password = secrets.token_urlsafe(18)
        password_hash = generate_password_hash(password)
        private_write(auth_path, json.dumps({"password_hash": password_hash}))
        print("首次启动管理密码（仅显示一次，请保存）: " + password, flush=True)
    auth_version = hashlib.sha256(password_hash.encode()).hexdigest()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_path.read_text(), MAX_CONTENT_LENGTH=32768,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    config = ConfigStore(directory)
    service = Service(directory, config, runner=runner)
    browser = BrowserManager(config, service)
    app.extensions["config_store"] = config
    app.extensions["signin_service"] = service
    app.extensions["browser_manager"] = browser
    attempts = {}
    auth_lock = threading.Lock()

    def authenticated():
        return session.get("auth_version") == auth_version

    def login_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not authenticated():
                return jsonify(error="请先登录"), 401
            return fn(*args, **kwargs)
        return wrapper

    @app.before_request
    def protect_mutations():
        if request.path.startswith("/browser/"):
            if not authenticated():
                return jsonify(error="请先登录"), 401
            if request.path == "/browser/ws" and request.headers.get("Origin", "").split("://", 1)[-1] != request.host:
                return jsonify(error="浏览器来源验证失败"), 403
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            csrf = session.get("csrf", "")
            given = request.headers.get("X-CSRF-Token", "")
            if not csrf or not hmac.compare_digest(csrf, given):
                return jsonify(error="页面验证已失效，请刷新后重试"), 403
            if not request.is_json:
                return jsonify(error="请使用 JSON 请求"), 415

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid_value(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(400)
    def bad_request(exc):
        return jsonify(error="请求格式无效"), 400

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error="请求内容过长"), 413

    @app.errorhandler(500)
    def server_error(exc):
        return jsonify(error="服务异常，请查看容器日志"), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/healthz")
    def health():
        alive = service.status()["scheduler_alive"] or not start_scheduler
        return jsonify(status="ok" if alive else "error"), 200 if alive else 503

    @app.get("/api/session")
    def session_info():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(authenticated=authenticated(), csrf=session["csrf"])

    @app.post("/api/login")
    def login():
        body = request.get_json()
        if not isinstance(body, dict) or not isinstance(body.get("password"), str):
            raise ValueError("请输入密码")
        address, now = request.remote_addr, time.monotonic()
        with auth_lock:
            recent = [v for v in attempts.get(address, []) if now - v < 300]
            if len(recent) >= 5:
                return jsonify(error="登录失败次数过多，请 5 分钟后重试"), 429
            if len(attempts) >= 2048 and address not in attempts:
                attempts.pop(next(iter(attempts)))
            attempts[address] = recent + [now]
        if not check_password_hash(password_hash, body["password"]):
            return jsonify(error="密码不正确"), 401
        with auth_lock:
            attempts.pop(address, None)
        session.clear()
        session.permanent = True
        session["auth_version"] = auth_version
        session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(authenticated=True, csrf=session["csrf"])

    @app.post("/api/logout")
    @login_required
    def logout():
        browser.close()
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/config")
    @login_required
    def get_config():
        return jsonify(config.for_admin())

    @app.put("/api/config")
    @login_required
    def save_config():
        result = config.update(request.get_json())
        service.refresh_schedule()
        return jsonify(result)

    @app.get("/api/status")
    @login_required
    def get_status():
        return jsonify(service.status())

    @app.get("/api/history")
    @login_required
    def history():
        return jsonify(items=service.history())

    @app.post("/api/run")
    @login_required
    def run():
        if not service.trigger():
            return jsonify(error="已有签到任务正在运行"), 409
        return jsonify(message="签到任务已开始，请稍后查看执行记录"), 202

    @app.get("/api/browser")
    @login_required
    def browser_status():
        return jsonify(browser.status())

    @app.post("/api/browser/start")
    @login_required
    def browser_start():
        if os.environ.get("NODESEEK_COOKIE"):
            raise ValueError("请先移除 NODESEEK_COOKIE 环境变量，以便从浏览器导入")
        return jsonify(browser.start())

    @app.post("/api/browser/capture")
    @login_required
    def browser_capture():
        return jsonify(browser.capture())

    @app.post("/api/browser/stop")
    @login_required
    def browser_stop():
        browser.close()
        return jsonify(browser.status())

    @app.post("/api/browser/paste")
    @login_required
    def browser_paste():
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象")
        browser.paste_text(body.get("text"))
        return jsonify(message="已粘贴到浏览器当前输入框")

    @app.get("/browser/<path:filename>")
    def browser_assets(filename):
        return send_from_directory("/usr/share/novnc", filename)

    @app.get("/browser/view")
    def browser_view():
        return render_template("browser.html")

    sock = Sock(app)

    @sock.route("/browser/ws")
    def browser_socket(ws):
        if not browser.status()["ready"]:
            ws.close()
            return
        try:
            upstream = socket.create_connection(("127.0.0.1", 5900), timeout=5)
        except OSError:
            ws.close()
            return
        done = threading.Event()
        upstream.settimeout(1)

        def relay():
            try:
                while not done.is_set():
                    try:
                        payload = upstream.recv(65536)
                    except socket.timeout:
                        continue
                    if not payload:
                        break
                    ws.send(payload)
            except Exception:
                pass
            finally:
                done.set()
                ws.close()
        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        try:
            while not done.is_set():
                payload = ws.receive(timeout=1)
                if payload is not None:
                    if not isinstance(payload, bytes):
                        break
                    upstream.sendall(payload)
        except Exception:
            pass
        finally:
            done.set()
            upstream.close()
            reader.join(timeout=2)

    if start_scheduler:
        service.start()
        atexit.register(service.shutdown)
        atexit.register(browser.close)
    return app
