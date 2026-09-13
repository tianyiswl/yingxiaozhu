import tempfile
import threading
import unittest
import os
from pathlib import Path
from unittest.mock import Mock,patch
from factory_video_tool.core import validate_rows, subtitle_cues, choose_clips, Cancelled, synthesize_with_retry, Runner, scan_materials, executable

class CoreTests(unittest.TestCase):
    def test_packaged_ffmpeg_is_preferred_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            bundled=Path(directory)/'tools'/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg');bundled.parent.mkdir();bundled.write_bytes(b'fixture')
            with patch('factory_video_tool.core.sys._MEIPASS',directory,create=True),patch.dict('os.environ',{},clear=True):
                self.assertEqual(executable('ffmpeg'),str(bundled))

    def test_content_validation(self):
        row=dict(external_id='一',voice_text='中文测试',title='测试标题',body='正文',tags='#测试',enabled=1)
        self.assertEqual(validate_rows([row])[0]['body'], '正文')
        for patch in ({'voice_text':''},{'title':''},{'enabled':'perhaps'}):
            with self.assertRaises(ValueError): validate_rows([dict(row,**patch)])
        with self.assertRaises(ValueError): validate_rows([row,row])
        self.assertEqual(validate_rows([dict(row,enabled=None)])[0]['enabled'],True)
    def test_subtitle_timing(self):
        cues=subtitle_cues('中文测试，第二句字幕。非常长的字幕需要自动拆分显示在画面安全区。',8.2)
        self.assertEqual(cues[0][0],0)
        self.assertAlmostEqual(cues[-1][1],8.2)
        self.assertTrue(all(len(x[2])<=14 for x in cues))
        compact=subtitle_cues('这是用于验证字幕每句显示字数的完整测试文本。',4,10)
        self.assertTrue(all(len(x[2])<=10 for x in compact))
        self.assertTrue(all(a[1]==b[0] for a,b in zip(cues,cues[1:])))
    def test_short_material_reuse_and_bounds(self):
        clips=choose_clips([(Path('中文 空格.mp4'),1.2)],5.3)
        self.assertAlmostEqual(sum(c[2] for c in clips),5.3)
        self.assertTrue(all(s>=0 and s+d<=1.20001 for _,s,d in clips))
        with self.assertRaises(ValueError): choose_clips([],4)
    def test_tts_failure_and_cancel(self):
        p=Mock();p.synthesize.side_effect=RuntimeError('系统配音不可用')
        with self.assertRaises(RuntimeError): synthesize_with_retry(p,None)
        self.assertEqual(p.synthesize.call_count,2)
        p.reset_mock();p.synthesize.side_effect=Cancelled('已取消')
        with self.assertRaises(Cancelled): synthesize_with_retry(p,None)
        self.assertEqual(p.synthesize.call_count,1)
    def test_missing_empty_corrupt(self):
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            p=Path(d)
            r=Runner(threading.Event())
            with self.assertRaises(ValueError):scan_materials(p/'不存在',r)
            with self.assertRaises(ValueError):scan_materials(p,r)
            (p/'坏 文件.mp4').write_text('not video')
            with self.assertRaises(ValueError):scan_materials(p,r)
    def test_process_cancel(self):
        import sys,time
        e=threading.Event();r=Runner(e)
        timer=threading.Timer(.2,e.set);timer.start();start=time.monotonic()
        with self.assertRaises(Cancelled):r.run([sys.executable,'-c','import time;time.sleep(30)'])
        self.assertLess(time.monotonic()-start,3)
    def test_lost_material_path(self):
        from factory_video_tool.core import probe
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            p=Path(d)/'已移动 素材.mp4';p.write_bytes(b'test');p.rename(Path(d)/'新 名.mp4')
            with self.assertRaisesRegex(ValueError,'不存在'):probe(p,Runner())
    def test_cancelled_export_leaves_no_final(self):
        from factory_video_tool.generation import export_video
        with tempfile.TemporaryDirectory(dir='.tmp') as d:
            source=Path(d)/'输入.mp4';source.write_bytes(b'test');dest=Path(d)/'输出.mp4'
            e=threading.Event();e.set()
            with self.assertRaises(Cancelled):export_video(source,dest,e,print)
            self.assertFalse(dest.exists());self.assertTrue(source.exists())
