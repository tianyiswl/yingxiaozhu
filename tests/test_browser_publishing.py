import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from factory_video_tool.repository import Repository
from factory_video_tool.browser_publishing import BrowserPublicationService
from factory_video_tool.providers.douyin_browser import validate_content


class FakeBrowserPublisher:
    def __init__(self):self.clicks=0;self.bad_form=False;self.lost=False;self.account='123';self.pending={};self.no_item_id=False
    def prepare(self,path,content,account,cancel,progress,scheduled_for=None):
        self.pending={'token':'token-1','title':content['title'],'body':content['body'],'topics':content['topics'],'scheduled_for':scheduled_for}
        return self.pending
    def commit(self,receipt,account,cancel,progress,before_click):
        if self.bad_form:raise ValueError('页面内容已改变')
        if self.account!=account['platform_user_id']:raise ValueError('实际账号不一致')
        before_click();self.clicks+=1
        if self.lost:raise RuntimeError('结果不明')
        return ({'visibility':'SCHEDULED','review':'UNKNOWN','accepted':True,'verified_by':'content_manage_navigation'} if self.no_item_id else {'item_id':'123456789','visibility':'UNKNOWN','review':'UNKNOWN','accepted':True})
    def query(self,receipt,account,cancel,progress):return {'item_id':receipt.get('item_id'),'visibility':'UNKNOWN'}


class BrowserPublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(self.tmp.name);self.publisher=FakeBrowserPublisher()
        self.service=BrowserPublicationService(self.repo,self.publisher)
        self.account={'platform':'douyin','platform_user_id':'123','display_name':'测试','verified':True,'provider_id':'douyin_browser'}
        self.repo.save_setting('fixed_account',self.account)
        self.video=Path(self.tmp.name)/'video.mp4';self.video.write_bytes(b'local-test-video')
        self.job=self.repo.create_job({'content':{'title':'测试标题','voice_text':'口播','body':'测试正文','tags':'#工厂 #加工'}})
        self.repo.set_job(self.job['attempt_id'],'SUCCEEDED',str(self.video))
        self.cancel=threading.Event();self.progress=lambda _:None
    def prepare(self):return self.service.prepare(self.job['attempt_id'],self.cancel,self.progress)
    def test_preflight_never_submits_then_commit_once(self):
        record=self.prepare();self.assertEqual(record['status'],'PREPARED');self.assertEqual(self.publisher.clicks,0)
        result=self.service.submit(record['publication_id'],self.cancel,self.progress)
        self.assertEqual(result['status'],'ACCEPTED');self.assertEqual(self.publisher.clicks,1)
        with self.assertRaises(ValueError):self.service.submit(record['publication_id'],self.cancel,self.progress)

    def test_preflight_without_requested_time_keeps_immediate_platform_publish(self):
        record=self.prepare()
        scheduled=self.publisher.pending['scheduled_for']
        self.assertIsNone(scheduled)
    def test_manage_navigation_acceptance_does_not_require_a_work_id(self):
        record=self.prepare();self.publisher.no_item_id=True
        result=self.service.submit(record['publication_id'],self.cancel,self.progress)
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(result['receipt']['verified_by'],'content_manage_navigation')

    def test_lost_receipt_is_unknown_and_blocks_new_attempt(self):
        record=self.prepare();self.publisher.lost=True
        result=self.service.submit(record['publication_id'],self.cancel,self.progress)
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertIn('click_intent_at',result['receipt'])
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.publisher.clicks,1)
    def test_modified_fields_or_account_do_not_click(self):
        record=self.prepare();self.publisher.bad_form=True
        result=self.service.submit(record['publication_id'],self.cancel,self.progress)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(self.publisher.clicks,0)
    def test_frozen_video_change_blocks_click(self):
        record=self.prepare();Path(record['receipt']['staged_path']).write_bytes(b'changed')
        result=self.service.submit(record['publication_id'],self.cancel,self.progress)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(self.publisher.clicks,0)
    def test_restart_invalidates_prepared_page_without_replay(self):
        record=self.prepare();self.repo.recover()
        self.assertEqual(self.repo.get_publication(record['publication_id'])['status'],'INTERRUPTED')
        with self.assertRaises(ValueError):self.service.submit(record['publication_id'],self.cancel,self.progress)
    def test_structured_title_body_topics_are_separate(self):
        value=validate_content(self.job['request']['content'])
        self.assertEqual(value['body'],'测试正文');self.assertEqual(value['topics'],['工厂','加工'])
        with self.assertRaises(ValueError):validate_content({'title':'t','body':'手写 #话题','tags':''})
        with self.assertRaises(ValueError):validate_content({'title':'t','body':'正文 @某人','tags':''})
