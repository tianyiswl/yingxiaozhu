import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from factory_video_tool.core import load_excel,probe,duration,Runner
from factory_video_tool.platforms import resolve_font
from factory_video_tool.generation import generate,export_video

@unittest.skipUnless(os.environ.get('P009_INTEGRATION')=='1','explicit integration run')
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.root=Path('runtime/合成 测试资料').resolve()
        cls.content=load_excel(cls.root/'人工内容包 测试.xlsx')[0]
    def request(self,bgm=''):
        return dict(workspace=str(self.root/'输出 中文 空格'),content=self.content,material_root=str(self.root/'产品 测试画面'),bgm=bgm,voice='Tingting',rate=1.2,bgm_volume=.15,width=720,font=resolve_font())
    def test_real_generation_with_and_without_bgm(self):
        results=[]
        for bgm in ('',str(self.root/'合成 测试音调.wav')):
            path=generate(self.request(bgm),threading.Event(),print)
            manifest=json.loads((Path(path).parent/'manifest.json').read_text())
            self.assertEqual(manifest['status'],'SUCCESS')
            self.assertAlmostEqual(manifest['duration'],manifest['voice_duration']+.3,delta=.15)
            info=probe(path,Runner());self.assertEqual(info['streams'][0]['width'],720)
            results.append(path)
        (self.root/'latest-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    def test_invalid_bgm_preserves_request(self):
        bad=self.root/'损坏 音频.wav';bad.write_bytes(b'invalid')
        req=self.request(str(bad))
        with self.assertRaises(Exception):generate(req,threading.Event(),lambda _:None)
        self.assertEqual(req['content'],self.content)
    def test_export_same_and_existing(self):
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            p=Path(d)/'中文 source.mp4';p.write_bytes(b'test')
            self.assertEqual(export_video(p,p,threading.Event(),print),str(p.resolve()))
            q=Path(d)/'空格 dest.mp4';export_video(p,q,threading.Event(),print)
            self.assertEqual(q.read_bytes(),b'test')
            with self.assertRaises(ValueError):export_video(p,q,threading.Event(),print)
    def test_real_mac_tts_cancel(self):
        from factory_video_tool.platforms import MacSystemTTSProvider
        from factory_video_tool.core import Cancelled
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            e=threading.Event();timer=threading.Timer(.08,e.set);timer.start()
            provider=MacSystemTTSProvider(Runner(e));start=time.monotonic()
            try:
                with self.assertRaises(Cancelled):provider.synthesize(('这是一段取消技术测试。'*1000,'Tingting',.8,Path(d)))
                self.assertLess(time.monotonic()-start,3)
            finally:timer.cancel()
    def test_mixed_corrupt_and_outside_scope(self):
        from factory_video_tool.core import scan_materials
        import shutil
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            root=Path(d).resolve();scope=root/'产品甲';scope.mkdir()
            good=self.root/'产品 测试画面'/'合成 色块.mp4'
            shutil.copyfile(good,scope/'好 视频.mp4');(scope/'损坏.mp4').write_bytes(b'broken')
            (scope/'目录外.mp4').symlink_to(good)
            valid,errors=scan_materials(scope,Runner())
            self.assertEqual(len(valid),1);self.assertEqual(len(errors),2)
    def test_subtitle_has_legible_white_fill(self):
        from factory_video_tool.generation import draw_subtitles
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            draw_subtitles([(0,2,'中文清晰度测试')],Path(d),720,resolve_font())
            image=QImage(str(Path(d)/'subtitle-0000.png'))
            white=sum(1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).alpha()>200 and min(image.pixelColor(x,y).red(),image.pixelColor(x,y).green(),image.pixelColor(x,y).blue())>220)
            self.assertGreater(white,300)
