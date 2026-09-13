import tempfile
import threading
import unittest
from pathlib import Path

from factory_video_tool.batch_store import BatchStore
from factory_video_tool.core import Runner
from factory_video_tool.library import Library
from factory_video_tool.repository import Repository


class BGMLibraryTests(unittest.TestCase):
    def test_import_copies_valid_audio_and_deduplicates_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'背景音乐.wav'
            Runner().run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=1',str(source)])
            library=Library(BatchStore(Repository(root/'workspace')))
            first=library.import_bgms([str(source)],threading.Event(),lambda _:None)
            second=library.import_bgms([str(source)],threading.Event(),lambda _:None)
            records=library.store.bgms()
            self.assertEqual((first['added'],first['duplicate']),(1,0))
            self.assertEqual((second['added'],second['duplicate']),(0,1))
            self.assertEqual(len(records),1)
            self.assertTrue(Path(records[0]['path']).is_file())
            self.assertGreater(records[0]['duration'],.9)
