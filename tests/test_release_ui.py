"""User-visible document editing, queue history and publication target guards."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from factory_video_tool.ui import Window

@unittest.skipUnless(os.environ.get('P009_UI_TEST')=='1','explicit UI run')
class ReleaseUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,w):
        self.app.processEvents()
        until=time.monotonic()+20
        while w.worker is not None and time.monotonic()<until:self.app.processEvents();time.sleep(.02)
        self.assertIsNone(w.worker)
    def test_local_document_edit_and_account_unavailable(self):
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            w=Window(d);w.show();self.addCleanup(lambda: w.close() if w.workspace.exists() else None);self.wait(w)
            self.assertTrue(hasattr(w,'release_panel'),'首版任务与发布界面尚未实现')
            p=Path(d)/'本地.txt';p.write_text('标题\n原口播\n第二段',encoding='utf-8')
            w.load_path(p);self.wait(w)
            self.assertEqual(w.contents[0]['voice_text'],'原口播\n第二段')
            w.save_edited_content(dict(title='新标题',voice_text='新口播\n保留段落',body='正文',tags=''))
            self.assertEqual(w.contents[0]['title'],'新标题')
            self.assertIn('保留段落',w.content_view.toPlainText())
            self.assertFalse(w.release_panel.publish_button.isEnabled())
            self.assertIn('未连接',w.release_panel.account_label.text())
            w.close();self.app.processEvents()
    def test_history_reopens_saved_result_without_regenerating(self):
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            w=Window(d);w.show();self.addCleanup(lambda: w.close() if w.workspace.exists() else None);self.wait(w)
            self.assertTrue(hasattr(w,'repository'),'任务仓储尚未接入界面')
            job=w.repository.create_job({'content':{'title':'历史成片','voice_text':'口播'}})
            file=Path('runtime/合成 测试资料/产品 测试画面/合成 色块.mp4').resolve()
            w.repository.set_job(job['attempt_id'],'SUCCEEDED',str(file.resolve()))
            w.release_panel.refresh();w.release_panel.open_job(job['attempt_id'])
            self.assertEqual(w.current_attempt_id,job['attempt_id'])
            self.assertEqual(w.result,str(file.resolve()))
            self.assertIsNone(w.worker)
            w.close();self.app.processEvents()
    def test_login_click_binds_returned_identity_without_token_dialog(self):
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            w=Window(d);w.show();self.wait(w)
            panel=w.release_panel
            self.assertEqual(panel.connect_button.text(),'登录抖音')
            calls=[]
            def login(cancel,progress):
                calls.append('official-browser-login')
                return panel.browser_session.binding.connected({'platform':'douyin','platform_user_id':'12345',
                    'display_name':'本地测试账号','name_observed':True,'provider_id':'douyin_browser'})
            panel.browser_session.login=login
            panel.connect_button.click();self.wait(w)
            self.assertEqual(calls,['official-browser-login'])
            self.assertIn('本地测试账号',panel.account_label.text())
            self.assertIn('已登录',panel.login_status.text())
            self.assertFalse(panel.cancel_login_button.isEnabled())
            self.assertFalse(panel.publish_button.isEnabled(),'Browser login must not imply verified automatic publishing')
            self.assertNotIn('credential_ref',w.repository.setting('fixed_account'))
            panel.browser_session.binding.state('EXPIRED','登录已失效，请重新登录')
            panel.refresh_account()
            self.assertEqual(panel.connect_button.text(),'登录抖音')
            w.close();self.app.processEvents()
