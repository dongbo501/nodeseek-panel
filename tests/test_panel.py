import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from nodeseek_signin import check_in, execute_check_in, safe_message, TransientError
from panel.browser import BrowserManager, extract_cookie
from panel.config import ConfigStore
from panel.service import Service, next_run
from panel.web import create_app


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"ADMIN_PASSWORD": "test-only-strong-password"}, clear=False)
        self.env.start()
        self.cookie_env = patch.dict(os.environ)
        self.cookie_env.start()
        os.environ.pop("NODESEEK_COOKIE", None)
        self.app = create_app(self.temp.name, start_scheduler=False, runner=lambda cfg, **kw: ("ok", "签到成功"))
        self.app.testing = True
        self.client = self.app.test_client()
        self.store = self.app.extensions["config_store"]
        self.service = self.app.extensions["signin_service"]
        self.csrf = self.client.get('/api/session').json['csrf']

    def tearDown(self):
        self.service.shutdown()
        self.service.db.close()
        self.cookie_env.stop()
        self.env.stop()
        self.temp.cleanup()

    def request(self, path, data=None, method='POST'):
        return self.client.open('/api/' + path, method=method, json=data or {}, headers={'X-CSRF-Token': self.csrf})

    def login(self):
        response = self.request('login', {'password': 'test-only-strong-password'})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json['csrf']

    def test_protected_routes(self):
        for path in ('/api/config','/api/status','/api/history','/api/browser','/browser/view','/browser/core/rfb.js'):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_csrf_and_json_required(self):
        self.assertEqual(self.client.post('/api/login', json={'password': 'x'}).status_code,403)
        self.assertEqual(self.client.post('/api/login',data='x',headers={'X-CSRF-Token':self.csrf}).status_code,415)

    def test_login_rotates_csrf_and_logout(self):
        old = self.csrf
        self.login()
        self.assertNotEqual(old, self.csrf)
        self.assertEqual(self.client.get('/api/config').status_code,200)
        self.assertEqual(self.request('logout').status_code,200)
        self.assertEqual(self.client.get('/api/config').status_code,401)

    def test_login_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.request('login',{'password':'wrong'}).status_code,401)
        self.assertEqual(self.request('login',{'password':'wrong'}).status_code,429)

    def test_secrets_never_returned_and_empty_preserves(self):
        self.login()
        secret = 'session=private-cookie-secret'
        response = self.request('config',{'cookie':secret,'bark_url':'https://api.day.app/private-bark-key'},'PUT')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('private-',response.get_data(as_text=True))
        self.request('config',{'cookie':'','bark_url':''},'PUT')
        self.assertEqual(self.store.get()['cookie'],secret)
        self.request('config',{'clear_cookie':True,'clear_bark_url':True},'PUT')
        self.assertFalse(self.store.get()['cookie'])
        self.assertFalse(self.store.get()['bark_url'])

    def test_validation_is_atomic(self):
        self.login()
        for change in ({'run_at':'25:00'},{'jitter_minutes':-1},{'enabled':'false'},{'timezone':'bad/timezone'},{'cookie':'a\nb'},{'bark_url':'http://unsafe.example/key'},{'unknown':True},{'user_agent':''}):
            self.assertEqual(self.request('config',change,'PUT').status_code,400,change)
        self.assertEqual(self.store.get()['run_at'],'08:00')

    def test_environment_cookie_wins(self):
        with patch.dict(os.environ,{'NODESEEK_COOKIE':'session=environment-cookie'}):
            self.assertEqual(self.store.get()['cookie'],'session=environment-cookie')
            self.assertTrue(self.store.public()['cookie_from_env'])
            self.login()
            self.assertEqual(self.request('browser/start').status_code,400)

    def test_manual_missing_cookie(self):
        self.login()
        self.assertEqual(self.request('run').status_code,400)

    def test_execution_and_redaction(self):
        self.store.update({'cookie':'session=very-private-token'})
        self.service.runner = lambda cfg, **kw: ('ok', 'echo very-private-token')
        self.assertTrue(self.service.trigger(synchronous=True))
        row = self.service.history()[0]
        self.assertEqual(row['status'],'ok')
        self.assertNotIn('very-private-token',row['message'])
        self.assertFalse(self.service.busy)

    def test_duplicate_run_rejected(self):
        self.store.update({'cookie':'session=secret'})
        self.service.busy = True
        self.assertFalse(self.service.trigger())

    def test_schedule_persists_after_restart(self):
        self.store.update({'cookie':'session=secret'})
        self.service.refresh_schedule()
        target = self.service.status()['next_run']
        second = Service(self.temp.name,self.store)
        self.assertEqual(second.status()['next_run'], target)
        second.db.close()

    def test_disabled_schedule_has_no_target(self):
        self.store.update({'cookie':'session=secret','enabled':False})
        self.service.refresh_schedule()
        self.assertIsNone(self.service.status()['next_run'])

    def test_stale_schedule_does_not_run(self):
        self.store.update({'cookie':'session=secret'})
        now = datetime.now(timezone.utc)
        with self.service.lock:
            self.service.set_state('next_run',(now-timedelta(days=2)).isoformat())
            self.service.db.commit()
        with patch.object(self.service,'trigger') as trigger:
            self.service.tick(now)
            trigger.assert_not_called()
        self.assertGreater(datetime.fromisoformat(self.service.status()['next_run']),now)

    def test_due_schedule_advances_before_trigger(self):
        self.store.update({'cookie':'session=secret','timezone':'UTC'})
        now = datetime(2026,10,16,9,tzinfo=timezone.utc)
        self.service.set_state('next_run',(now-timedelta(minutes=1)).isoformat())
        self.service.db.commit()
        with patch.object(self.service,'trigger') as trigger:
            self.service.tick(now)
            self.service.tick(now)
            trigger.assert_called_once_with('scheduled')

    def test_browser_origin_rejected(self):
        self.login()
        self.assertEqual(self.client.get('/browser/ws',headers={'Origin':'https://attacker.example'}).status_code,403)

    def test_browser_capture_saves_cookie_and_ua(self):
        browser = self.app.extensions['browser_manager']
        with patch.object(browser,'status',return_value={'running':True}), patch.object(browser,'_cdp',return_value=('session=captured','Browser UA')), patch.object(browser,'_cleanup') as cleanup:
            browser.capture()
            self.assertEqual(self.store.get()['cookie'],'session=captured')
            self.assertEqual(self.store.get()['user_agent'],'Browser UA')
            cleanup.assert_called_once()

    def test_security_headers(self):
        r=self.client.get('/')
        self.assertEqual(r.headers['Cache-Control'],'no-store')
        self.assertIn("script-src 'self'",r.headers['Content-Security-Policy'])


class CoreTests(unittest.TestCase):
    def test_timezone_and_jitter(self):
        cfg={'timezone':'Asia/Shanghai','run_at':'08:00','jitter_minutes':10}
        class Rng:
            @staticmethod
            def randint(a,b): return b
        target=next_run(cfg,datetime(2026,10,16,23,tzinfo=timezone.utc),Rng)
        self.assertEqual(target.isoformat(),'2026-10-17T08:10:00+08:00')

    def test_browser_cookie_domain_filter(self):
        cookies=[{'name':'session','value':'ok','domain':'.nodeseek.com'}, {'name':'cf_clearance','value':'clear','domain':'www.nodeseek.com'}, {'name':'private','value':'bad','domain':'evilnodeseek.com'}]
        self.assertEqual(extract_cookie(cookies),'session=ok; cf_clearance=clear')
        self.assertIsNone(extract_cookie(cookies[1:]))

    def test_redacts_full_and_partial_secrets(self):
        cfg={'cookie':'session=privatevalue; cf_clearance=othersecret','bark_url':'https://api.day.app/barksecret'}
        text=safe_message('privatevalue othersecret https://api.day.app/barksecret',cfg)
        self.assertNotIn('secret',text)
        self.assertNotIn('privatevalue',text)

    def test_http_500_already_checked_in(self):
        cfg={'random':True,'cookie':'session=secret','user_agent':'test'}
        err=urllib.error.HTTPError('https://www.nodeseek.com/api/attendance',500,'error',{},io.BytesIO(json.dumps({'message':'今天已经签到过了'}).encode()))
        with patch('nodeseek_signin.OPENER.open',side_effect=err):
            self.assertEqual(check_in(cfg)[0],'already')

    def test_cloudflare_html_is_actionable(self):
        cfg={'random':True,'cookie':'session=secret','user_agent':'test'}
        err=urllib.error.HTTPError('https://www.nodeseek.com/api/attendance',403,'error',{},io.BytesIO(b'<html>challenge</html>'))
        with patch('nodeseek_signin.OPENER.open',side_effect=err):
            status,message=check_in(cfg)
            self.assertEqual(status,'fail')
            self.assertIn('Cloudflare',message)

    def test_retry_and_cancel(self):
        with patch('nodeseek_signin.check_in',side_effect=TransientError('test')) as check:
            stop=threading.Event();stop.set()
            status,message=execute_check_in({'cookie':''},stop)
            self.assertEqual(status,'fail')
            self.assertEqual(check.call_count,1)


if __name__ == '__main__':
    unittest.main()
