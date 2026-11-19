"""String 和星驰控制台的每日签到，共用 Bearer 认证和令牌续期。"""
import http.client
import json
import time
import urllib.error
import urllib.request
from datetime import date

from nodeseek_signin import NoRedirect, RETRIES, RETRY_DELAY, TIMEOUT

from .config import SITES, TOKEN_ORIGINS

OPENER = urllib.request.build_opener(NoRedirect())


class APIError(Exception):
    def __init__(self, message, status=0, transient=False):
        super().__init__(message)
        self.status, self.transient = status, transient


def request(cfg, path, method="GET", body=None, authenticated=True):
    origin = TOKEN_ORIGINS[cfg["site"]]
    name = SITES[cfg["site"]]
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": cfg["user_agent"], "Origin": origin,
               "Referer": origin + "/dashboard"}
    if authenticated:
        headers["Authorization"] = "Bearer " + cfg["access_token"]
    req = urllib.request.Request(origin + "/api/v1" + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with OPENER.open(req, timeout=TIMEOUT) as response:
            code, raw = response.status, response.read(65536)
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, exc.read(65536)
        exc.close()
    if code == 401:
        raise APIError(f"{name} 登录已失效，请重新导入登录凭证", code)
    if code == 403:
        raise APIError(f"{name} 拒绝请求，请在网站检查登录状态或站点防护", code)
    if code == 429 or code >= 500:
        raise APIError(f"{name} HTTP {code}，暂时限流或服务异常", code, transient=True)
    if cfg["site"] == "xingchi" and method == "POST" and path == "/user/checkin" and 400 <= code < 500:
        try:
            error = json.loads(raw)
        except (ValueError, UnicodeError):
            error = None
        if isinstance(error, dict) and "CHECKIN_ALREADY_CLAIMED" in (error.get("reason"), error.get("code")):
            raise APIError("星驰 API 今日已签到", 409)
    if not 200 <= code < 300:
        raise APIError(f"{name} HTTP {code}，请求未成功", code)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise APIError(f"{name} 返回的不是 JSON，请检查网站或登录状态") from None
    if not isinstance(data, dict) or type(data.get("code")) is not int or data["code"] != 0 or "data" not in data:
        # 不将远端原始响应写入日志或通知，避免回显凭证。
        raise APIError(f"{name} 接口返回异常，请在网站检查签到状态")
    return data["data"]


def validate_status(data):
    try:
        date.fromisoformat(data["day"])
        basic = data["basic"]
        if type(basic["today"]) is not int or basic["today"] < 0:
            raise ValueError
        if type(data["rules"]["basic"]["enabled"]) is not bool:
            raise ValueError
        if not isinstance(data.get("tickets", []), list):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise APIError("String 签到状态格式已变化，未提交签到，请更新适配") from None
    return data


def result_message(data):
    message = f"{data['day']} 普通签到奖励 +{data['basic']['today'] / 100:.2f} 积分"
    if data.get("tickets"):
        message += f"；有 {len(data['tickets'])} 张奖励券待领取，请到网站手动开启"
    return message


def claim_string(authorized):
    before = validate_status(authorized("/user/checkin"))
    if before["basic"]["today"] > 0:
        return "already", result_message(before)
    if not before["rules"]["basic"]["enabled"]:
        return "fail", "String 当前未开放普通签到"
    try:
        authorized("/user/checkin", "POST")
    except APIError as exc:
        if exc.status != 409:
            raise
    after = validate_status(authorized("/user/checkin"))
    if after["basic"]["today"] > 0:
        return "ok", result_message(after)
    return "fail", "String 未确认签到成功，请在网站查看记录"


def claim_xingchi(authorized):
    path = "/user/checkin"
    status = authorized(path)
    if not isinstance(status, dict) or type(status.get("enabled")) is not bool:
        raise APIError("星驰 API 签到状态格式异常，请检查控制台")
    if not status["enabled"]:
        return "fail", "星驰 API 当前未开放每日签到"
    if type(status.get("claimed_today")) is not bool:
        raise APIError("星驰 API 未返回今日签到状态")
    if status["claimed_today"]:
        return "already", "星驰 API 今日已签到"

    try:
        receipt = authorized(path, "POST")
    except APIError as exc:
        if exc.status != 409:
            raise
        after = authorized(path)
        if isinstance(after, dict) and after.get("claimed_today") is True:
            return "already", "星驰 API 今日已签到"
    else:
        if isinstance(receipt, dict):
            after = receipt.get("status")
            if isinstance(after, dict) and after.get("claimed_today") is True:
                amount = receipt.get("amount")
                if type(amount) in (float, int) and 0 <= amount < float("inf"):
                    return "ok", f"星驰 API 签到成功，奖励额度 +{amount}"
                return "ok", "星驰 API 签到成功"
        # 提交后响应格式变化时，使用控制台的当日状态确认。
        after = authorized(path)
        if isinstance(after, dict) and after.get("claimed_today") is True:
            return "ok", "星驰 API 签到成功"
    return "fail", "星驰 API 未确认签到成功，请查看控制台"


def execute_check_in(cfg, stop_event=None, save_tokens=None):
    cfg = dict(cfg)
    name = SITES[cfg["site"]]
    claim = {"string": claim_string, "xingchi": claim_xingchi}[cfg["site"]]

    def authorized(path, method="GET"):
        try:
            return request(cfg, path, method)
        except APIError as exc:
            if exc.status != 401 or not cfg.get("refresh_token"):
                raise
        if save_tokens is None:
            raise APIError(f"{name} 登录需要续期，但未提供凭证保存功能，请重新登录")
        expected = {key: cfg[key] for key in ("access_token", "refresh_token")}
        tokens = request(cfg, "/auth/refresh", "POST", {"refresh_token": cfg["refresh_token"]}, authenticated=False)
        if not isinstance(tokens, dict) or any(not isinstance(tokens.get(key), str) or not tokens[key].strip()
                                               for key in expected):
            raise APIError(f"{name} 续期响应无效，请重新导入登录凭证")
        updated = {key: tokens[key] for key in expected}
        # 刷新令牌会轮换，先原子保存，再进行签到。若用户同时换号则停止此次任务。
        if not save_tokens(updated, expected):
            raise APIError(f"{name} 登录配置已更改，本次任务已停止，请重新执行")
        cfg.update(updated)
        return request(cfg, path, method)

    message = f"{name} 网络异常"
    for attempt in range(RETRIES):
        if stop_event is not None and stop_event.is_set():
            return "fail", "服务正在停止，已取消签到"
        try:
            return claim(authorized)
        except APIError as exc:
            message = str(exc)
            if not exc.transient:
                return "fail", message
        except (OSError, http.client.HTTPException) as exc:
            message = f"{name} 网络异常：" + type(exc).__name__
        if attempt + 1 < RETRIES:
            if stop_event is not None:
                if stop_event.wait(RETRY_DELAY):
                    return "fail", "服务正在停止，已取消重试"
            else:
                time.sleep(RETRY_DELAY)
        # 超时后下一轮先查状态，避免直接重复提交。
    return "fail", message
