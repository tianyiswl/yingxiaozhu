"""Publication coordinator: freeze, claim, upload, submit once, then query."""
from pathlib import Path
from .models import file_hash,atomic_json,now
from .core import Runner,Cancelled
from .generation import export_video
from .providers.douyin_publisher import APIError

class PublicationService:
    def __init__(self,repository,publisher):self.repository=repository;self.publisher=publisher
    def prepare(self,attempt_id,account):
        if account.get('platform')!='douyin' or not account.get('verified') or not account.get('platform_user_id'):
            raise ValueError('请先核对并绑定一个抖音账号')
        job=self.repository.get_job(attempt_id)
        if job['status']!='SUCCEEDED' or not job['result']:raise ValueError('请先生成并预览成功成片')
        content=job['request']['content']
        text='\n'.join(content.get(k,'').strip() for k in ('title','body','tags') if content.get(k,'').strip())
        if not text or len(text)>1000:raise ValueError('合并后的发布文字须为1—1000字，不会自动截断')
        snapshot={'video_path':job['result'],'video_sha256':file_hash(job['result']),'text':text,'content':content,'created_at':now()}
        record=self.repository.create_publication(attempt_id,account,snapshot)
        self._write(record);return record
    def _write(self,record):
        atomic_json(self.repository.root/'publications'/record['publication_id']/'record.json',record)
    def _verify_account(self,record,cancel):
        fixed=self.repository.setting('fixed_account',{})
        if fixed.get('platform_user_id')!=record['account']['platform_user_id'] or fixed.get('platform')!='douyin':
            raise ValueError('当前固定账号已改变，不能改投旧任务')
        actual=self.publisher.identity(cancel)
        if actual.get('platform_user_id')!=record['account']['platform_user_id']:raise ValueError('实际授权账号与固定账号不一致')
    def submit(self,ident,cancel,progress):
        record=self.repository.get_publication(ident)
        self.repository.claim_publication(ident)
        submitted=False;receipt={}
        try:
            Runner(cancel).check()
            snap=record['snapshot']
            if file_hash(snap['video_path'])!=snap['video_sha256']:raise ValueError('成片已改变，禁止提交')
            self._verify_account(record,cancel)
            staged=self.repository.root/'publications'/ident/'upload.mp4'
            export_video(snap['video_path'],staged,cancel,progress)
            if file_hash(staged)!=snap['video_sha256']:raise ValueError('复制期间成片已改变，禁止上传')
            progress('上传到已核对的抖音账号（尚未发布）')
            video_id=self.publisher.upload(str(staged),record['account'],cancel)
            receipt={'upload_video_id':video_id,'uploaded_at':now()}
            Runner(cancel).check()
            self.repository.set_publication(ident,'SUBMITTING',receipt);self._write(self.repository.get_publication(ident))
            submitted=True;progress('正在提交；中断后将先查询结果')
            result=self.publisher.submit(video_id,snap['text'],record['account'],cancel)
            receipt.update(result,submitted_at=now())
            self.repository.set_publication(ident,'ACCEPTED',receipt)
        except Exception as exc:
            # Explicit validation/permission rejection is safe to report failed. Internal/network errors remain unknown.
            definite=isinstance(exc,APIError) and exc.code in {'2100005','210005','2190007','2114006','2114007','28001003','28001008','28001014','28001018','28001019','28001007'}
            status='UNKNOWN' if submitted and not definite else ('CANCELLED' if isinstance(exc,Cancelled) else 'FAILED')
            receipt.update(error=str(exc),error_at=now())
            self.repository.set_publication(ident,status,receipt)
        final=self.repository.get_publication(ident);self._write(final);return final
    def query(self,ident,cancel):
        record=self.repository.get_publication(ident);item=record['receipt'].get('item_id')
        if not item:raise ValueError('尚无作品ID，请到抖音核对后补录；禁止直接重发')
        return self._query(record,item,cancel,False)
    def reconcile(self,ident,item_id,cancel):
        record=self.repository.get_publication(ident)
        if record['status']!='UNKNOWN':raise ValueError('仅结果不明的记录可补录作品ID')
        return self._query(record,item_id.strip(),cancel,True)
    def _query(self,record,item,cancel,reconcile):
        self._verify_account(record,cancel)
        result=self.publisher.query(item,record['account'],cancel)
        result['content_matches']=result.get('title')==record['snapshot']['text']
        result['queried_at']=now()
        if reconcile and not result['content_matches']:raise ValueError('查询作品文字与本次快照不一致，未关联')
        receipt=dict(record['receipt'],query=result,item_id=item)
        status='ACCEPTED' if reconcile else record['status']
        self.repository.set_publication(record['publication_id'],status,receipt)
        final=self.repository.get_publication(record['publication_id']);self._write(final);return final
