import tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from factory_video_tool.library import Library
from factory_video_tool.repository import Repository
from factory_video_tool.batch_store import BatchStore
from factory_video_tool.models import file_hash

class MaterialReferenceTests(unittest.TestCase):
    def test_import_stores_path_and_delete_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4';source.write_bytes(b'video')
            lib=Library(BatchStore(Repository(root/'workspace')))
            with patch('factory_video_tool.library.probe',return_value={'streams':[{'codec_type':'video'}],'format':{'duration':'2'}}),patch('factory_video_tool.library.Runner.run'):
                result=lib.import_materials([source],threading.Event(),lambda _:None)
            self.assertEqual(result['added'],1,result)
            record=lib.store.materials()[0]
            self.assertEqual(Path(record['path']),source.resolve());self.assertEqual(record['storage_mode'],'reference')
            self.assertEqual(list(lib.assets.iterdir()),[])
            lib.remove_material(record['id']);self.assertEqual(source.read_bytes(),b'video')

    def test_old_copy_cleanup_requires_matching_available_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);lib=Library(BatchStore(Repository(root/'workspace')))
            old=lib.assets/'legacy.mp4';old.write_bytes(b'video');ident=file_hash(old)
            original=root/'original.mp4';original.write_bytes(b'video');wrong=root/'wrong.mp4';wrong.write_bytes(b'other')
            with lib.store.repo.connect() as db:db.execute('INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',(ident,str(old),'legacy',2,1))
            with self.assertRaisesRegex(ValueError,'内容不同'):lib.relink_material(ident,wrong)
            self.assertTrue(old.exists())
            lib.relink_material(ident,original);self.assertTrue(old.exists())
            original.unlink()
            with self.assertRaisesRegex(ValueError,'保留旧副本'):lib.cleanup_material_copy(ident)
            self.assertTrue(old.exists())
            original.write_bytes(b'video');lib.cleanup_material_copy(ident)
            self.assertFalse(old.exists());self.assertTrue(original.exists())
