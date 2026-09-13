import unittest,tempfile,threading
from unittest.mock import Mock
from factory_video_tool.repository import Repository
from factory_video_tool.batch_worker import Background

class RestoreTests(unittest.TestCase):
    def test_existing_account_checked_and_failure_does_not_stop_worker(self):
        with tempfile.TemporaryDirectory() as d:
            b=Background.__new__(Background)
            b.repo=Repository(d);b.simulation=False;b.stop=threading.Event()
            b.session=Mock();b.ensure_session=Mock();b.progress=Mock();b.heartbeat=Mock()
            b.restore_account();b.ensure_session.assert_not_called()
            b.repo.save_setting('fixed_account',{'platform_user_id':'123'})
            b.restore_account();b.session.restore.assert_called_once_with(b.stop)
            b.session.restore.side_effect=RuntimeError('network unavailable')
            b.restore_account()
            self.assertFalse(b.stop.is_set())
            self.assertIn('network',b.repo.setting('daily_account_restore_error'))
            b.simulation=True;b.session.reset_mock();b.restore_account()
            b.session.restore.assert_not_called()
