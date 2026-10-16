"""完整镜像中的真实浏览器测试；不连接 NodeSeek，也不使用真实账号。"""
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

import websocket
from panel.browser import BrowserManager
from panel.config import ConfigStore
from panel.service import Service


@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', '仅在完整 Docker 镜像中运行')
class BrowserIntegrationTests(unittest.TestCase):
    def test_real_chromium_cookie_capture_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigStore(directory)
            config.update({'enabled':False})
            service = Service(directory,config)
            browser = BrowserManager(config,service)
            original_popen = subprocess.Popen

            def launch(command, **kwargs):
                if command[0] == 'chromium':
                    # 用离线页面替代站点，测试永不提交真实签到或登录。
                    command = command[:-1] + ['--app=data:text/html,<title>Cookie import fixture</title>']
                return original_popen(command,**kwargs)

            try:
                with patch('panel.browser.subprocess.Popen',side_effect=launch):
                    browser.start()
                processes = list(browser.processes)
                profile = browser.profile
                deadline = time.monotonic()+20
                while not browser.status()['ready'] and time.monotonic()<deadline:
                    time.sleep(.2)
                self.assertTrue(browser.status()['ready'])
                self.assertFalse(config.get()['cookie'])
                with urllib.request.urlopen('http://127.0.0.1:9222/json/version',timeout=3) as r:
                    info=json.load(r)
                ws=websocket.create_connection(info['webSocketDebuggerUrl'],timeout=3,suppress_origin=True)
                try:
                    ws.send(json.dumps({'id':1,'method':'Storage.setCookies','params':{'cookies':[
                        {'name':'session','value':'integration-fixture-session','domain':'.nodeseek.com','path':'/','secure':True,'httpOnly':True},
                        {'name':'cf_clearance','value':'integration-fixture-clearance','domain':'.nodeseek.com','path':'/','secure':True},
                        {'name':'private-other-site','value':'must-not-import','domain':'.example.com','path':'/'}
                    ]}}))
                    self.assertNotIn('error',json.loads(ws.recv()))
                finally:
                    ws.close()
                deadline=time.monotonic()+20
                while not browser.status()['imported_at'] and time.monotonic()<deadline:
                    time.sleep(.2)
                self.assertTrue(browser.status()['imported_at'])
                self.assertEqual(config.get()['cookie'],'session=integration-fixture-session; cf_clearance=integration-fixture-clearance')
                self.assertIn('Chrome/',config.get()['user_agent'])
                self.assertFalse(browser.status()['running'])
                self.assertFalse(Path(profile).exists())
                self.assertTrue(all(p.poll() is not None for p in processes))
            finally:
                browser.close()
                service.shutdown()
                service.db.close()
