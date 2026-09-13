import unittest,tempfile,os,threading
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from factory_video_tool.generation import draw_subtitles

class SubtitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_requested_size_color_and_no_clipping(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            draw_subtitles([(0,2,'工厂字幕字体大小颜色验证')],p,720,'PingFang SC',font_size=72,font_color='#FF0000')
            im=QImage(str(p/'subtitle-0000.png'))
            reds=0;edge=False
            for y in range(im.height()):
                for x in range(im.width()):
                    c=im.pixelColor(x,y)
                    if c.alpha()>200 and c.red()>200 and c.green()<60:reds+=1
                    if x in (0,719) and c.alpha()>0:edge=True
            self.assertGreater(reds,500);self.assertFalse(edge)

@unittest.skipUnless(os.environ.get('P009_UI_TEST')=='1','UI')
class SettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_path_input_and_subtitle_settings_persist(self):
        from factory_video_tool.daily_ui import DailyWindow
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                w.import_paths('contents','"/tmp/中文 文案.txt"\n/tmp/另一篇.txt')
                self.assertEqual(w.store.commands()[0]['payload']['paths'],['/tmp/中文 文案.txt','/tmp/另一篇.txt'])
                w.voice.addItem('Tingting');w.subtitle_size.setValue(48);w.subtitle_max_chars.setValue(12);w.subtitle_color='#FF0000';w.save_defaults()
                s=w.repository.setting('daily_defaults');self.assertEqual(s['font_size'],48);self.assertEqual(s['subtitle_max_chars'],12);self.assertEqual(s['font_color'],'#FF0000');self.assertTrue(s['font'])
            finally:w.close()
    def test_both_import_buttons_accept_paths_without_file_browser(self):
        from factory_video_tool.daily_ui import DailyWindow
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPlainTextEdit
        with tempfile.TemporaryDirectory() as d:
            w=DailyWindow(d,simulation=True,start_worker=False)
            try:
                for folder in (False,True):
                    value='/tmp/中文 空格目录' if folder else '/tmp/中文 文案.xlsx'
                    def fill():
                        dialog=w.import_dialogs[-1];dialog.findChild(QPlainTextEdit).setPlainText(value);dialog.accept()
                    QTimer.singleShot(0,fill);w.path_dialog('contents',folder)
                    self.assertEqual(w.store.commands()[0]['payload']['paths'],[value])
            finally:w.close()
