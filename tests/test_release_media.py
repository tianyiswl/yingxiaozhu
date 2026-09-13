"""The renderer must consume supplied audio without selecting or invoking a voice."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from factory_video_tool.core import Runner,probe,duration
from factory_video_tool.models import SpeechArtifact,text_hash
from factory_video_tool.generation import render
from factory_video_tool.platforms import resolve_font

@unittest.skipUnless(os.environ.get('P009_INTEGRATION')=='1','explicit real render integration')
class ProviderMediaTests(unittest.TestCase):
    def test_subtitles_remain_visible_near_the_end_of_a_long_render(self):
        """Catches an image-sequence overlay that stopped advancing after the early cues."""
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(dir='.tmp') as folder:
            root=Path(folder).resolve();audio=root/'long.wav'
            Runner().run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=320:duration=20','-ar','44100',audio])
            reference=Path('runtime/tasks/99e5851af0df48c19bf95a5a46896bb7/eefa1a7f302c4ab388d5a800c293fa71/manifest.json')
            payload=json.loads(reference.read_text())
            text=payload['content']['voice_text'];clips=[(Path(path),start,length) for path,start,length in payload['clips']]
            request={'content':{'voice_text':text,'title':'字幕连续性测试'},'material_root':str(Path('runtime/library/assets').resolve()),'width':720,'font':resolve_font()}
            speech=SpeechArtifact(str(audio),20,'injected-audio',text_hash(text))
            with patch('factory_video_tool.generation.choose_clips',return_value=clips):
                result=render(request,speech,root/'output',threading.Event(),lambda _:None)
            joined=root/'output'/'joined.mp4';late=root/'late.png';source=root/'source.png'
            Runner().run(['ffmpeg','-y','-v','error','-ss','17','-i',result,'-frames:v','1','-vf','crop=720:216:0:896',late])
            Runner().run(['ffmpeg','-y','-v','error','-ss','17','-i',joined,'-frames:v','1','-vf','crop=720:216:0:896',source])
            from PySide6.QtGui import QImage
            final=QImage(str(late));plain=QImage(str(source));changed=0
            for y in range(final.height()):
                for x in range(final.width()):
                    a=final.pixelColor(x,y);b=plain.pixelColor(x,y)
                    if max(abs(a.red()-b.red()),abs(a.green()-b.green()),abs(a.blue()-b.blue()))>80:changed+=1
            self.assertGreater(changed,500,'17秒处应仍有字幕叠加')

    def test_existing_audio_drives_render_and_wrong_text_binding_is_rejected(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(dir='.tmp') as folder:
            root=Path(folder).resolve();audio=root/'外部声音.wav'
            Runner().run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=320:duration=1.2','-ar','44100',audio])
            request={'content':{'voice_text':'外部声音测试','title':'测试'},'material_root':str(Path('runtime/合成 测试资料/产品 测试画面').resolve()),'width':720,'font':resolve_font()}
            speech=SpeechArtifact(str(audio),1.2,'injected-audio',text_hash('外部声音测试'))
            with patch('factory_video_tool.platforms.tts_provider',side_effect=AssertionError('Renderer must not invoke TTS')):
                result=render(request,speech,root/'output',threading.Event(),lambda _:None)
            self.assertAlmostEqual(duration(probe(result,Runner())),1.5,delta=.15)
            wrong=SpeechArtifact(str(audio),1.2,'injected-audio',text_hash('另一篇口播'))
            with self.assertRaisesRegex(ValueError,'不匹配'):render(request,wrong,root/'wrong',threading.Event(),lambda _:None)
