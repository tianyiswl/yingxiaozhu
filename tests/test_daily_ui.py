import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from PySide6.QtWidgets import QApplication,QPushButton,QFrame
from PySide6.QtCore import QLocale
from factory_video_tool.daily_ui import DailyWindow,duration_text
from factory_video_tool.library import Library
from factory_video_tool.release_panels import ContentEditor

@unittest.skipUnless(os.environ.get('P009_UI_TEST')=='1','explicit UI run')
class DailyUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_complete_exit_waits_for_worker_lock(self):
        from PySide6.QtGui import QCloseEvent
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                w.auto_worker=True
                with patch.object(w,'worker_is_locked',return_value=True):
                    event=QCloseEvent();w.closeEvent(event)
                    self.assertFalse(event.isAccepted())
                    self.assertEqual([c['kind'] for c in w.store.commands()],['STOP'])
                    w.finish_shutdown();self.assertFalse(getattr(w,'_exit_ready',False))
                    w.closeEvent(QCloseEvent());self.assertEqual(len(w.store.commands()),1)
                with patch.object(w,'worker_is_locked',return_value=False):w.finish_shutdown()
                self.assertTrue(w._exit_ready)
            finally:
                w.auto_worker=False;w.close();self.app.processEvents()

    def test_material_name_sort_uses_numeric_order(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                records=[dict(id=str(n),name=name,duration=1,created_at=n,path='/test/'+name) for n,name in enumerate(['素材10.mp4','素材2.mp4','素材1.mp4'])]
                for mode,expected in [(3,['素材1.mp4','素材2.mp4','素材10.mp4']),(4,['素材10.mp4','素材2.mp4','素材1.mp4'])]:
                    w.material_sort.setCurrentIndex(mode);w.render_material_table(records)
                    self.assertEqual([w.material_table.item(n,0).text() for n in range(3)],expected)
            finally:w.close();self.app.processEvents()

    def test_bulk_selection_survives_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                lib=Library(w.store)
                for n in range(3):lib.add_content({'title':'标题'+str(n),'voice_text':'文案'+str(n),'body':'','tags':''})
                w.refresh();w.content_table.selectAll()
                self.assertEqual(len(w.content_table.selectionModel().selectedRows()),3)
                lib.add_content({'title':'新标题','voice_text':'新文案','body':'','tags':''});w.refresh()
                self.assertEqual(len(w.content_table.selectionModel().selectedRows()),3)
                w.content_table.clearSelection()
                self.assertEqual(len(w.content_table.selectionModel().selectedRows()),0)
                for title in ('全选','全不选','删除选中'):
                    self.assertEqual(sum(b.text()==title for b in w.findChildren(QPushButton)),4)
            finally:w.close();self.app.processEvents()

    def test_prepare_once_defaults_and_no_publish_on_open(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False);w.show();self.app.processEvents()
            self.assertFalse(w.start_button.isEnabled());self.assertIn('本地模拟',w.windowTitle())
            w.repository.save_setting('daily_form',{'count':3,'interval':45,'time':'18:00'})
            for key in w.targets:w.navigate(key);self.app.processEvents()
            w.close();self.app.processEvents()
            v=DailyWindow(d,simulation=True,start_worker=False)
            self.assertEqual(v.count.value(),3);self.assertEqual(v.days.value(),1);self.assertEqual(v.interval_total_minutes(),180)
            self.assertEqual(v.repository.publications(),[]);self.assertEqual(v.store.commands(),[])
            v.close();self.app.processEvents()

    def test_new_workbench_defaults_to_immediate_test_submission(self):
        fixed=datetime(2026,9,10,14,30,0,tzinfo=ZoneInfo('Asia/Shanghai'))
        with tempfile.TemporaryDirectory() as d:
            with patch('factory_video_tool.daily_ui.datetime') as clock:
                clock.now.return_value=fixed
                w=DailyWindow(d,simulation=True,start_worker=False)
                try:
                    self.assertTrue(w.immediate_toggle.isChecked())
                    self.assertEqual(w.schedule_stack.currentIndex(),0)
                    self.assertIn('立即提交（制作完成后）',w.summary.text())
                finally:w.close();self.app.processEvents()

    def test_scheduled_time_stays_in_the_publish_method_row(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                self.assertEqual(w.schedule_stack.currentIndex(),0)
                self.assertEqual(w.schedule_date.displayFormat(),'MM月dd日')
                self.assertEqual(w.schedule_time.displayFormat(),'HH:mm')
                self.assertEqual(w.schedule_slot.width(),230)
                self.assertEqual(w.immediate_toggle.text(),'即时发布')
                self.assertEqual(w.scheduled_toggle.text(),'定时发布')
                self.assertEqual(w.schedule_placeholder.text(),'选择定时后设置发布时间')
                self.assertFalse(hasattr(w,'schedule_label'))
                w.scheduled_toggle.setChecked(True);self.app.processEvents()
                self.assertEqual(w.schedule_stack.currentIndex(),1)
                self.assertEqual(w.schedule_date.calendarWidget().locale().language(),QLocale.Language.Chinese)
                self.assertGreaterEqual((w.scheduled_datetime()-datetime.now(ZoneInfo('Asia/Shanghai'))).total_seconds(),2.9*3600)
            finally:w.close();self.app.processEvents()

    def test_interval_uses_numeric_hours_and_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                self.assertEqual(w.interval_total_minutes(),180)
                self.assertEqual(w.interval_hours.suffix(),'')
                self.assertEqual(w.interval_minutes.suffix(),'')
                w.interval_hours.setValue(1);w.interval_minutes.setValue(45)
                self.assertEqual(w.spec()['interval'],105)
            finally:w.close();self.app.processEvents()

    def test_home_login_is_direct_and_checking_is_visible(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,start_worker=False)
            try:
                self.assertEqual(w.account_button.text(),'登录抖音')
                w.account_button.click()
                self.assertEqual(w.store.commands()[0]['kind'],'LOGIN')
                w.repository.save_setting('douyin_session',{'state':'CHECKING'})
                w.refresh()
                self.assertFalse(w.account_button.isEnabled())
                self.assertIn('检查',w.account_button.text())
                self.assertEqual(w.repository.publications(),[])
            finally:w.close()

    def test_settings_has_separate_login_backend_and_save_actions(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                self.assertEqual(w.login_button.text(),'登录')
                self.assertEqual(w.switch_account_button.text(),'切换账号')
                self.assertEqual(w.open_backend_button.text(),'打开后台')
                self.assertFalse(w.switch_account_button.isEnabled())
                self.assertEqual(w.restore_settings_button.text(),'恢复默认设置')
                self.assertEqual(w.save_settings_button.text(),'保存设置')
                self.assertFalse(hasattr(w,'worker_status'))
                self.assertIsNotNone(w.findChild(QFrame,'feedbackCard'))
            finally:w.close();self.app.processEvents()

    def test_tasks_page_is_one_list_without_an_old_video_history(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                w.navigate('tasks')
                self.assertEqual(w.task_table.rowCount(),0)
                self.assertEqual([w.task_table.horizontalHeaderItem(i).text() for i in range(w.task_table.columnCount())],['发布时间','标题','状态','账号','平台','批次','处理时间'])
                self.assertEqual(w.task_filter.currentText(),'全部任务')
                self.assertEqual(w.task_filter.minimumWidth(),172)
                self.assertEqual(w.task_total_metric.value_label.text(),'0')
                self.assertFalse(hasattr(w,'history'))
            finally:w.close();self.app.processEvents()

    def test_task_row_menu_has_play_folder_and_detail_actions(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                menu=w.task_menu({'id':'batch-1','status':'ACTIVE'},{'id':'task-1','state':'UNKNOWN','result':None})
                self.assertEqual([action.text() for action in menu.actions()],['播放成片','打开所在文件夹','查看详细信息','核对提交结果','暂停本批任务','取消本批未开始任务'])
                self.assertFalse(menu.actions()[0].isEnabled())
                self.assertFalse(menu.actions()[1].isEnabled())
            finally:w.close();self.app.processEvents()

    def test_task_detail_includes_title_copy_and_tags(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                batch={'created_at':0,'status':'COMPLETED','account':{'display_name':'测试账号'},'mode':'simulate'}
                task={'due_at':0,'submitted_at':None,'state':'SIMULATED','error':'','result':None,'request':{'content':{'title':'产品标题','voice_text':'这是完整口播文案。','body':'发布正文。','tags':'#工厂 #产品'}}}
                detail=w.task_detail_text(batch,task)
                self.assertIn('标题：产品标题',detail)
                self.assertIn('文案：\n这是完整口播文案。',detail)
                self.assertIn('标签：\n#工厂 #产品',detail)
            finally:w.close();self.app.processEvents()

    def test_started_notice_clears_after_its_batch_is_no_longer_active(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                w.started_batch_id='batch-1';w.issue.setText('已开始：共 4 条，后台会自动制作并按间隔处理。')
                w.clear_finished_start_notice([{'id':'batch-1','status':'COMPLETED'}])
                self.assertEqual(w.issue.text(),'')
                self.assertIsNone(w.started_batch_id)
            finally:w.close();self.app.processEvents()

    def test_saving_settings_shows_a_temporary_success_message(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                w.voice.addItem('Tingting');w.save_defaults()
                self.assertEqual(w.settings_message.text(),'设置已保存')
                self.assertIn('#168467',w.settings_message.styleSheet())
            finally:w.close();self.app.processEvents()

    def test_material_library_has_searchable_media_table_and_actions(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                with w.repository.connect() as db:
                    db.execute("INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)",('material-1','/tmp/切割视频.mp4','切割视频.mp4',65,100))
                    db.execute("INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)",('material-2','/tmp/展示视频.mp4','展示视频.mp4',12,200))
                w.refresh();w.navigate('materials')
                self.assertEqual(w.material_table.rowCount(),2)
                self.assertEqual(w.material_table.rowHeight(0),38)
                self.assertEqual(w.material_total_metric.parent().height(),70)
                self.assertEqual(w.material_total_metric.value_label.text(),'0')
                self.assertIn('文件失效',w.material_table.item(0,3).text())
                self.assertEqual(w.material_duration_metric.value_label.text(),'1分17秒')
                self.assertEqual(duration_text(12),'12 秒')
                w.material_search.setText('切割');self.app.processEvents()
                self.assertEqual(w.material_table.rowCount(),1)
                menu=w.material_menu(w.table_material(0))
                self.assertEqual([action.text() for action in menu.actions()],['预览素材','打开所在文件夹','重新定位原文件','删除素材'])
            finally:w.close();self.app.processEvents()

    def test_bgm_library_sets_a_default_background_track(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                with w.repository.connect() as db:
                    db.execute("INSERT INTO library_bgm(id,path,name,duration,created_at) VALUES(?,?,?,?,?)",('music-1','/tmp/车间节奏.mp3','车间节奏.mp3',75,100))
                w.refresh();w.navigate('bgm')
                self.assertEqual(w.bgm_table.rowCount(),1)
                self.assertEqual(list(w.nav)[2:4],['contents','bgm'])
                self.assertEqual(w.bgm_total_metric.value_label.text(),'1')
                self.assertEqual([action.text() for action in w.bgm_menu(w.table_bgm(0)).actions()],['试听音乐','打开所在文件夹','设为默认背景音乐','停止使用此音乐','重新定位原文件','删除背景音乐'])
                w.set_default_bgm(w.table_bgm(0))
                self.assertEqual(w.repository.setting('daily_defaults')['bgm'],'/tmp/车间节奏.mp3')
                self.assertEqual(w.bgm_choice.currentData(),'/tmp/车间节奏.mp3')
                self.assertTrue(w.bgm_enabled.isChecked())
                self.assertTrue(w.bgm_choice.isEnabled())
                self.assertEqual(w.bgm_mode.currentData(),'specified')
            finally:w.close();self.app.processEvents()

    def test_settings_use_clear_choice_labels_and_clickable_font_picker(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                self.assertEqual([w.resolution.itemData(index) for index in range(w.resolution.count())],[720,1080])
                self.assertIn('标准清晰',w.resolution.itemText(0))
                self.assertIn('更清晰',w.resolution.itemText(1))
                self.assertEqual([w.bgm_mode.itemData(index) for index in range(w.bgm_mode.count())],['random','sequence','specified'])
                self.assertIn('每条自动抽取',w.bgm_mode.itemText(0))
                self.assertIn('轮流切换',w.bgm_mode.itemText(1))
                self.assertIn('同一首',w.bgm_mode.itemText(2))
                self.assertEqual(w.bgm_choice.itemText(0),'请选择背景音乐')
                self.assertFalse(w.subtitle_font.isEditable())
                self.assertIn('点击任意位置',w.subtitle_font.toolTip())
                self.assertEqual(w.subtitle_max_chars.value(),14)
                self.assertEqual(w.subtitle_max_chars.suffix(),' 个字')
                self.assertTrue(w.avoid_recent.isChecked())
                self.assertFalse(w.bgm_mode.isEnabled())
                self.assertFalse(w.bgm_choice.isEnabled())
                self.assertFalse(w.bgm_volume.isEnabled())
                self.assertFalse(w.bgm_mode_label.isEnabled())
                self.assertFalse(w.bgm_choice_label.isEnabled())
                self.assertFalse(w.bgm_volume_label.isEnabled())
                w.bgm_enabled.setChecked(True);self.assertEqual(w.bgm_mode.currentData(),'random');w.bgm_mode.setCurrentIndex(w.bgm_mode.findData('specified'))
                self.assertTrue(w.bgm_mode.isEnabled())
                self.assertTrue(w.bgm_choice.isEnabled())
                self.assertTrue(w.bgm_volume.isEnabled())
            finally:w.close();self.app.processEvents()

    def test_material_import_placeholder_uses_a_video_path(self):
        self.assertEqual(DailyWindow.import_path_placeholder('materials',False),'/Users/你的用户名/Documents/工厂素材/车间镜头.mp4')
        self.assertEqual(DailyWindow.import_path_placeholder('materials',True),'/Users/你的用户名/Documents/工厂素材')

    def test_content_library_has_searchable_table_and_status_actions(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                library=Library(w.store);used,_=library.add_content({'title':'已用标题','voice_text':'这是已经使用的文案。','body':'','tags':'工厂'})
                available,_=library.add_content({'title':'未用标题','voice_text':'这是可以继续使用的文案。','body':'','tags':'产品'})
                with w.repository.connect() as db:db.execute('UPDATE library_contents SET used=1 WHERE id=?',(used,))
                w.refresh();w.navigate('contents')
                self.assertEqual(w.content_table.rowCount(),2)
                self.assertEqual(w.content_selection.currentData(),'sequence')
                self.assertEqual(w.content_selection.currentText(),'按导入顺序使用')
                w.content_selection.setCurrentIndex(w.content_selection.findData('random'))
                self.assertEqual(w.repository.setting('content_selection_mode'),'random')
                self.assertEqual(w.content_total_metric.value_label.text(),'2')
                self.assertEqual(w.content_available_metric.value_label.text(),'1')
                w.content_search.setText('未用');self.app.processEvents()
                self.assertEqual(w.content_table.rowCount(),1)
                self.assertEqual(w.table_content(0)['id'],available)
                self.assertEqual([action.text() for action in w.content_menu(w.table_content(0)).actions()],['查看 / 修改文案','停用文案','删除文案'])
            finally:w.close();self.app.processEvents()

    def test_template_download_dialog_has_file_and_folder_actions(self):
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                dialog,open_file,open_folder=w.content_template_dialog(Path(d)/'文案模板.xlsx')
                self.assertEqual(dialog.windowTitle(),'文案模板已下载')
                self.assertEqual(open_file.text(),'打开模板')
                self.assertEqual(open_folder.text(),'打开所在文件夹')
            finally:w.close();self.app.processEvents()

    def test_content_editor_is_wide_and_uses_chinese_actions(self):
        editor=ContentEditor({'title':'标题','voice_text':'口播','body':'正文','tags':'工厂'})
        try:
            self.assertGreaterEqual(editor.minimumWidth(),700)
            self.assertEqual(editor.values(),{'title':'标题','voice_text':'口播','body':'正文','tags':'工厂'})
            self.assertEqual([button.text() for button in editor.findChildren(QPushButton)],['取消','保存文案'])
        finally:editor.close();self.app.processEvents()
