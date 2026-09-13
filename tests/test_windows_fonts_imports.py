import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from factory_video_tool.platforms import resolve_font
from factory_video_tool.library import Library,DOC_EXT
from factory_video_tool.batch_client import ensure_worker

class WindowsFontImportTests(unittest.TestCase):
    def test_chinese_family_names(self):
        with patch('platform.system',return_value='Windows'),patch('PySide6.QtGui.QFontDatabase.families',return_value=['宋体']):
            self.assertEqual(resolve_font(),'宋体')

    def test_worker_uses_native_windows_font_environment(self):
        with tempfile.TemporaryDirectory() as root,patch('platform.system',return_value='Windows'),patch('subprocess.Popen') as launch:
            ensure_worker(root,simulation=True)
            self.assertEqual(launch.call_args.kwargs['env']['QT_QPA_PLATFORM'],'windows')

    def test_document_folder_skips_packaged_dependencies(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            for name in ['文案/模板.xlsx','_internal/importlib_metadata-1.dist-info/top_level.txt','node_modules/readme.txt']:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
            self.assertEqual(Library.expand([root],DOC_EXT),[(root/'文案/模板.xlsx').resolve()])
