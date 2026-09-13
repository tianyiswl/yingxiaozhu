import tempfile
import unittest
from unittest.mock import patch
from factory_video_tool.batch_client import ensure_worker


class PackagedWorkerTests(unittest.TestCase):
    def test_missing_voices_do_not_stop_import_queue(self):
        from factory_video_tool.batch_worker import Background
        from factory_video_tool.batch_store import BatchStore
        from factory_video_tool.repository import Repository
        with tempfile.TemporaryDirectory() as root:
            repo=Repository(root);store=BatchStore(repo)
            with patch('threading.Thread.start'),patch('factory_video_tool.batch_worker.tts_provider') as speech:
                speech.return_value.list_voices.side_effect=RuntimeError('No SAPI voice')
                worker=Background(store,True,'')
                def process(command):
                    worker.stop.set()
                    return {'message':'processed'}
                store.command('LOGIN')
                worker.execute_command=process
                worker.run()
            self.assertEqual(store.commands()[0]['status'],'DONE')
    def test_frozen_client_uses_worker_entrypoint(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('sys.frozen',True,create=True),patch('subprocess.Popen') as launch:
                ensure_worker(root,simulation=True)
            args=launch.call_args.args[0]
            self.assertIn('--worker',args)
            self.assertNotIn('-m',args)
            self.assertIn('--simulate',args)
    def test_source_client_keeps_module_entrypoint(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('sys.frozen',False,create=True),patch('subprocess.Popen') as launch:
                ensure_worker(root,simulation=True)
            self.assertIn('-m',launch.call_args.args[0])
