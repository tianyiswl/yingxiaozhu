"""Durable browser publication: preflight first, explicit single commit later."""
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .models import file_hash, atomic_json, now
from .core import Runner, Cancelled
from .generation import export_video
from .providers.douyin_browser import validate_content

SHANGHAI=ZoneInfo('Asia/Shanghai')


class BrowserPublicationService:
    def __init__(self, repository, publisher):
        self.repo=repository;self.publisher=publisher
    def _save(self, ident):
        record=self.repo.get_publication(ident)
        atomic_json(self.repo.root/'publications'/ident/'record.json',record)
        return record
    def _account(self, expected=None):
        account=self.repo.setting('fixed_account',{})
        if account.get('provider_id')!='douyin_browser' or not account.get('verified'):
            raise ValueError('请先登录并核对抖音账号')
        if expected and account.get('platform_user_id')!=expected.get('platform_user_id'):
            raise ValueError('固定账号已改变，不能改投旧任务')
        return account
    def prepare(self, attempt_id, cancel, progress, scheduled_for=None):
        account=self._account();job=self.repo.get_job(attempt_id)
        if job['status']!='SUCCEEDED' or not job['result']:raise ValueError('请先生成并预览成片')
        content=validate_content(job['request']['content'])
        snapshot={'provider_id':'douyin_browser','video_path':job['result'],'video_sha256':file_hash(job['result']),
                  'content':content,'text':content['title']+'\n'+content['body'],'created_at':now()}
        record=self.repo.create_publication(attempt_id,account,snapshot);ident=record['publication_id'];receipt={}
        self.repo.set_publication(ident,'PREPARING');self._save(ident)
        try:
            Runner(cancel).check()
            staged=self.repo.root/'publications'/ident/'upload.mp4'
            export_video(job['result'],staged,cancel,progress)
            if file_hash(staged)!=snapshot['video_sha256']:raise ValueError('成片在复制期间改变，已停止上传')
            receipt={'staged_path':str(staged),'staged_sha256':snapshot['video_sha256']}
            receipt.update(self.publisher.prepare(str(staged),content,account,cancel,progress,scheduled_for=scheduled_for))
            self.repo.set_publication(ident,'PREPARED',dict(receipt,prepared_at=now()))
        except Exception as exc:
            self.repo.set_publication(ident,'CANCELLED' if isinstance(exc,Cancelled) else 'FAILED',dict(receipt,error=str(exc)))
        return self._save(ident)
    def submit(self, ident, cancel, progress, before_submit=None):
        # Atomic claim; exceptions before the click callback cannot become UNKNOWN.
        self.repo.claim_prepared_publication(ident)
        record=self.repo.get_publication(ident);receipt=dict(record['receipt']);clicked=False
        def before_click():
            nonlocal clicked
            Runner(cancel).check();self._account(record['account'])
            if before_submit is not None:before_submit()
            receipt['click_intent_at']=now()
            self.repo.set_publication(ident,'SUBMITTING',receipt);self._save(ident)
            clicked=True
        try:
            self._account(record['account']);Runner(cancel).check()
            if file_hash(receipt['staged_path'])!=record['snapshot']['video_sha256']:raise ValueError('冻结成片已改变，禁止提交')
            result=self.publisher.commit(receipt,record['account'],cancel,progress,before_click)
            receipt.update(result,checked_at=now())
            self.repo.set_publication(ident,'ACCEPTED' if result.get('accepted') else 'UNKNOWN',receipt)
        except Exception as exc:
            status='UNKNOWN' if clicked else ('CANCELLED' if isinstance(exc,Cancelled) else 'FAILED')
            self.repo.set_publication(ident,status,dict(receipt,error=str(exc),checked_at=now()))
        return self._save(ident)
    def query(self, ident, cancel, progress=lambda _:None):
        record=self.repo.get_publication(ident);self._account(record['account'])
        result=self.publisher.query(record['receipt'],record['account'],cancel,progress)
        receipt=dict(record['receipt'],query=result)
        status=record['status']
        if result.get('accepted') and result.get('item_id'):
            receipt['item_id']=result['item_id'];status='ACCEPTED'
        self.repo.set_publication(ident,status,receipt);return self._save(ident)
    def reconcile(self, ident, item_id, cancel, progress=lambda _:None):
        record=self.repo.get_publication(ident)
        if record['status']!='UNKNOWN':raise ValueError('仅结果不明的记录可以补录作品ID')
        probe=dict(record['receipt'],item_id=item_id)
        result=self.publisher.query(probe,record['account'],cancel,progress)
        if not result.get('accepted'):raise ValueError('未在当前账号作品列表核对到该ID与标题，未关联')
        self.repo.set_publication(ident,'ACCEPTED',dict(record['receipt'],item_id=item_id,query=result));return self._save(ident)
    def cancel_prepared(self, ident, cancel, progress):
        record=self.repo.get_publication(ident)
        if record['status'] not in ('PREPARED','INTERRUPTED','FAILED') or record['receipt'].get('click_intent_at'):raise ValueError('已进入提交的记录不能取消或重发，请先查询')
        self.publisher.discard(record['receipt'],cancel,progress)
        self.repo.set_publication(ident,'CANCELLED',dict(record['receipt'],cancelled_at=now()))
        return self._save(ident)
