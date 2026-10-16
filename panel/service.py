import hashlib
import json
import logging
import random
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from nodeseek_signin import execute_check_in, send_bark, safe_message

log = logging.getLogger("nodeseek")


def utcnow():
    return datetime.now(timezone.utc)


def next_run(cfg, now=None, rng=random):
    now = (now or utcnow()).astimezone(ZoneInfo(cfg["timezone"]))
    hour, minute = map(int, cfg["run_at"].split(":"))
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if base <= now:
        base += timedelta(days=1)
    return base + timedelta(seconds=rng.randint(0, int(cfg["jitter_minutes"] * 60)))


class Service:
    def __init__(self, directory, config, runner=None):
        self.config = config
        self.runner = runner or execute_check_in
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.busy = False
        self.thread = None
        self.db = sqlite3.connect(Path(directory) / "history.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                source TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.execute("UPDATE history SET status='fail', message='容器重启，执行结果未知，请查看 NodeSeek', finished_at=? WHERE status='running'", (utcnow().isoformat(),))
        self.db.commit()
        Path(directory, "history.sqlite3").chmod(0o600)
        self.refresh_schedule()

    def state(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, value))

    def refresh_schedule(self):
        with self.lock:
            cfg = self.config.get()
            signature = hashlib.sha256(json.dumps({k: cfg[k] for k in ("run_at", "jitter_minutes", "timezone", "enabled")}, sort_keys=True).encode()).hexdigest()
            active = cfg["enabled"] and bool(cfg["cookie"])
            current = self.state("next_run")
            if not active:
                current = ""
            elif self.state("schedule_signature") != signature or not current:
                current = next_run(cfg).isoformat()
            self.set_state("schedule_signature", signature)
            self.set_state("next_run", current)
            self.db.commit()
        self.wake.set()

    def history(self, limit=100):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))]

    def status(self):
        with self.lock:
            rows = self.history(1)
            return {"running": self.busy, "next_run": self.state("next_run") or None,
                    "last_result": rows[0] if rows else None,
                    "scheduler_alive": bool(self.thread and self.thread.is_alive())}

    def trigger(self, source="manual", synchronous=False):
        with self.lock:
            if self.busy:
                return False
            cfg = self.config.get()
            if not cfg["cookie"]:
                raise ValueError("请先配置 NodeSeek Cookie")
            self.busy = True
            rowid = self.db.execute("INSERT INTO history(started_at,source,status,message) VALUES (?,?,'running','正在签到')", (utcnow().isoformat(), source)).lastrowid
            self.db.commit()
        if synchronous:
            self._execute(rowid, cfg)
        else:
            threading.Thread(target=self._execute, args=(rowid, cfg), name="signin", daemon=True).start()
        return True

    def _execute(self, rowid, cfg):
        try:
            status, message = self.runner(cfg, stop_event=self.stop)
            if status not in ("ok", "already", "fail"):
                status, message = "fail", "签到返回了未知状态"
        except Exception as exc:
            status, message = "fail", "执行异常：" + type(exc).__name__
        message = safe_message(message, cfg)
        try:
            with self.lock:
                self.db.execute("UPDATE history SET status=?, message=?, finished_at=? WHERE id=?", (status, message, utcnow().isoformat(), rowid))
                self.db.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 1000)")
                self.db.commit()
            log.info("签到 %s: %s", status, message)
            title = {"ok": "NodeSeek 签到成功", "already": "NodeSeek 今日已签到", "fail": "NodeSeek 签到失败"}[status]
            send_bark(cfg, title, message)
        finally:
            with self.lock:
                self.busy = False
            self.wake.set()

    def tick(self, now=None):
        now = now or utcnow()
        with self.lock:
            cfg = self.config.get()
            target = self.state("next_run")
            if not cfg["enabled"] or not cfg["cookie"] or not target or self.busy:
                return
            target = datetime.fromisoformat(target)
            if target > now:
                return
            # 持久化下次时间后启动；重启不会重复领取今天的定时任务。
            self.set_state("next_run", next_run(cfg, now).isoformat())
            self.db.commit()
            if target.astimezone(ZoneInfo(cfg["timezone"])).date() != now.astimezone(ZoneInfo(cfg["timezone"])).date():
                log.info("跳过停机期间过期的任务，已安排下次签到")
                return
            self.trigger("scheduled")

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    log.error("定时任务检查异常: %s", type(exc).__name__)
                self.wake.wait(15)
                self.wake.clear()
        self.thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
