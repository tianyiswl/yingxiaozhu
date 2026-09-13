"""Exercise real HTTP encoding and durable submission transitions against a local fixture server."""
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('factory_video_tool.publishing'),'发布流程尚未实现')
        from factory_video_tool.repository import Repository
        from factory_video_tool.providers.douyin_publisher import DouyinPublisher,HTTPTransport
        from factory_video_tool.publishing import PublicationService
        self.tmp=tempfile.TemporaryDirectory(dir='.tmp');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.repo=Repository(self.root);self.calls=[];self.mode='ok';self.identity='account-A';owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                data=self.rfile.read(int(self.headers.get('Content-Length','0')))
                owner.calls.append((self.path,data))
                if self.path.startswith('/oauth/userinfo/'):
                    result={'open_id':owner.identity,'nickname':'测试固定账号','error_code':0}
                elif '/upload_video/' in self.path:
                    result={'video':{'video_id':'uploaded-id'},'error_code':0}
                elif '/create_video/' in self.path:
                    if owner.mode=='lost':self.connection.shutdown(2);self.connection.close();return
                    result={'item_id':'item-123','video_id':'12345678','error_code':0}
                else:
                    title='其他内容' if owner.mode=='mismatch' else '标题\n正文\n#标签'
                    result={'list':[{'item_id':'item-123','video_id':'12345678','title':title,'create_time':123}], 'error_code':0}
                raw=json.dumps({'data':result,'extra':{'error_code':0,'logid':'fixture'}}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        transport=HTTPTransport('http://127.0.0.1:%s'%self.server.server_port)
        self.publisher=DouyinPublisher(lambda:'test-not-a-real-token',transport)
        self.service=PublicationService(self.repo,self.publisher)
        self.account={'platform':'douyin','platform_user_id':'account-A','display_name':'测试固定账号','verified':True,'credential_ref':'fake'}
        self.repo.save_setting('fixed_account',self.account)
        self.video=self.root/'视频.mp4';self.video.write_bytes(b'fake-video-upload-fixture')
        job=self.repo.create_job({'content':{'title':'标题','voice_text':'口播','body':'正文','tags':'#标签'}})
        self.repo.set_job(job['attempt_id'],'SUCCEEDED',str(self.video));self.job=job['attempt_id']
    def prepare(self):return self.service.prepare(self.job,self.account)
    def submit(self,p):return self.service.submit(p['publication_id'],threading.Event(),lambda _:None)
    def test_real_http_upload_create_and_query_keep_visibility_unknown(self):
        p=self.prepare();result=self.submit(p)
        self.assertEqual(result['status'],'ACCEPTED')
        create=[json.loads(body) for path,body in self.calls if '/create_video/' in path][0]
        self.assertEqual(create,{'video_id':'uploaded-id','text':'标题\n正文\n#标签','private_status':0})
        upload=[body for path,body in self.calls if '/upload_video/' in path][0]
        self.assertIn(b'name="video"',upload);self.assertIn(b'fake-video-upload-fixture',upload)
        queried=self.service.query(p['publication_id'],threading.Event())
        self.assertEqual(queried['receipt']['query']['visibility'],'UNKNOWN')
        self.assertTrue(queried['receipt']['query']['content_matches'])
        with self.assertRaises(ValueError):self.prepare()
    def test_lost_submit_response_blocks_replay_even_after_restart(self):
        self.mode='lost';p=self.prepare();result=self.submit(p)
        self.assertEqual(result['status'],'UNKNOWN')
        self.repo.recover()
        with self.assertRaises(ValueError):self.submit(p)
        self.assertEqual(sum('/create_video/' in path for path,_ in self.calls),1)
        with self.assertRaises(ValueError):self.prepare()
    def test_wrong_account_prevents_upload(self):
        p=self.prepare();self.identity='account-B';result=self.submit(p)
        self.assertEqual(result['status'],'FAILED')
        self.assertFalse(any('/upload_video/' in path for path,_ in self.calls))
    def test_changed_video_is_not_uploaded(self):
        p=self.prepare();self.video.write_bytes(b'changed');result=self.submit(p)
        self.assertEqual(result['status'],'FAILED');self.assertFalse(self.calls)
    def test_changing_fixed_account_does_not_retarget_ready_publication(self):
        p=self.prepare();self.repo.save_setting('fixed_account',dict(self.account,platform_user_id='B'))
        result=self.submit(p);self.assertEqual(result['status'],'FAILED');self.assertFalse(self.calls)
    def test_query_content_mismatch_is_visible(self):
        p=self.prepare();self.submit(p);self.mode='mismatch'
        result=self.service.query(p['publication_id'],threading.Event())
        self.assertFalse(result['receipt']['query']['content_matches'])
        self.assertEqual(result['status'],'ACCEPTED')
    def test_unknown_can_reconcile_id_only_with_matching_account_and_content(self):
        self.mode='lost';p=self.prepare();self.submit(p)
        self.mode='mismatch'
        with self.assertRaises(ValueError):self.service.reconcile(p['publication_id'],'item-123',threading.Event())
        self.assertEqual(self.repo.get_publication(p['publication_id'])['status'],'UNKNOWN')
        self.mode='ok';result=self.service.reconcile(p['publication_id'],'item-123',threading.Event())
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(sum('/create_video/' in path for path,_ in self.calls),1)

    def test_upload_uses_frozen_copy_if_source_changes_after_preparation(self):
        p=self.prepare()
        real_upload=self.publisher.upload
        def source_changes(path,account,cancel):
            self.video.write_bytes(b'changed-during-upload')
            return real_upload(path,account,cancel)
        self.publisher.upload=source_changes
        result=self.submit(p)
        self.assertEqual(result['status'],'ACCEPTED')
        payload=[body for path,body in self.calls if '/upload_video/' in path][0]
        self.assertIn(b'fake-video-upload-fixture',payload)
        self.assertNotIn(b'changed-during-upload',payload)
