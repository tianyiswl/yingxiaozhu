"""Novice navigation and preservation of production/publication state."""
import os,time,tempfile,json
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from factory_video_tool.ui import Window

@unittest.skipUnless(os.environ.get('P009_UI_TEST')=='1','explicit UI run')
class StudioUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,w):
        end=time.monotonic()+30
        self.app.processEvents()
        while w.worker is not None and time.monotonic()<end:
            self.app.processEvents();time.sleep(.01)
        self.assertIsNone(w.worker)
    def test_first_use_and_step_guards(self):
        with tempfile.TemporaryDirectory() as d:
            w=Window(d);w.show();self.wait(w)
            try:
                self.assertEqual(w.pages.currentWidget(),w.home_page)
                self.assertEqual(w.home_start.text(),'制作第一条视频')
                w.home_start.click()
                self.assertEqual(w.wizard.currentIndex(),0)
                w.advance_step()
                self.assertEqual(w.wizard.currentIndex(),0)
                self.assertIn('素材',w.step_error.text())
                self.assertFalse(w.release_panel.publish_button.isEnabled())
            finally:w.close();self.app.processEvents()
    def test_draft_settings_restore_without_account(self):
        with tempfile.TemporaryDirectory() as d:
            w=Window(d);w.show();self.wait(w)
            w.accept_contents([dict(external_id='one',title='标题',voice_text='这是口播。',body='',tags='',enabled=True)])
            w.material.setText('/tmp/素材目录');w.rate.setCurrentText('1.2');w.resolution.setCurrentIndex(1)
            w.show_wizard(2);w.persist();w.close();self.app.processEvents()
            v=Window(d);v.show();self.wait(v)
            try:
                self.assertEqual(v.rate.currentText(),'1.2')
                self.assertEqual(v.resolution.currentIndex(),1)
                self.assertEqual(v.draft_step,2)
                self.assertEqual(v.material.text(),'/tmp/素材目录')
                self.assertEqual(v.contents[0]['voice_text'],'这是口播。')
                self.assertEqual(v.pages.currentWidget(),v.home_page)
                self.assertEqual(v.repository.publications(),[])
            finally:v.close();self.app.processEvents()
    def test_cancelled_publication_has_no_submit_and_plain_language(self):
        with tempfile.TemporaryDirectory() as d:
            w=Window(d);w.show();self.wait(w)
            try:
                j=w.repository.create_job({'content':{'title':'本地测试'}})
                r=w.repository.create_publication(j['attempt_id'],{'display_name':'测试','platform_user_id':'fixture'}, {'text':'本地测试'})
                w.repository.set_publication(r['publication_id'],'CANCELLED',{'submitted':False})
                w.release_panel.refresh()
                self.assertFalse(w.release_panel.publish_button.isEnabled())
                self.assertIn('未提交',w.release_panel.details.toPlainText())
                self.assertNotIn('UNKNOWN',w.release_panel.details.toPlainText())
                w.navigate('home');w.navigate('videos');w.navigate('publications')
                self.assertEqual(w.repository.get_publication(r['publication_id'])['status'],'CANCELLED')
            finally:w.close();self.app.processEvents()
    def test_new_result_replaces_selected_history_and_filter_clears_target(self):
        with tempfile.TemporaryDirectory() as d:
            w=Window(d);w.show();self.wait(w)
            try:
                file=Path('runtime/合成 测试资料/产品 测试画面/合成 色块.mp4').resolve()
                jobs=[]
                for title in ['旧视频','新视频']:
                    j=w.repository.create_job({'content':{'title':title}});w.repository.set_job(j['attempt_id'],'SUCCEEDED',str(file));jobs.append(j)
                w.release_panel.refresh();w.open_attempt(jobs[0]['attempt_id'])
                self.assertEqual(w.current_attempt_id,jobs[0]['attempt_id'])
                w.current_attempt_id=jobs[1]['attempt_id'];w.generated(str(file))
                self.assertEqual(w.current_attempt_id,jobs[1]['attempt_id'])
                self.assertEqual(w.video_title.text(),'新视频')
                w.video_filter.setCurrentIndex(3)
                self.assertIsNone(w.current_attempt_id)
                self.assertFalse(w.to_publish.isEnabled())
                self.assertFalse(w.export.isEnabled())
            finally:w.close();self.app.processEvents()
    def test_audition_keeps_video_and_publication_target(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            w=Window(d);w.show();self.wait(w)
            try:
                file=Path('runtime/合成 测试资料/产品 测试画面/合成 色块.mp4').resolve()
                j=w.repository.create_job({'content':{'title':'保留成片'}});w.repository.set_job(j['attempt_id'],'SUCCEEDED',str(file))
                w.release_panel.refresh();w.open_attempt(j['attempt_id']);original=w.player.source()
                with patch('factory_video_tool.ui.SystemSpeech.synthesize',return_value=SimpleNamespace(path=str(file))):
                    w.audition();self.wait(w)
                self.assertEqual(w.player.source(),original)
                self.assertEqual(w.current_attempt_id,j['attempt_id'])
                self.assertEqual(w.result,str(file))
                self.assertEqual(w.repository.publications(),[])
            finally:w.close();self.app.processEvents()
    def test_refresh_keeps_accessible_video_rows_alive(self):
        import shiboken6
        with tempfile.TemporaryDirectory() as d:
            w=Window(d);w.show();self.wait(w)
            try:
                file=Path('runtime/合成 测试资料/产品 测试画面/合成 色块.mp4').resolve()
                j=w.repository.create_job({'content':{'title':'稳定列表'}});w.repository.set_job(j['attempt_id'],'SUCCEEDED',str(file));w.release_panel.refresh();w.refresh_studio()
                recent=w.recent.item(0);row=w.video_list.item(0)
                w.open_attempt(j['attempt_id']);w.refresh_studio()
                self.assertTrue(shiboken6.isValid(recent),'点击后不应销毁正在被系统辅助功能引用的列表项')
                self.assertTrue(shiboken6.isValid(row))
                self.assertIs(w.recent.item(0),recent)
                w.export_result();self.assertTrue(w.confirm_save.isEnabled());w.hide_save_panel()
                w.video_filter.setCurrentIndex(3);w.video_filter.setCurrentIndex(0)
                self.assertTrue(shiboken6.isValid(row))
            finally:w.close();self.app.processEvents()
