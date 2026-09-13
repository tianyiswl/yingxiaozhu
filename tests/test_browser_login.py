"""Account identity must come from the current official browser session."""
import tempfile
import threading
import unittest
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from factory_video_tool.browser_login import parse_identity, LoginBinding, DouyinBrowserSession
from factory_video_tool.repository import Repository
from factory_video_tool.core import Cancelled


class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(self.temp.name)
        self.binding = LoginBinding(self.repo)

    def test_official_identity_is_minimal_and_preserves_large_id(self):
        account = parse_identity('https://creator.douyin.com/aweme/v1/creator/user/info/', {
            'status_code': 0, 'uid': 9876543210123456789,
            'user_profile': {'nick_name': '测试工厂', 'unique_id': 'factory_demo'},
            'private_data': 'must not be saved'})
        self.assertEqual(account['platform_user_id'], '9876543210123456789')
        self.assertEqual(account['display_name'], '测试工厂')
        self.assertNotIn('private_data', account)

    def test_signed_out_unrelated_origin_and_nickname_only_are_not_identity(self):
        url = 'https://creator.douyin.com/aweme/v1/creator/user/info/'
        for source, body in [
            (url, {'status_code': 8, 'uid': '123', 'nickname': '旧昵称'}),
            (url, {'status_code': 0, 'nickname': '仅昵称'}),
            (url.replace('creator.douyin.com', 'example.com'), {'status_code': 0, 'uid': '123'}),
            ('https://creator.douyin.com/web/api/media/top/users', {'status_code': 0, 'uid': '123'}),
            (url, {'uid': '123'}),
        ]:
            self.assertIsNone(parse_identity(source, body))

    def test_binding_first_login_automatically_and_refuses_account_change(self):
        first = {'platform': 'douyin', 'platform_user_id': '123', 'display_name': '测试甲', 'provider_id': 'douyin_browser'}
        self.binding.connected(first)
        self.assertTrue(self.repo.setting('fixed_account')['verified'])
        with self.assertRaisesRegex(ValueError, '固定账号'):
            self.binding.connected(dict(first, platform_user_id='456', display_name='测试乙'))
        self.assertEqual(self.repo.setting('fixed_account')['platform_user_id'], '123')
        self.assertFalse(self.repo.setting('fixed_account')['verified'])

    def test_restart_keeps_the_saved_profile_but_expiry_clears_it(self):
        self.binding.connected({'platform': 'douyin', 'platform_user_id': '123', 'display_name': '测试', 'provider_id': 'douyin_browser'})
        LoginBinding(self.repo)
        self.assertTrue(self.repo.setting('fixed_account')['verified'])
        self.assertEqual(self.repo.setting('douyin_session')['state'], 'SAVED')
        self.binding.state('EXPIRED', '请重新登录')
        self.assertEqual(self.repo.setting('fixed_account')['platform_user_id'], '123')
        self.assertFalse(self.repo.setting('fixed_account')['verified'])

    def test_switch_account_forgets_only_the_app_owned_profile_before_login(self):
        self.binding.connected({'platform': 'douyin', 'platform_user_id': '123', 'display_name': '旧账号', 'provider_id': 'douyin_browser'})
        session=DouyinBrowserSession(self.repo)
        session.profile.mkdir(parents=True)
        (session.profile/'session-state').write_text('app profile only')
        observed={}
        def fake_login(cancel, progress, timeout, allow_wait):
            observed['fixed_account']=self.repo.setting('fixed_account', {})
            observed['allow_wait']=allow_wait
            return {'platform_user_id': '456'}
        session._login=fake_login
        result=session._switch_account(threading.Event(), lambda _: None, 5)
        self.assertEqual(result['platform_user_id'], '456')
        self.assertEqual(observed['fixed_account'], {})
        self.assertTrue(observed['allow_wait'])
        self.assertFalse(session.profile.exists())


@unittest.skipUnless(os.environ.get('P009_BROWSER_TEST') == '1', 'explicit real Chrome fixture run')
class BrowserSessionIntegrationTests(unittest.TestCase):
    """Real Chrome/thread/profile lifecycle; all web traffic is a local test fixture."""
    def setUp(self):
        from factory_video_tool.browser_login import DouyinBrowserSession
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(self.temp.name)
        self.payload = {'status_code': 8}
        self.cookie_seen = False
        owner = self

        class FixtureSession(DouyinBrowserSession):
            def _ensure_browser(self):
                if self._page is not None and not self._page.is_closed():
                    return
                from playwright.sync_api import sync_playwright
                self._dispose()
                self._playwright = sync_playwright().start()
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self.profile), channel='chrome', headless=True, args=['--restore-last-session'])
                def route(r):
                    if r.request.url.endswith('/aweme/v1/creator/user/info/'):
                        r.fulfill(json=owner.payload)
                    elif r.request.is_navigation_request():
                        owner.cookie_seen = 'p009_fixture=session-only' in r.request.headers.get('cookie', '')
                        r.fulfill(content_type='text/html', body='''<!doctype html><title>P009 fixture</title>
                            <p>Local fixture. No real Douyin account.</p><script>
                            document.cookie='p009_fixture=session-only; Secure; SameSite=Lax; path=/';
                            fetch('/aweme/v1/creator/user/info/');
                            </script>''')
                    else:
                        r.fulfill(body='')
                self._context.route('**/*', route)
                self._page = self._context.pages[0]
                self._page.on('response', self._response)
        self.session_class = FixtureSession
        self.session = FixtureSession(self.repo)
        self.addCleanup(self.session.close)

    def test_same_session_is_used_across_workers_and_expiry_blocks(self):
        self.payload = {'status_code': 0, 'uid': '12345', 'user_profile': {'nick_name': '本地模拟账号'}}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.session.login, threading.Event(), lambda _: None, 5).result(timeout=20)
            self.assertIsNone(self.session._context, '登录完成后应关闭专用浏览器')
            self.assertTrue(self.repo.setting('fixed_account')['verified'])
            second = pool.submit(self.session.identity, threading.Event()).result(timeout=20)
        self.assertEqual(first['platform_user_id'], second['platform_user_id'])
        self.payload = {'status_code': 8}
        with self.assertRaisesRegex(ValueError, '失效'):
            self.session.identity(threading.Event())
        self.assertFalse(self.repo.setting('fixed_account')['verified'])

    def test_cancel_waiting_login_then_restart_native_profile(self):
        cancel = threading.Event()
        with ThreadPoolExecutor() as pool:
            result = pool.submit(self.session.login, cancel, lambda _: None, 20)
            deadline = time.monotonic() + 15
            while self.repo.setting('douyin_session', {}).get('state') != 'WAITING_LOGIN' and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(self.repo.setting('douyin_session')['state'], 'WAITING_LOGIN')
            cancel.set()
            with self.assertRaises(Cancelled):
                result.result(timeout=5)
        self.assertFalse(self.repo.setting('fixed_account', {}).get('verified'))
        self.session.close()
        self.assertFalse(self.session._thread.is_alive())
        self.payload = {'status_code': 0, 'uid': '12345', 'nickname': '本地模拟'}
        restarted = self.session_class(self.repo)
        self.addCleanup(restarted.close)
        restarted.login(threading.Event(), lambda _: None, 5)
        self.assertTrue(self.cookie_seen, 'Native profile must restore the local fixture session cookie')
        self.assertNotIn('p009_fixture', self.repo.path.read_bytes().decode(errors='ignore'))


    def test_restart_restores_saved_session_without_leaving_browser_open(self):
        self.payload={'status_code':0,'uid':'12345','nickname':'本地模拟'}
        self.session.login(threading.Event(),lambda _:None,5)
        self.session.close()
        restarted=self.session_class(self.repo);self.addCleanup(restarted.close)
        result=restarted.restore(threading.Event())
        self.assertEqual(result['platform_user_id'],'12345')
        self.assertTrue(self.cookie_seen)
        self.assertTrue(self.repo.setting('fixed_account')['verified'])
        self.assertIsNone(restarted._context)

    def test_restore_expired_session_does_not_mark_connected(self):
        self.payload={'status_code':8}
        with self.assertRaisesRegex(ValueError,'失效'):self.session.restore(threading.Event())
        self.assertFalse(self.repo.setting('fixed_account',{}).get('verified',False))
