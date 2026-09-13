import tempfile
import unittest
from pathlib import Path

from factory_video_tool.batch_planner import BatchPlanner
from factory_video_tool.batch_store import BatchStore
from factory_video_tool.library import Library
from factory_video_tool.repository import Repository


class LibraryDeletionTests(unittest.TestCase):
    def records(self,library):
        material=library.assets/'material.mp4';material.write_bytes(b'video')
        music=library.bgm_assets/'music.mp3';music.write_bytes(b'audio')
        with library.store.repo.connect() as db:
            db.execute('INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',('material',str(material),'车间镜头.mp4',3,1))
            db.execute('INSERT INTO library_bgm(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',('music',str(music),'车间节奏.mp3',3,1))
        content,_=library.add_content({'title':'工厂标题','voice_text':'工厂口播','body':'','tags':''})
        return material,music,content

    def test_remove_library_records_and_managed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            library=Library(BatchStore(Repository(Path(folder)/'workspace')))
            material,music,content=self.records(library)
            library.store.repo.save_setting('daily_defaults',{'bgm_enabled':True,'bgm_mode':'specified','bgm':str(music)})
            library.remove_material('material');library.remove_bgm('music');library.remove_content(content)
            self.assertTrue(material.exists());self.assertTrue(music.exists())
            self.assertEqual(library.store.materials(),[]);self.assertEqual(library.store.bgms(),[]);self.assertEqual(library.store.contents(),[])
            defaults=library.store.repo.setting('daily_defaults')
            self.assertFalse(defaults['bgm_enabled']);self.assertEqual(defaults['bgm'],'')

    def test_remove_blocks_records_used_by_unfinished_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            library=Library(BatchStore(Repository(Path(folder)/'workspace')))
            _material,music,content=self.records(library)
            spec={'count':1,'days':1,'interval':180,'mode':'simulate','immediate':True,
                  'settings':{'voice':'Tingting','bgm_enabled':True,'bgm_mode':'specified','bgm':str(music)}}
            BatchPlanner(library.store).create(spec,'delete-guard',1_789_000_000)
            for remove,ident in ((library.remove_material,'material'),(library.remove_bgm,'music'),(library.remove_content,content)):
                with self.assertRaisesRegex(ValueError,'未完成任务.*工厂标题.*待制作.*任务编号.*取消'):
                    remove(ident)
