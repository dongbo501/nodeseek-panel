"""根据 String 公开前端确认的接口契约测试；不使用真实凭证，不提交真实签到。"""
import copy
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from nodeseek_signin import safe_message
from panel import token_signin as api
from panel.browser import BrowserManager
from panel.config import ConfigStore
from panel.service import Service
from panel.web import create_app

CFG = {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "user_agent": "Test browser"}
STRING_CFG = {**CFG, "site": "string"}
STATUS = {"day": "2026-11-19", "rules": {"basic": {"enabled": True}},
          "basic": {"today": 0, "progress": 0, "days": 7}, "tickets": []}


def status(cents=0, tickets=None):
    result = copy.deepcopy(STATUS)
    result["basic"]["today"] = cents
    result["tickets"] = tickets or []
    return result


def response(data, code=200):
    result = io.BytesIO(json.dumps(data).encode())
    result.status = code
    return result


class StringRequestTests(unittest.TestCase):
    def test_request_matches_frontend_and_never_sends_nodeseek_cookie(self):
        with patch.object(api.OPENER, "open", return_value=response({"code": 0, "data": status(2)})) as send:
            result = api.request({**STRING_CFG, "cookie": "session=private-node"}, "/user/checkin")
        req = send.call_args.args[0]
        self.assertEqual(req.full_url, "https://www.string.ink/api/v1/user/checkin")
        self.assertEqual(req.get_header("Authorization"), "Bearer fixture-access")
        self.assertIsNone(req.get_header("Cookie"))
        self.assertEqual(result["basic"]["today"], 2)

    def test_refresh_sends_only_refresh_payload(self):
        with patch.object(api.OPENER, "open", return_value=response({"code": 0, "data": STRING_CFG})) as send:
            api.request(STRING_CFG, "/auth/refresh", "POST", {"refresh_token": STRING_CFG["refresh_token"]}, authenticated=False)
        req = send.call_args.args[0]
        self.assertEqual(json.loads(req.data), {"refresh_token": "fixture-refresh"})
        self.assertIsNone(req.get_header("Authorization"))

    def test_errors_are_actionable_and_do_not_echo_server_secrets(self):
        for code in (302, 401, 403, 409, 429, 500):
            with self.subTest(code=code):
                error = urllib.error.HTTPError("https://www.string.ink/api/v1", code, "error", {}, io.BytesIO(b"fixture-access fixture-refresh"))
                with patch.object(api.OPENER, "open", side_effect=error), self.assertRaises(api.APIError) as caught:
                    api.request(STRING_CFG, "/user/checkin")
                self.assertEqual(caught.exception.status, code)
                self.assertEqual(caught.exception.transient, code in (429, 500))
                self.assertNotIn("fixture", str(caught.exception))

    def test_unexpected_envelopes_fail_closed(self):
        for data in ([], {"code": True, "data": {}}, {"code": 0}, {"code": 42, "message": "fixture-access"}):
            with self.subTest(data=data), patch.object(api.OPENER, "open", return_value=response(data)), self.assertRaises(api.APIError):
                api.request(STRING_CFG, "/user/checkin")
        raw = io.BytesIO(b"<html>please login</html>"); raw.status = 200
        with patch.object(api.OPENER, "open", return_value=raw), self.assertRaises(api.APIError):
            api.request(STRING_CFG, "/user/checkin")

    def test_opener_blocks_redirects(self):
        redirect = next(handler for handler in api.OPENER.handlers if isinstance(handler, api.NoRedirect))
        self.assertIsNone(redirect.redirect_request(None, None, 302, None, {}, "https://other.example"))


class StringRunnerTests(unittest.TestCase):
    def test_already_signed_skips_post_and_mentions_tickets(self):
        with patch.object(api, "request", return_value=status(2, [{"id": 123, "track": "basic"}])) as request:
            outcome, message = api.execute_check_in(STRING_CFG)
        self.assertEqual(outcome, "already")
        self.assertIn("+0.02", message)
        self.assertIn("手动开启", message)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[2], "GET")

    def test_sign_checks_result_after_post(self):
        with patch.object(api, "request", side_effect=[status(), {}, status(3)]) as request:
            outcome, message = api.execute_check_in(STRING_CFG)
        self.assertEqual(outcome, "ok")
        self.assertIn("+0.03", message)
        self.assertEqual([call.args[2] for call in request.call_args_list], ["GET", "POST", "GET"])

    def test_unconfirmed_post_is_not_reported_as_success(self):
        with patch.object(api, "request", side_effect=[status(), {}, status()]):
            self.assertEqual(api.execute_check_in(STRING_CFG)[0], "fail")

    def test_unknown_status_or_disabled_rule_never_submits(self):
        disabled = status(); disabled["rules"]["basic"]["enabled"] = False
        malformed = status(); malformed["basic"]["today"] = "2"
        for data in ({}, None, malformed, disabled):
            with self.subTest(data=data), patch.object(api, "request", return_value=data) as request:
                self.assertEqual(api.execute_check_in(STRING_CFG)[0], "fail")
                self.assertEqual(request.call_count, 1)

    def test_timeout_after_post_rechecks_before_another_submission(self):
        with patch.object(api, "request", side_effect=[status(), TimeoutError(), status(2)]) as request, patch.object(api.time, "sleep"):
            self.assertEqual(api.execute_check_in(STRING_CFG)[0], "already")
        self.assertEqual([call.args[2] for call in request.call_args_list], ["GET", "POST", "GET"])

    def test_conflict_is_verified(self):
        with patch.object(api, "request", side_effect=[status(), api.APIError("conflict", 409), status(2)]):
            self.assertEqual(api.execute_check_in(STRING_CFG)[0], "ok")

    def test_rotated_tokens_are_saved_before_using_them(self):
        tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
        save = Mock(return_value=True)
        def request(cfg, path, method="GET", *args, **kwargs):
            if path == "/auth/refresh":
                self.assertFalse(kwargs["authenticated"])
                return tokens
            if cfg["access_token"] == "fixture-access":
                raise api.APIError("expired", 401)
            save.assert_called_once_with(tokens, {key: STRING_CFG[key] for key in tokens})
            self.assertEqual(cfg["access_token"], "new-access")
            return status(2)
        with patch.object(api, "request", side_effect=request):
            self.assertEqual(api.execute_check_in(STRING_CFG, save_tokens=save)[0], "already")
        self.assertEqual(STRING_CFG["access_token"], "fixture-access")

    def test_config_change_during_refresh_stops_task(self):
        with patch.object(api, "request", side_effect=[api.APIError("expired", 401), STRING_CFG]) as request:
            result = api.execute_check_in(STRING_CFG, save_tokens=Mock(return_value=False))
        self.assertEqual(result[0], "fail")
        self.assertIn("配置已更改", result[1])
        self.assertEqual(request.call_count, 2)

    def test_expired_or_invalid_refresh_stops_without_leaking(self):
        for refreshed in (None, {"access_token": "new"}, api.APIError("expired", 401)):
            with self.subTest(refreshed=refreshed), patch.object(api, "request", side_effect=[api.APIError("expired", 401), refreshed]) as request:
                self.assertEqual(api.execute_check_in(STRING_CFG, save_tokens=Mock())[0], "fail")
                self.assertEqual(request.call_count, 2)
        with patch.object(api, "request", side_effect=api.APIError("expired", 401)) as request:
            self.assertEqual(api.execute_check_in({**STRING_CFG, "refresh_token": ""})[0], "fail")
            self.assertEqual(request.call_count, 1)

    def test_retry_exhaustion_and_cancellation(self):
        with patch.object(api, "request", side_effect=api.APIError("busy", 429, True)) as request, patch.object(api.time, "sleep"):
            self.assertEqual(api.execute_check_in(STRING_CFG)[0], "fail")
            self.assertEqual(request.call_count, 3)
        stop = threading.Event(); stop.set()
        with patch.object(api, "request") as request:
            self.assertEqual(api.execute_check_in(STRING_CFG, stop)[0], "fail")
            request.assert_not_called()


class MultiSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"ADMIN_PASSWORD": "fixture-admin-password", "NODESEEK_COOKIE": ""})
        self.env.start()
        self.app = create_app(self.temp.name, start_scheduler=False)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").json["csrf"]
        self.csrf = self.send("login", {"password": "fixture-admin-password"}).json["csrf"]
        self.stores = self.app.extensions["site_configs"]
        self.services = self.app.extensions["site_services"]

    def tearDown(self):
        self.app.extensions["browser_manager"].close()
        for service in self.services.values():
            service.shutdown(); service.db.close()
        self.env.stop(); self.temp.cleanup()

    def send(self, path, data=None, method="POST"):
        return self.client.open("/api/" + path, method=method, json=data or {}, headers={"X-CSRF-Token": self.csrf})

    def test_config_history_schedule_are_isolated_and_legacy_path_works(self):
        self.assertEqual(self.send("config", {"cookie": "session=node-private", "run_at": "08:00"}, "PUT").status_code, 200)
        self.assertEqual(self.send("config?site=string", {**CFG, "run_at": "09:00"}, "PUT").status_code, 200)
        node = self.client.get("/api/config").json
        string = self.client.get("/api/config?site=string").json
        self.assertNotIn("access_token", node)
        self.assertEqual(string["cookie"], "")
        self.assertEqual(string["run_at"], "09:00")
        self.assertTrue(string["credential_configured"])
        self.assertIsNone(self.services["xingchi"].status()["next_run"])
        self.assertEqual(self.send("config?site=xingchi", {"access_token": "fixture-xingchi"}, "PUT").status_code, 200)
        self.assertEqual(self.stores["string"].get()["access_token"], CFG["access_token"])
        for site, service in self.services.items():
            self.assertIsNotNone(service.status()["next_run"])
            with patch.object(service, "runner", return_value=("ok", site)), patch("panel.service.notify_check_in"):
                service.trigger(synchronous=True)
            self.assertEqual(service.history()[0]["message"], site)
        self.assertEqual(self.client.get("/api/history?site=string").json["items"][0]["message"], "string")
        restarted = ConfigStore(Path(self.temp.name) / "string", site="string")
        self.assertEqual(restarted.get()["refresh_token"], CFG["refresh_token"])
        self.assertEqual(restarted.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.send("config?site=string", {"enabled": False}, "PUT").status_code, 200)
        self.assertIsNone(self.services["string"].status()["next_run"])
        self.assertIsNotNone(self.services["nodeseek"].status()["next_run"])

    def test_site_endpoints_require_auth_csrf_and_known_site(self):
        anonymous = self.app.test_client()
        for path in ("config", "status", "history"):
            self.assertEqual(anonymous.get("/api/" + path + "?site=string").status_code, 401)
            self.assertEqual(self.client.get("/api/" + path + "?site=unknown").status_code, 400)
        self.assertEqual(self.client.put("/api/config?site=string", json=CFG).status_code, 403)
        self.assertEqual(self.send("run?site=string").status_code, 400)

    def test_token_validation_clear_rotation_and_account_replacement(self):
        store = self.stores["string"]; store.update(CFG)
        for value in (False, None, "Bearer some-token", "bad\nheader", "x" * 16385):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                store.update({"access_token": value})
            self.assertEqual(store.get()["access_token"], CFG["access_token"])
        store.update({"access_token": "", "refresh_token": ""})
        self.assertEqual(store.get()["access_token"], CFG["access_token"])
        expected = {key: CFG[key] for key in ("access_token", "refresh_token")}
        updated = {"access_token": "rotated-access", "refresh_token": "rotated-refresh"}
        self.assertTrue(store.rotate_tokens(updated, expected))
        self.assertFalse(store.rotate_tokens(expected, expected))
        store.update({"access_token": "different-account"})
        self.assertEqual(store.get()["refresh_token"], "")
        store.update({"clear_access_token": True, "clear_refresh_token": True})
        self.assertFalse(store.for_admin()["credential_configured"])

    def test_nodeseek_environment_cookie_never_enters_string(self):
        with patch.dict(os.environ, {"NODESEEK_COOKIE": "session=env-node"}):
            self.assertEqual(self.stores["nodeseek"].get()["cookie"], "session=env-node")
            self.assertEqual(self.stores["string"].get()["cookie"], "")
            self.assertFalse(self.stores["string"].for_admin()["cookie_from_env"])

    def test_string_service_uses_refresh_persistence_and_string_notifications(self):
        store = self.stores["string"]; store.update(CFG)
        tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
        with patch.object(api, "request", side_effect=[api.APIError("expired", 401), tokens, status(2)]), patch("nodeseek_signin.send_bark") as bark:
            self.services["string"].trigger(synchronous=True)
        self.assertEqual(store.get()["refresh_token"], "new-refresh")
        self.assertEqual(bark.call_args.args[1], "String 今日已签到")
        self.assertEqual(self.services["string"].history()[0]["status"], "already")
        self.assertEqual(safe_message("fixture-access fixture-refresh", CFG), "[已隐藏] [已隐藏]")

    def test_string_browser_import_and_other_site_session_conflict(self):
        store, service = self.stores["string"], self.services["string"]
        browser = BrowserManager(store, service)
        tokens = {key: CFG[key] for key in ("access_token", "refresh_token")}
        with patch.object(browser, "status", return_value={"running": True}), patch.object(browser, "_cdp", return_value=(tokens, "Browser UA")), patch.object(browser, "_cleanup") as cleanup:
            browser.capture()
            self.assertEqual(store.get()["access_token"], CFG["access_token"])
            self.assertEqual(store.get()["user_agent"], "Browser UA")
            self.assertEqual(self.stores["nodeseek"].get()["cookie"], "")
            cleanup.assert_called_once()
            with self.assertRaisesRegex(ValueError, "另一个站点"):
                browser.start(self.stores["nodeseek"], self.services["nodeseek"])

    def test_browser_reads_tokens_only_from_exact_string_origin(self):
        pages = [{"type": "page", "url": url, "webSocketDebuggerUrl": f"ws://fixture/{i}"} for i, url in enumerate([
            "https://accounts.google.com", "https://www.string.ink.evil.example", "http://www.string.ink",
            "https://www.string.ink:444", "https://www.string.ink@evil.example", "https://www.string.ink/dashboard"])]
        browser = BrowserManager(self.stores["string"], self.services["string"])
        socket = Mock()
        socket.recv.return_value = json.dumps({"id": 1, "result": {"result": {"value": CFG}}})
        with patch("panel.browser.urllib.request.urlopen", return_value=response(pages)), patch("panel.browser.websocket.create_connection", return_value=socket) as connect:
            self.assertEqual(browser._login_tokens(), {key: CFG[key] for key in ("access_token", "refresh_token")})
        connect.assert_called_once_with("ws://fixture/5", timeout=3, suppress_origin=True)
        self.assertIn('location.origin !== "https://www.string.ink"', json.loads(socket.send.call_args.args[0])["params"]["expression"])
        socket.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
