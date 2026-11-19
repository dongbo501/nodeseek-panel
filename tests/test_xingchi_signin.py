import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from panel import token_signin as api


CFG = {"site": "xingchi", "access_token": "xc-access", "refresh_token": "xc-refresh", "user_agent": "fixture"}
READY = {"enabled": True, "claimed_today": False}
CLAIMED = {"enabled": True, "claimed_today": True}


def response(data):
    raw = io.BytesIO(json.dumps({"code": 0, "data": data}).encode())
    raw.status = 200
    return raw


class XingchiTests(unittest.TestCase):
    def test_claim_matches_dashboard_and_uses_only_xingchi_credentials(self):
        replies = [response(READY), response({"amount": 0.25, "status": CLAIMED})]
        with patch.object(api.OPENER, "open", side_effect=replies) as send:
            result, message = api.execute_check_in({**CFG, "cookie": "session=unrelated"})
        self.assertEqual(result, "ok")
        self.assertIn("+0.25", message)
        self.assertEqual([c.args[0].method for c in send.call_args_list], ["GET", "POST"])
        for call in send.call_args_list:
            req = call.args[0]
            self.assertEqual(req.full_url, "https://xingchiapi.com/api/v1/user/checkin")
            self.assertEqual(req.get_header("Authorization"), "Bearer xc-access")
            self.assertIsNone(req.get_header("Cookie"))
            self.assertIsNone(req.data)

    def test_no_claim_when_disabled_already_claimed_or_status_is_invalid(self):
        for state, outcome in [(CLAIMED, "already"), ({"enabled": False}, "fail"), ({}, "fail"),
                               ({"enabled": True, "claimed_today": "false"}, "fail")]:
            with self.subTest(state=state), patch.object(api, "request", return_value=state) as request:
                self.assertEqual(api.execute_check_in(CFG)[0], outcome)
                request.assert_called_once()

    def test_incomplete_receipt_is_confirmed_with_status(self):
        for state, outcome in [(CLAIMED, "ok"), (READY, "fail"), ({}, "fail")]:
            with self.subTest(state=state), patch.object(api, "request", side_effect=[READY, {}, state]) as request:
                self.assertEqual(api.execute_check_in(CFG)[0], outcome)
                self.assertEqual([c.args[2] for c in request.call_args_list], ["GET", "POST", "GET"])

    def test_duplicate_claim_is_confirmed(self):
        reason = io.BytesIO(b'{"reason":"CHECKIN_ALREADY_CLAIMED"}')
        duplicate = urllib.error.HTTPError("https://xingchiapi.com", 400, "duplicate", {}, reason)
        with patch.object(api.OPENER, "open", side_effect=[response(READY), duplicate, response(CLAIMED)]):
            self.assertEqual(api.execute_check_in(CFG)[0], "already")

    def test_refresh_uses_xingchi_origin_and_persists_only_rotated_tokens(self):
        expired = urllib.error.HTTPError("https://xingchiapi.com", 401, "expired", {}, io.BytesIO(b""))
        rotated = {"access_token": "xc-new", "refresh_token": "xc-rotated"}
        save = Mock(return_value=True)
        with patch.object(api.OPENER, "open", side_effect=[expired, response(rotated), response(CLAIMED)]) as send:
            self.assertEqual(api.execute_check_in(CFG, save_tokens=save)[0], "already")
        refresh = send.call_args_list[1].args[0]
        self.assertEqual(refresh.full_url, "https://xingchiapi.com/api/v1/auth/refresh")
        self.assertEqual(json.loads(refresh.data), {"refresh_token": "xc-refresh"})
        self.assertIsNone(refresh.get_header("Authorization"))
        save.assert_called_once_with(rotated, {key: CFG[key] for key in rotated})
