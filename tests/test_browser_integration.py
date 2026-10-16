"""完整镜像中的真实浏览器测试；不连接 NodeSeek，也不使用真实账号。"""
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.request
from urllib.parse import quote
from pathlib import Path
from unittest.mock import patch

import websocket
from panel.browser import BrowserManager
from panel.config import ConfigStore
from panel.service import Service


@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1', '仅在完整 Docker 镜像中运行')
class BrowserIntegrationTests(unittest.TestCase):
    def test_real_chromium_paste_cookie_capture_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigStore(directory)
            config.update({'enabled':False})
            service = Service(directory,config)
            browser = BrowserManager(config,service)
            original_popen = subprocess.Popen

            def launch(command, **kwargs):
                if command[0] == 'chromium':
                    # 用离线页面替代站点，测试永不提交真实签到或登录。
                    html = '<title>Offline login fixture</title><input id="account"><input id="password" type="password"><input id="readonly" readonly><button id="other">Other</button><script>window.inputEvents=0;document.addEventListener("input",()=>window.inputEvents++);</script>'
                    command = command[:-1] + ['--app=data:text/html,' + quote(html)]
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
                with urllib.request.urlopen('http://127.0.0.1:9222/json/list',timeout=3) as r:
                    page = next(p for p in json.load(r) if p['type']=='page')
                page_ws = websocket.create_connection(page['webSocketDebuggerUrl'],timeout=3,suppress_origin=True)
                def evaluate(expression):
                    return browser._command(page_ws,1,'Runtime.evaluate',{'expression':expression,'returnByValue':True})['result'].get('value')
                try:
                    browser._command(page_ws,2,'Page.bringToFront')
                    evaluate('document.getElementById("password").focus()')
                    password = 'QA-only-密码🔑 &<>"\'\\+%'
                    browser.paste_text(password)
                    self.assertEqual(evaluate('document.getElementById("password").value'),password)
                    self.assertEqual(evaluate('window.inputEvents'),1)
                    evaluate('document.getElementById("password").select()')
                    browser.paste_text('replacement')
                    self.assertEqual(evaluate('document.getElementById("password").value'),'replacement')
                    evaluate('document.getElementById("account").focus()')
                    browser.paste_text('user@example.com')
                    self.assertEqual(evaluate('document.getElementById("account").value'),'user@example.com')
                    for target in ('readonly','other'):
                        evaluate('document.getElementById(' + json.dumps(target) + ').focus()')
                        with self.assertRaisesRegex(ValueError,'请先在容器浏览器中点击'):
                            browser.paste_text('must-not-insert')
                    self.assertNotIn(password,config.path.read_text())
                    self.assertEqual(service.history(),[])
                finally:
                    page_ws.close()
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
