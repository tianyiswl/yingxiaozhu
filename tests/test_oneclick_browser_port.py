"""Real browser tests on synthetic pages. No real account or platform traffic."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from factory_video_tool.providers.douyin_browser import DouyinBrowserPublisher,validate_content
from factory_video_tool.providers.oneclick_form import OneClickForm
from factory_video_tool.core import Cancelled

HTML='''<!doctype html><meta charset="utf-8"><title>Local P009 fixture</title>
<div class="upload-card-test"><input type="file" accept="image/png"><input type="file" accept="video/mp4" onchange="document.querySelector('#form').hidden=false"></div>
<section id="form" hidden><div class="long-card-test"><div>重新上传</div></div>
<input placeholder="填写作品标题" value="上次的标题">
<div class="zone-container" contenteditable="true">上次文案</div>
<button id="topic">#添加话题</button><span class="tag-hash-view-name" style="display:none">工厂</span>
<label id="schedule-label" data-checked="false"><input id="schedule" type="checkbox">定时发布</label><input id="datetime" placeholder="发布时间">
<button id="publish">发布</button></section><script>
window.finalClicks=0;window.showVerification=false;window.topicEntity=true;
document.querySelector('#schedule-label').onclick=()=>document.querySelector('#schedule-label').dataset.checked='true';
 document.querySelector('#topic').onclick=()=>{const e=document.querySelector('.zone-container');e.focus();
 const r=document.createRange();r.selectNodeContents(e);r.collapse(false);getSelection().removeAllRanges();getSelection().addRange(r);
 document.querySelector('.tag-hash-view-name').style.display='inline';};
document.querySelector('.tag-hash-view-name').onclick=()=>{const e=document.querySelector('.zone-container');
 const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);let n,last;while(n=walker.nextNode())last=n;if(last&&last.nodeValue.endsWith('工厂'))last.nodeValue=last.nodeValue.slice(0,-2);let s=document.createElement('span');s.textContent='#工厂';
 if(window.topicEntity){s.dataset.mention='#';s.contentEditable='false';} e.append(s);
 document.querySelector('.tag-hash-view-name').style.display='none';};
for (const id of ['publish']) document.querySelector('#'+id).onclick=()=>{window.finalClicks++;
 if(window.showVerification){let p=document.createElement('div');p.className='second-verify-panel';p.textContent='使用原设备扫码';document.body.append(p);
 setTimeout(()=>{p.remove();history.pushState({},'', '/creator-micro/content/manage');},700);
 }else{history.pushState({},'', '/creator-micro/content/manage');}};
</script>'''

@unittest.skipUnless(os.environ.get('P009_BROWSER_TEST')=='1','explicit real Chrome fixture run')
class OneClickPortTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.p=sync_playwright().start();self.addCleanup(self.p.stop)
        self.browser=self.p.chromium.launch(channel='chrome',headless=True);self.addCleanup(self.browser.close)
        self.context=self.browser.new_context();self.addCleanup(self.context.close)
        self.context.route('**/*',lambda r:r.fulfill(content_type='text/html',body=HTML))
        owner=self
        class LocalSession:
            def __init__(self):self._context=owner.context;self._page=self._context.new_page();self.disposed=False
            def _dispose(self):self.disposed=True
            def execute(self,action,cancel,progress):return action(self,cancel,progress)
        class LocalPublisher(DouyinBrowserPublisher):
            def _check_account(self,session,account,cancel,progress):return account
        self.session=LocalSession();self.publisher=LocalPublisher(self.session);self.cancel=threading.Event()
        self.account={'platform_user_id':'123','verified':True};self.content=validate_content({'title':'本地测试','body':'第一段\n第二段','tags':'#工厂'})
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/'fixture.mp4';self.file.write_bytes(b'local-fixture-only')
    def prepare(self):return self.publisher.prepare(str(self.file),self.content,self.account,self.cancel,lambda _:None,scheduled_for='2026-09-12T10:00:00+08:00')
    def test_ported_form_replaces_old_fields_and_reads_official_topic_entity(self):
        receipt=self.prepare();page=self.publisher.pending['page']
        self.assertTrue(receipt['preflight']['detail_confirmed'])
        self.assertEqual(receipt['preflight']['topics_confirmed'],['工厂'])
        self.assertEqual(page.evaluate('window.finalClicks'),0)
        page.locator('input[placeholder="填写作品标题"]').fill('用户手动修改')
        with self.assertRaisesRegex(RuntimeError,'标题回读'):
            self.publisher.commit(receipt,self.account,self.cancel,lambda _:None,lambda:None)
        self.assertEqual(page.evaluate('window.finalClicks'),0)

    def test_prepare_reuses_the_single_publisher_page(self):
        pages_before=len(self.context.pages)
        self.prepare()
        self.assertIs(self.publisher.pending['page'],self.session._page)
        self.assertEqual(len(self.context.pages),pages_before)
    def test_verification_keeps_same_page_and_does_not_reclick_publish(self):
        receipt=self.prepare();page=self.publisher.pending['page'];page.evaluate('window.showVerification=true')
        progress=[];intent=[]
        result=self.publisher.commit(receipt,self.account,self.cancel,progress.append,lambda:intent.append(True))
        self.assertEqual(intent,[True]);self.assertEqual(page.evaluate('window.finalClicks'),1)
        self.assertTrue(any('安全验证' in s for s in progress))
        self.assertTrue(result['accepted'])
        self.assertEqual(result['verified_by'],'content_manage_navigation')
        self.assertTrue(self.session.disposed)
        self.assertIsNone(self.publisher.pending)
    def test_plain_hash_text_is_not_a_platform_topic(self):
        page=self.context.new_page();page.goto('https://creator.douyin.com/local-fixture')
        page.locator('#form').evaluate('(e)=>e.hidden=false')
        page.evaluate('window.topicEntity=false')
        form=OneClickForm(self.content,self.cancel)
        with self.assertRaisesRegex(RuntimeError,'实体'):
            form.fill(page)
        self.assertEqual(page.evaluate('window.finalClicks'),0)
