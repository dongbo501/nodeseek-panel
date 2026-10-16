import json
import math
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nodeseek_signin import DEFAULT_UA

DEFAULTS = {
    "cookie": "", "random": True, "run_at": "08:00", "jitter_minutes": 10,
    "user_agent": DEFAULT_UA, "bark_url": "", "enabled": True,
    "timezone": "Asia/Shanghai",
}
SECRETS = ("cookie", "bark_url")


def validate(cfg):
    if not isinstance(cfg, dict):
        raise ValueError("配置必须是 JSON 对象")
    for key in ("random", "enabled"):
        if type(cfg[key]) is not bool:
            raise ValueError(f"{key} 必须为布尔值")
    if not isinstance(cfg["run_at"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", cfg["run_at"]):
        raise ValueError("签到时间应为 HH:MM，例如 08:00")
    jitter = cfg["jitter_minutes"]
    if type(jitter) not in (int, float) or not math.isfinite(jitter) or not 0 <= jitter <= 240:
        raise ValueError("随机延迟必须在 0～240 分钟之间")
    if not isinstance(cfg["timezone"], str):
        raise ValueError("时区必须为字符串")
    try:
        ZoneInfo(cfg["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("无效时区，例如 Asia/Shanghai") from None
    for key in ("cookie", "user_agent", "bark_url"):
        if not isinstance(cfg[key], str):
            raise ValueError(f"{key} 必须为字符串")
        cfg[key] = cfg[key].strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in cfg[key]):
            raise ValueError(f"{key} 不可包含换行或控制字符")
    if len(cfg["cookie"]) > 16384 or len(cfg["user_agent"]) > 1024 or len(cfg["bark_url"]) > 2048:
        raise ValueError("配置字段过长")
    if not cfg["user_agent"]:
        raise ValueError("User-Agent 不可为空")
    if cfg["bark_url"]:
        url = urlsplit(cfg["bark_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Bark 地址必须是有效的 HTTPS URL，且不能含用户名、密码或片段")
    return cfg


class ConfigStore:
    def __init__(self, directory):
        self.path = Path(directory) / "config.json"
        self.lock = threading.RLock()
        loaded = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(loaded, dict):
            raise ValueError("config.json 必须是 JSON 对象")
        self.cfg = validate({**DEFAULTS, **{k: v for k, v in loaded.items() if k in DEFAULTS}})
        with self.lock:
            self._write(self.cfg)

    def _write(self, cfg):
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self.path.chmod(0o600)

    def get(self):
        with self.lock:
            cfg = dict(self.cfg)
            if os.environ.get("NODESEEK_COOKIE"):
                cfg["cookie"] = os.environ["NODESEEK_COOKIE"].strip()
            return validate(cfg)

    def for_admin(self):
        """当前生效的完整配置；仅供通过管理员验证的接口返回。"""
        cfg = self.get()
        for key in SECRETS:
            cfg[key + "_configured"] = bool(cfg[key])
        cfg["cookie_from_env"] = bool(os.environ.get("NODESEEK_COOKIE"))
        return cfg

    def update(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("请求必须是 JSON 对象")
        unknown = set(changes) - set(DEFAULTS) - {"clear_cookie", "clear_bark_url"}
        if unknown:
            raise ValueError("存在未知配置字段")
        with self.lock:
            cfg = dict(self.cfg)
            for key, value in changes.items():
                if key in DEFAULTS and (key not in SECRETS or value != ""):
                    cfg[key] = value
            for key in SECRETS:
                clear = changes.get("clear_" + key, False)
                if type(clear) is not bool:
                    raise ValueError("清除标记必须为布尔值")
                if clear:
                    cfg[key] = ""
            cfg = validate(cfg)
            self._write(cfg)
            self.cfg = cfg
        return self.for_admin()
