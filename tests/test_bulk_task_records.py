import tempfile
import unittest
from factory_video_tool.repository import Repository
from factory_video_tool.batch_store import BatchStore
from factory_video_tool.daily_ui import voice_label

class BulkTaskRecordTests(unittest.TestCase):
    def test_voice_name_is_short(self):
        self.assertEqual(voice_label(r'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_ZH-CN_HUIHUI_11.0'),'慧慧 · 中文')
        self.assertEqual(voice_label('Tingting'),'Tingting')

    def test_delete_retains_history_and_blocks_active_or_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            store=BatchStore(Repository(root))
            for state in ['ACCEPTED','FAILED','SUBMITTING','UNKNOWN']:
                with store.repo.connect() as db:
                    db.execute('INSERT INTO daily_items(id,batch_id,content_id,ordinal,due_at,request,state,reservation) VALUES(?,?,?,?,?,?,?,0)',(state,'batch',state,1,1,'{}',state))
            store.delete_task_record('ACCEPTED');store.delete_task_record('FAILED')
            self.assertEqual(store.deleted_task_ids(),{'ACCEPTED','FAILED'})
            self.assertEqual(store.item('ACCEPTED')['state'],'ACCEPTED')
            self.assertEqual(store.item('FAILED')['state'],'CANCELLED')
            for state in ['SUBMITTING','UNKNOWN']:
                with self.assertRaises(ValueError):store.delete_task_record(state)
