"""One serial step of a finite batch. Simulation never calls a publisher."""
import time
import threading
from .core import Cancelled,Runner

class BatchCancel:
    def __init__(self,store,ident,stop):self.store=store;self.ident=ident;self.stop=stop
    def is_set(self):return self.stop.is_set() or self.store.batch(self.ident)['status']!='ACTIVE'

class BatchEngine:
    def __init__(self,store,workflow,publisher=None,simulation=False,clock=time.time,stop=None,progress=lambda _:None):
        self.store=store;self.repo=store.repo;self.workflow=workflow;self.publisher=publisher
        self.simulation=simulation;self.clock=clock;self.stop=stop or threading.Event();self.progress=progress
    def _finish(self,item,state):
        with self.repo.connect() as db:
            db.execute('UPDATE daily_items SET state=?,reservation=0,error=? WHERE id=?',(state,'',item['id']))
            db.execute('UPDATE library_contents SET used=1 WHERE id=?',(item['content_id'],))
        self.store.complete_if_done(item['batch_id'])
    def _hold(self,batch,message):self.store.set_batch(batch['id'],'NEEDS_ATTENTION',message)
    def _eligible(self,batch,item):
        now=self.clock();earliest=item['due_at']
        if batch['spec'].get('immediate') and item['ordinal']>0:
            first=self.store.items(batch['id'])[0]
            if first['state'] not in ('ACCEPTED','SIMULATED','GENERATED'):return False
        for other in self.store.batches():
            if other['mode']!=batch['mode'] or other['account'].get('platform_user_id')!=batch['account'].get('platform_user_id'):continue
            for old in self.store.items(other['id']):
                if old['state'] in ('UNKNOWN','SUBMITTING'):return False
        if earliest>batch['deadline']:
            self._hold(batch,'按原间隔顺延将超出任务日期，请取消未执行项后重新排期');return False
        return now>=earliest or (batch['mode']=='publish' and 7800<=earliest-now<=13*86400)
    def step(self):
        if self.stop.is_set():return False
        active=[b for b in self.store.batches() if b['status']=='ACTIVE' and (b['mode']=='simulate')==self.simulation]
        active.sort(key=lambda b:b['created_at'])
        for batch in active:
            if self.clock()>batch['deadline']:
                self._hold(batch,'任务日期已过，未补发；请重新安排剩余内容');continue
            items=self.store.items(batch['id']);cancel=BatchCancel(self.store,batch['id'],self.stop)
            # A ready due video takes precedence over rendering the next one.
            ready=next((i for i in items if i['state']=='READY' and (batch['mode']=='generate' or self._eligible(batch,i))),None)
            if ready and (batch['mode']=='generate' or self._eligible(batch,ready)):
                self._publish(batch,ready,cancel);return True
            queued=next((i for i in items if i['state']=='QUEUED'),None)
            if queued:
                self._generate(batch,queued,cancel);return True
            self.store.complete_if_done(batch['id'])
            if any(i['state']=='FAILED' for i in items) and not any(i['state'] in ('QUEUED','READY') for i in items):self._hold(batch,'部分视频制作失败，请查看任务详情')
        return False
    def _generate(self,batch,item,cancel):
        self.store.update_item(item['id'],state='GENERATING',tries=item['tries']+1,error='')
        try:
            Runner(cancel).check()
            job=self.workflow.enqueue(item['request']);self.store.update_item(item['id'],attempt_id=job['attempt_id'])
            job=self.workflow.run([job['attempt_id']],cancel,self.progress)[0]
            if job['status']!='SUCCEEDED':raise RuntimeError(job.get('error') or '制作未完成')
            self.store.update_item(item['id'],state='READY',result=job['result'])
            if cancel.is_set() and self.store.batch(batch['id'])['status']=='CANCELLED':self.store.cancel_batch(batch['id'])
        except Exception as exc:
            status=self.store.batch(batch['id'])['status']
            if status=='CANCELLED':self.store.update_item(item['id'],state='CANCELLED',reservation=0,error=str(exc))
            elif cancel.is_set():self.store.update_item(item['id'],state='QUEUED',error='制作已暂停')
            elif item['tries']<1:self.store.update_item(item['id'],state='QUEUED',error='制作失败，将再试一次：'+str(exc))
            else:
                self.store.update_item(item['id'],state='FAILED',error=str(exc))
                # Isolate this content; the remaining videos can still be made.
                self.progress('已隔离失败文案，其余视频继续处理')
    def _publish(self,batch,item,cancel):
        if batch['mode']=='publish' and item['request'].get('schedule_policy')!='hybrid_v1':
            self._hold(batch,'发布规则已更新，请取消旧测试任务后重新创建')
            return
        if batch['mode']=='generate':self._finish(item,'GENERATED');return
        if batch['mode']=='simulate':
            Runner(cancel).check();self.store.update_item(item['id'],submitted_at=self.clock());self._finish(item,'SIMULATED');return
        self.store.update_item(item['id'],state='PREPARING')
        try:
            Runner(cancel).check()
            if self.publisher is None:raise ValueError('发布连接尚未就绪，请先登录')
            service=self.publisher(cancel,self.progress) if callable(self.publisher) else self.publisher
            service._account(batch['account'])
            from datetime import datetime
            from .batch_planner import SHANGHAI
            target=datetime.fromtimestamp(item['due_at'],SHANGHAI).isoformat() if item['due_at']-self.clock()>=7800 else None
            if item['request'].get('schedule_policy')=='hybrid_v1':
                pub=service.prepare(item['attempt_id'],cancel,self.progress,scheduled_for=target)
            else:pub=service.prepare(item['attempt_id'],cancel,self.progress)
            self.store.update_item(item['id'],publication_id=pub['publication_id'])
            if pub['status']!='PREPARED':raise ValueError(pub['receipt'].get('error','上传准备未完成'))
            def guard():
                Runner(cancel).check();service._account(batch['account'])
                if self.clock()>batch['deadline'] or not self._eligible(batch,self.store.item(item['id'])):raise ValueError('已超出本批排期或发布间隔，请重新安排')
                # Durable conservative intent precedes the external click. Never replay on crash.
                self.store.update_item(item['id'],state='SUBMITTING',submitted_at=self.clock())
            pub=service.submit(pub['publication_id'],cancel,self.progress,before_submit=guard)
            if pub['status']=='ACCEPTED':
                self._finish(item,'ACCEPTED')
                if batch['spec'].get('immediate') and item['ordinal']==0:
                    offset=self.clock()-item['due_at']
                    with self.repo.connect() as db:
                        db.execute("UPDATE daily_items SET due_at=due_at+? WHERE batch_id=? AND ordinal>0 AND submitted_at IS NULL",(offset,batch['id']))

            elif pub['status']=='UNKNOWN':
                self.store.update_item(item['id'],state='UNKNOWN',error='已尝试提交，结果待核对；不会重发')
                self._hold(batch,'发布结果待核对，已暂停该账号后续提交')
            else:raise ValueError(pub['receipt'].get('error','发布未完成'))
        except Exception as exc:
            current=self.store.item(item['id'])
            if '定时余量不足' in str(exc) and not current['submitted_at']:
                self.store.update_item(item['id'],state='READY',publication_id=None,error='本机等待，到点发布')
                return
            if current['submitted_at']:
                self.store.update_item(item['id'],state='UNKNOWN',error=str(exc));self._hold(batch,'提交结果不明，请核对原记录')
            else:
                status=self.store.batch(batch['id'])['status']
                self.store.update_item(item['id'],state='CANCELLED' if status=='CANCELLED' else 'FAILED',reservation=0 if status=='CANCELLED' else 1,error=str(exc))
                if status not in ('CANCELLED','PAUSED'):self._hold(batch,str(exc))
