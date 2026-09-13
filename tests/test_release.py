"""Behavioral regressions: document loss, mutable snapshots and unsafe recovery."""
import importlib.util
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('factory_video_tool.repository'), '首版任务仓储尚未实现')
        from factory_video_tool.repository import Repository
        self.tmp=tempfile.TemporaryDirectory(dir='.tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.repo=Repository(self.root)
    def test_local_text_preserves_paragraphs_and_docx_rejects_tables(self):
        from factory_video_tool.providers.local_documents import LocalDocuments
        p=self.root/'中文 文案.txt';p.write_text('标题\n\n第一段。\n第二段。',encoding='utf-8-sig')
        content=LocalDocuments().load(p)[0]
        self.assertEqual(content['title'],'标题')
        self.assertEqual(content['voice_text'],'第一段。\n第二段。')
        doc=self.root/'文案.docx'
        with zipfile.ZipFile(doc,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>标题</w:t></w:r></w:p><w:p><w:r><w:t>口播</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(LocalDocuments().load(doc)[0]['voice_text'],'口播')
        with zipfile.ZipFile(doc,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl/></w:body></w:document>')
        with self.assertRaisesRegex(ValueError,'表格'):LocalDocuments().load(doc)
    def test_job_snapshot_is_immutable_and_retry_retains_old_attempt(self):
        req={'content':{'title':'原标题','voice_text':'原口播','enabled':True}}
        job=self.repo.create_job(req);req['content']['title']='已改'
        self.assertEqual(self.repo.get_job(job['attempt_id'])['request']['content']['title'],'原标题')
        self.repo.set_job(job['attempt_id'],'FAILED',error='故障')
        retry=self.repo.create_job(self.repo.get_job(job['attempt_id'])['request'],job_id=job['job_id'])
        self.assertNotEqual(retry['attempt_id'],job['attempt_id'])
        self.assertEqual(self.repo.get_job(job['attempt_id'])['status'],'FAILED')
    def test_recovery_does_not_claim_interrupted_work_succeeded(self):
        j=self.repo.create_job({'content':{}});self.repo.set_job(j['attempt_id'],'RUNNING')
        pub=self.repo.create_publication(j['attempt_id'],{'platform_user_id':'A'}, {'text':'测试'})
        self.repo.set_publication(pub['publication_id'],'SUBMITTING')
        self.repo.recover()
        self.assertEqual(self.repo.get_job(j['attempt_id'])['status'],'INTERRUPTED')
        self.assertEqual(self.repo.get_publication(pub['publication_id'])['status'],'UNKNOWN')
    def test_workflow_serial_failure_cancel_and_injected_providers(self):
        from factory_video_tool.workflow import Workflow
        from factory_video_tool.models import SpeechArtifact
        from factory_video_tool.models import text_hash
        seen=[]
        class Speech:
            def synthesize(self,request,work,cancel,progress):
                seen.append(request['content']['title'])
                if request['content']['title']=='坏':raise ValueError('音色不可用')
                path=work/'voice.wav';path.write_bytes(b'test-only-audio')
                return SpeechArtifact(str(path),1,'test',text_hash(request['content']['voice_text']))
        class Render:
            def render(self,request,speech,work,cancel,progress):
                out=work/'result.mp4';out.write_text(request['content']['title'],encoding='utf-8');return str(out)
        (self.root/'素材.mp4').write_bytes(b'test-only-video')
        flow=Workflow(self.repo,Speech(),Render())
        jobs=[flow.enqueue({'material_root':str(self.root),'content':{'title':t,'voice_text':'口播','enabled':True}}) for t in ['甲','坏','乙']]
        results=flow.run([j['attempt_id'] for j in jobs],threading.Event(),lambda _:None)
        self.assertEqual([r['status'] for r in results],['SUCCEEDED','FAILED','SUCCEEDED'])
        self.assertEqual([Path(r['result']).read_text(encoding='utf-8') for r in results if r['result']],['甲','乙'])
        cancel=threading.Event();cancel.set();new=flow.enqueue({'material_root':str(self.root),'content':{'title':'丙','voice_text':'口播','enabled':True}})
        flow.run([new['attempt_id']],cancel,lambda _:None)
        self.assertEqual(self.repo.get_job(new['attempt_id'])['status'],'CANCELLED')
        self.assertEqual(seen,['甲','坏','乙'])
    def test_disabled_content_cannot_be_enqueued(self):
        from factory_video_tool.workflow import Workflow
        with self.assertRaisesRegex(ValueError,'停用'):
            Workflow(self.repo).enqueue({'content':{'title':'标题','voice_text':'口播','enabled':False}})
