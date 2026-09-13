import tempfile,threading,unittest,json
from pathlib import Path
from unittest.mock import patch
from factory_video_tool.storage import migrate,startup_storage,save_config
from factory_video_tool.repository import Repository
from factory_video_tool.batch_store import BatchStore
from factory_video_tool.library import Library
from factory_video_tool.models import file_hash
from factory_video_tool.workflow import Workflow

class PortableStorageTests(unittest.TestCase):
    def setUp(self):
        original_temp=tempfile.tempdir
        self.addCleanup(setattr,tempfile,'tempdir',original_temp)
        env=patch.dict('os.environ',{},clear=False);env.start();self.addCleanup(env.stop)

    def test_new_package_ignores_all_old_test_data_without_dialog(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);program=base/'new';program.mkdir()
            legacy=base/'system/P009FactoryVideo';Repository(legacy).save_setting('marker','old')
            registry=base/'system/P009FactoryVideo-storage.json'
            registry.write_text(json.dumps({'data_dir':str(legacy)}))
            Repository(program/'runtime').save_setting('marker','old-runtime')
            with patch.dict('os.environ',{'LOCALAPPDATA':str(base/'system')}),patch('factory_video_tool.storage.program_dir',return_value=program),patch('factory_video_tool.storage.choose_existing_directory') as picker,patch('factory_video_tool.storage.migrate') as copy,patch('PySide6.QtWidgets.QMessageBox.information') as message:
                root=startup_storage();self.assertEqual(root,(program/'data').resolve())
                self.assertIsNone(Repository(root).setting('marker'))
                picker.assert_not_called();copy.assert_not_called();message.assert_not_called()
                Repository(root).save_setting('marker','current')
                self.assertEqual(startup_storage(),root)
                self.assertEqual(Repository(root).setting('marker'),'current')
            self.assertEqual(Repository(legacy).setting('marker'),'old')
            self.assertEqual(json.loads(registry.read_text())['data_dir'],str(legacy))

    def test_separate_packages_have_separate_empty_databases(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            with patch('factory_video_tool.storage.program_dir',return_value=base/'v1'):
                old=startup_storage();Repository(old).save_setting('marker','old')
            with patch('factory_video_tool.storage.program_dir',return_value=base/'v2'):
                new=startup_storage();self.assertNotEqual(new,old)
                self.assertIsNone(Repository(new).setting('marker'))

    def test_saved_directory_is_preserved_and_missing_directory_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            program=Path(d);selected=program/'chosen';Repository(selected)
            with patch('factory_video_tool.storage.program_dir',return_value=program):
                save_config({'data_dir':str(selected)})
                self.assertEqual(startup_storage(),selected.resolve())
                save_config({'data_dir':str(program/'missing')})
                with patch('factory_video_tool.storage.choose_existing_directory',side_effect=ValueError('cancelled')):
                    with self.assertRaises(ValueError):startup_storage()
                self.assertFalse((program/'missing').exists())

    def test_only_user_requested_migration_is_performed_once(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);program=base/'program';program.mkdir();old=base/'old';new=base/'new'
            Repository(old).save_setting('marker','kept')
            with patch('factory_video_tool.storage.program_dir',return_value=program):
                save_config({'data_dir':str(new),'migrate_from':str(old)})
                with patch('PySide6.QtWidgets.QProgressDialog'),patch('PySide6.QtWidgets.QMessageBox.information'),patch('PySide6.QtWidgets.QApplication.processEvents'):
                    self.assertEqual(startup_storage(),new.resolve())
                self.assertNotIn('migrate_from',json.loads((program/'storage.json').read_text()))
                with patch('factory_video_tool.storage.migrate') as copy:
                    self.assertEqual(startup_storage(),new.resolve());copy.assert_not_called()
            self.assertEqual(Repository(new).setting('marker'),'kept');self.assertTrue((old/'app.sqlite').exists())

    def test_music_reference_and_removal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);lib=Library(BatchStore(Repository(root/'data')))
            source=root/'music.wav';source.write_bytes(b'audio')
            with patch('factory_video_tool.library.probe',return_value={'streams':[{'codec_type':'audio'}],'format':{'duration':'2'}}),patch('factory_video_tool.library.Runner.run'):
                result=lib.import_bgms([source],threading.Event(),lambda _:None)
            self.assertEqual(result['added'],1,result);self.assertEqual(list(lib.bgm_assets.iterdir()),[])
            lib.remove_bgm(file_hash(source));self.assertTrue(source.exists())

    def test_migration_preserves_original_and_rewrites_database_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'old';new=root/'new';repo=Repository(old);store=BatchStore(repo)
            asset=old/'library/assets/a.mp4';asset.parent.mkdir(parents=True);asset.write_bytes(b'video')
            repo.save_setting('daily_defaults',{'bgm':str(old/'library/bgm/a.wav')})
            repo.save_setting('trial_period',{'started_at':'original-date'})
            with repo.connect() as db:db.execute('INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',('a',str(asset),'a',1,0))
            migrate(old,new)
            self.assertEqual(asset.read_bytes(),(new/'library/assets/a.mp4').read_bytes())
            copied=Repository(new)
            self.assertEqual(BatchStore(copied).materials()[0]['path'],str((new/'library/assets/a.mp4').resolve()))
            self.assertEqual(copied.setting('trial_period'),{'started_at':'original-date'})
            self.assertEqual(repo.setting('daily_defaults')['bgm'],str(old/'library/bgm/a.wav'))
            with self.assertRaises(ValueError):migrate(old,new)

    def test_default_storage_is_program_local(self):
        with tempfile.TemporaryDirectory() as d,patch('factory_video_tool.storage.program_dir',return_value=Path(d)):
            original_temp=__import__('tempfile').tempdir
            with patch.dict('os.environ',{},clear=False):
                try:
                    root=startup_storage();repo=Repository(root)
                    self.assertEqual(root,(Path(d)/'data').resolve())
                    self.assertEqual(repo.setting('output_dir'),str((Path(d)/'outputs').resolve()))
                    self.assertEqual(repo.setting('temp_dir'),str((Path(d)/'temp').resolve()))
                finally:__import__('tempfile').tempdir=original_temp

    def test_registered_external_video_can_enter_workflow(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo=Repository(root/'data');lib=Library(BatchStore(repo));video=root/'source.mp4';video.write_bytes(b'video');sha=file_hash(video)
            with repo.connect() as db:db.execute('INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',(sha,str(video),'source',1,0))
            job=Workflow(repo).enqueue({'material_root':str(lib.assets),'material_snapshot':[{'path':str(video),'sha256':sha}],'content':{'title':'test','voice_text':'text'}})
            self.assertEqual(job['status'],'QUEUED')
