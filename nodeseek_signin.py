#!/usr/bin/env python3
"""NodeSeek 每日签到脚本（仅使用 Python 标准库，无需安装第三方包）。

用法:
  python3 nodeseek_signin.py              常驻运行：每天 run_at 起、jitter_minutes 分钟内的随机时刻签到
  python3 nodeseek_signin.py --once       随机等待 0~jitter_minutes 分钟后签到一次（适合 cron / 计划任务在 08:00 调用）
  python3 nodeseek_signin.py --once --no-delay   立即签到，不等待（用于测试）
"""
import argparse
import http.client
import json
import logging
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "signin.log"

ATTENDANCE_URL = "https://www.nodeseek.com/api/attendance"
REFERER = "https://www.nodeseek.com/board"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
RETRIES = 3          # 网络异常时的最大尝试次数
RETRY_DELAY = 60     # 两次尝试之间的等待秒数
TIMEOUT = 20

log = logging.getLogger("nodeseek")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Cookie 和 Bark 密钥不得随 HTTP 重定向发送到其他主机。
OPENER = urllib.request.build_opener(NoRedirect())


def safe_message(message, cfg):
    text = str(message)
    secrets = [cfg.get("cookie", ""), cfg.get("bark_url", "")]
    secrets += [part.split("=", 1)[-1].strip() for part in cfg.get("cookie", "").split(";") if "=" in part]
    if cfg.get("bark_url"):
        secrets += [p for p in urlsplit(cfg["bark_url"]).path.split("/") if p]
    for value in sorted(set(secrets), key=len, reverse=True):
        if value:
            text = text.replace(value, "[已隐藏]")
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)[:2000]


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def load_config():
    cfg = {
        "cookie": "",
        "random": True,
        "run_at": "08:00",
        "jitter_minutes": 10,
        "user_agent": DEFAULT_UA,
        "bark_url": "",
    }
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open(encoding="utf-8") as f:
            cfg.update(json.load(f))
    env_cookie = os.environ.get("NODESEEK_COOKIE")
    if env_cookie:
        cfg["cookie"] = env_cookie
    # 去掉首尾空白/换行，否则 urllib 会因请求头含换行抛出 ValueError
    cfg["cookie"] = str(cfg["cookie"] or "").strip()
    if not cfg["cookie"]:
        raise SystemExit(
            "未配置 cookie：请填写 config.json 的 cookie 字段，或设置环境变量 NODESEEK_COOKIE"
        )
    try:
        hour, minute = (int(x) for x in str(cfg["run_at"]).split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
        float(cfg["jitter_minutes"])
    except (ValueError, TypeError):
        raise SystemExit("config.json 配置有误：run_at 应形如 \"08:00\"，jitter_minutes 应为数字")
    return cfg


class TransientError(Exception):
    """服务器临时错误（如 Cloudflare 502/503），值得重试。"""


def check_in(cfg):
    """发送一次签到请求。返回 (状态, 信息)，状态为 ok / already / fail。
    网络层异常直接抛出，由调用方决定是否重试。"""
    url = ATTENDANCE_URL + ("?random=true" if cfg["random"] else "")
    req = urllib.request.Request(
        url,
        data=None,
        method="POST",
        headers={
            "User-Agent": cfg["user_agent"],
            "Referer": REFERER,
            "Origin": "https://www.nodeseek.com",
            "Cookie": cfg["cookie"],
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with OPENER.open(req, timeout=TIMEOUT) as resp:
            code, body = resp.status, resp.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # 接口对"今日已签到"等情况会返回非 200 状态码（实测为 500），但正文仍是 JSON，需要照常解析
        code, body = e.code, e.read(65536).decode("utf-8", errors="replace")
        e.close()

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        if code in (401, 403) and "<html" in body.lower():
            return "fail", f"HTTP {code}，被 Cloudflare 拦截或 cookie 已失效（请更新 cookie，必要时带上 cf_clearance）"
        if code >= 500:
            raise TransientError(f"HTTP {code}，服务器临时错误")
        return "fail", f"HTTP {code}，返回的不是 JSON，请检查站点或 Cookie"
    if not isinstance(data, dict):
        return "fail", f"HTTP {code}，返回格式异常"

    message = str(data.get("message") or data.get("msg") or data)
    if data.get("success") is True:
        return "ok", message
    if "已" in message and "签到" in message:
        return "already", message
    if code == 429 or code >= 500:
        raise TransientError(f"HTTP {code}，服务器临时错误或限流")
    return "fail", f"HTTP {code}: {message}"


def send_bark(cfg, title, body):
    """通过 Bark 推送通知。未配置 bark_url 时跳过；推送失败只记录日志，不影响签到。"""
    bark_url = (cfg.get("bark_url") or "").strip().rstrip("/")
    if not bark_url:
        return
    payload = json.dumps({"title": title, "body": body, "group": "NodeSeek"}).encode("utf-8")
    req = urllib.request.Request(
        bark_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with OPENER.open(req, timeout=TIMEOUT) as resp:
            resp.read(65536)
        log.info("Bark 通知已发送")
    except Exception as e:  # 通知失败不应影响主流程
        log.warning("Bark 通知发送失败: %s", type(e).__name__)


def execute_check_in(cfg, stop_event=None):
    status, message = "fail", "网络异常，已重试 %d 次" % RETRIES
    for attempt in range(1, RETRIES + 1):
        try:
            status, message = check_in(cfg)
            break
        except (OSError, http.client.HTTPException, TransientError) as e:
            log.warning("第 %d/%d 次尝试网络异常: %s", attempt, RETRIES, type(e).__name__)
            status, message = "fail", "网络异常: %s" % type(e).__name__
            if attempt < RETRIES:
                if stop_event is not None:
                    if stop_event.wait(RETRY_DELAY):
                        return "fail", "服务正在停止，已取消重试"
                else:
                    time.sleep(RETRY_DELAY)
    return status, safe_message(message, cfg)


def run_once(cfg):
    status, message = execute_check_in(cfg)

    if status == "ok":
        log.info("签到成功: %s", message)
        title = "NodeSeek 签到成功"
    elif status == "already":
        log.info("今日已签到: %s", message)
        title = "NodeSeek 今日已签到"
    else:
        log.error("签到失败: %s", message)
        title = "NodeSeek 签到失败"

    send_bark(cfg, title, message)
    return status != "fail"


def seconds_until(run_at):
    hour, minute = (int(x) for x in run_at.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target, (target - now).total_seconds()


def jitter_seconds(cfg):
    """返回 0 ~ jitter_minutes 分钟之间的随机秒数。"""
    limit = int(float(cfg.get("jitter_minutes", 0)) * 60)
    return random.randint(0, limit) if limit > 0 else 0


def sleep_until(when):
    """按墙上时钟分段睡眠到指定时刻。
    time.sleep 不计入系统休眠的时间，一次长睡眠在电脑休眠/唤醒后会大幅延后。"""
    while True:
        remaining = (when - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def daemon(cfg):
    log.info("常驻模式启动：每天 %s 起 %s 分钟内随机签到（Ctrl+C 退出）",
             cfg["run_at"], cfg["jitter_minutes"])
    while True:
        target, _ = seconds_until(cfg["run_at"])
        run_time = target + timedelta(seconds=jitter_seconds(cfg))
        log.info("下次签到时间: %s", run_time.strftime("%Y-%m-%d %H:%M:%S"))
        sleep_until(run_time)
        try:
            run_once(cfg)
        except Exception:  # 任何意外异常都不应让常驻进程退出
            log.exception("签到过程出现未预期的异常")
        time.sleep(60)  # 避免在同一分钟内重复触发


def main():
    parser = argparse.ArgumentParser(description="NodeSeek 每日签到")
    parser.add_argument("--once", action="store_true", help="签到一次后退出（先随机等待）")
    parser.add_argument("--no-delay", action="store_true", help="与 --once 合用：不随机等待，立即签到")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config()
    try:
        if args.once:
            if not args.no_delay:
                extra = jitter_seconds(cfg)
                log.info("随机等待 %d 秒后签到", extra)
                time.sleep(extra)
            sys.exit(0 if run_once(cfg) else 1)
        daemon(cfg)
    except KeyboardInterrupt:
        log.info("已退出")


if __name__ == "__main__":
    main()
