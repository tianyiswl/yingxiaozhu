import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QTimer,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from factory_video_tool.ui import Window
from factory_video_tool.core import Runner,load_excel

@unittest.skipUnless(os.environ.get('P009_UI_TEST')=='1','explicit UI integration run')
class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.root=Path('runtime/合成 测试资料').resolve()
    def wait_for(self,predicate,timeout=60000):
        start=time.monotonic()
        while not predicate():
            if (time.monotonic()-start)*1000>timeout:self.fail('UI wait timed out')
            self.app.processEvents();time.sleep(.02)
    def window(self):
        w=Window(self.root/'界面 测试');w.show();QTest.qWait(50)
        self.wait_for(lambda:w.worker is None)
        self.assertTrue(w.voice.count()>0,w.log.toPlainText())
        w.load_path(str(self.root/'人工内容包 测试.xlsx'));self.wait_for(lambda:w.worker is None)
        w.material.setText(str(self.root/'产品 测试画面'));w.show_wizard(2);return w
    def test_real_ui_generate_preview_export_responsive(self):
        w=self.window();w.bgm.setText(str(self.root/'合成 测试音调.wav'))
        frames=[];w.video.videoSink().videoFrameChanged.connect(lambda f:frames.append(f.isValid()))
        ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start(25)
        QTest.mouseClick(w.start_button,Qt.MouseButton.LeftButton)
        self.wait_for(lambda:w.worker is None)
        timer.stop()
        self.assertIsNotNone(w.result,w.log.toPlainText())
        self.assertGreater(len(ticks),10)
        max_gap=max(b-a for a,b in zip(ticks,ticks[1:]))
        self.assertLess(max_gap,1.0)
        self.wait_for(lambda:w.player.position()>200,10000)
        self.wait_for(lambda:any(frames),10000)
        w.player.pause();w.player.setPosition(1800);QTest.qWait(500)
        evidence=Path('docs/development');w.grab().save(str(evidence/'ui-preview.png'))
        out=self.root/('界面 导出 '+str(time.time_ns())+'.mp4')
        QTest.mouseClick(w.export,Qt.MouseButton.LeftButton)
        w.save_name.setText(out.name);w.save_destination.setCurrentIndex(3);w.save_directory.setText(str(out.parent))
        QTest.mouseClick(w.confirm_save,Qt.MouseButton.LeftButton)
        self.wait_for(lambda:w.worker is None)
        self.assertEqual(Path(w.result).read_bytes(),out.read_bytes())
        report=dict(event_ticks=len(ticks),max_event_gap_seconds=max_gap,output=w.result,export=str(out),preview_position_ms=w.player.position(),valid_preview_frames=sum(frames))
        (evidence/'ui-test-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        w.close();QTest.qWait(50)
    def test_import_failure_preserves_content_and_cancel_tts(self):
        w=self.window();original=w.contents.copy()
        w.load_path(str(self.root/'不存在.xlsx'));self.wait_for(lambda:w.worker is None)
        self.assertEqual(w.contents,original)
        reached=threading.Event()
        def slow(provider,request):
            reached.set();provider.runner.run([sys.executable,'-c','import time;time.sleep(30)'])
        with patch('factory_video_tool.platforms.MacSystemTTSProvider.synthesize',slow):
            QTest.mouseClick(w.start_button,Qt.MouseButton.LeftButton)
            self.wait_for(reached.is_set,10000)
            start=time.monotonic();QTest.mouseClick(w.cancel_button,Qt.MouseButton.LeftButton)
            self.wait_for(lambda:w.worker is None,5000)
            self.assertLess(time.monotonic()-start,3)
            self.assertEqual(w.contents,original);self.assertIsNone(w.result)
            self.assertIn('已取消',w.log.toPlainText())
        self.assertTrue(w.start_button.isEnabled());w.close();QTest.qWait(50)
    def test_close_cancels_worker_safely(self):
        w=self.window();reached=threading.Event()
        def slow(provider,request):
            reached.set();provider.runner.run([sys.executable,'-c','import time;time.sleep(30)'])
        with patch('factory_video_tool.platforms.MacSystemTTSProvider.synthesize',slow):
            w.start_generation();self.wait_for(reached.is_set,10000);w.close()
            self.wait_for(lambda:not w.isVisible(),5000)
            self.assertIsNone(w.worker)
