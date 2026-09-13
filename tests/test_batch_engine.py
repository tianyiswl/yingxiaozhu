import threading
from pathlib import Path
import test_daily_batch
import unittest
from factory_video_tool.batch_planner import BatchPlanner
from factory_video_tool.batch_engine import BatchEngine

class FakeWorkflow:
    def __init__(self,repo):self.repo=repo;self.runs=0
    def enqueue(self,request):return self.repo.create_job(request)
    def run(self,ids,cancel,progress):
        self.runs+=1
        for ident in ids:
            p=self.repo.root/(ident+'.mp4');p.write_bytes(b'local test')
            self.repo.set_job(ident,'SUCCEEDED',str(p))
        return [self.repo.get_job(i) for i in ids]

class EngineTests(unittest.TestCase):
    setUp=test_daily_batch.DailyBatchTests.setUp
    seed=test_daily_batch.DailyBatchTests.seed
    content=test_daily_batch.DailyBatchTests.content
    def setup_engine(self):
        self.seed();self.batch=BatchPlanner(self.store).create(self.spec,'engine',self.now)
        self.clock=[self.now];self.flow=FakeWorkflow(self.repo)
        self.engine=BatchEngine(self.store,self.flow,simulation=True,clock=lambda:self.clock[0])
    def test_generate_due_and_real_interval(self):
        self.setup_engine();self.engine.step();self.engine.step()
        items=self.store.items();self.assertEqual([i['state'] for i in items],['READY','READY'])
        self.engine.step();self.assertFalse(self.store.items()[0]['submitted_at'])
        self.clock[0]=items[0]['due_at']+4000;self.engine.step()
        self.assertEqual(self.store.items()[0]['state'],'SIMULATED')
        self.engine.step()
        self.assertEqual(self.store.items()[1]['state'],'SIMULATED')
        self.clock[0]+=3600;self.engine.step()
        self.assertEqual(self.store.batch(self.batch['id'])['status'],'COMPLETED')
        self.assertEqual(self.repo.publications(),[]);self.assertEqual(self.flow.runs,2)
    def test_pause_cancel_and_mode_isolation(self):
        self.setup_engine();self.store.pause_batch(self.batch['id']);self.engine.step()
        self.assertEqual(self.flow.runs,0)
        self.store.set_batch(self.batch['id'],'ACTIVE')
        BatchEngine(self.store,self.flow,simulation=False).step();self.assertEqual(self.flow.runs,0)
        self.store.cancel_batch(self.batch['id']);self.engine.step();self.assertEqual(self.flow.runs,0)
    def test_expired_scope_never_submits(self):
        self.setup_engine();self.engine.step();self.clock[0]=self.batch['deadline']+1;self.engine.step()
        self.assertEqual(self.store.batch(self.batch['id'])['status'],'NEEDS_ATTENTION')
        self.assertFalse(any(i['submitted_at'] for i in self.store.items()))
    def test_recovery_does_not_replay_unknown(self):
        self.setup_engine();item=self.store.items()[0]
        self.store.update_item(item['id'],state='SUBMITTING',submitted_at=self.now)
        self.store.recover();self.engine.step()
        self.assertEqual(self.store.item(item['id'])['state'],'UNKNOWN')
        self.assertEqual(self.flow.runs,0)

    def test_accepted_platform_schedule_does_not_delay_the_next_upload(self):
        self.seed();account={'provider_id':'douyin_browser','platform_user_id':'fixture','verified':True}
        self.repo.save_setting('fixed_account',account)
        old=BatchPlanner(self.store).create(dict(self.spec,mode='publish',count=1,immediate=True),'old',self.now)
        old_item=self.store.items(old['id'])[0]
        publication=self.repo.create_publication(old_item['attempt_id'] or 'old-attempt',account,{'video_sha256':'old'})
        self.repo.set_publication(publication['publication_id'],'ACCEPTED',{'scheduled_for':'2026-09-12T09:00:00+08:00'})
        self.store.update_item(old_item['id'],state='ACCEPTED',publication_id=publication['publication_id'],submitted_at=self.now,reservation=0)
        new=BatchPlanner(self.store).create(dict(self.spec,mode='publish',count=1,immediate=True),'new',self.now)
        current=self.store.items(new['id'])[0]
        engine=BatchEngine(self.store,FakeWorkflow(self.repo),simulation=False,clock=lambda:self.now)
        self.assertTrue(engine._eligible(new,current))

    def test_live_unknown_freezes_account_and_never_repeats(self):
        self.seed();self.repo.save_setting('fixed_account',{'provider_id':'douyin_browser','platform_user_id':'fixture','verified':True})
        batch=BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'live',self.now)
        flow=FakeWorkflow(self.repo);clock=[self.now];calls=[]
        class Service:
            def _account(s,expected):return expected
            def prepare(s,*args,scheduled_for=None):return {'publication_id':'fake','status':'PREPARED'}
            def submit(s,ident,cancel,progress,before_submit):
                before_submit();calls.append(ident);return {'status':'UNKNOWN'}
        engine=BatchEngine(self.store,flow,Service(),clock=lambda:clock[0])
        engine.step();engine.step();clock[0]=self.store.items()[0]['due_at'];engine.step()
        clock[0]+=7200
        for _ in range(3):engine.step()
        self.assertEqual(calls,['fake']);self.assertEqual(self.store.items()[0]['state'],'UNKNOWN')
        self.assertEqual(self.store.batch(batch['id'])['status'],'NEEDS_ATTENTION')
        with self.assertRaisesRegex(ValueError,'核对'):self.store.resume_batch(batch['id'],self.now)
    def test_pause_during_generation_retains_ready_video(self):
        self.setup_engine();original=self.flow.run
        def run(*args):
            result=original(*args);self.store.pause_batch(self.batch['id']);return result
        self.flow.run=run;self.engine.step()
        self.assertEqual(self.store.items()[0]['state'],'READY')
        self.clock[0]=self.store.items()[0]['due_at'];self.engine.step();self.assertFalse(self.store.items()[0]['submitted_at'])
    def test_frozen_workflow_does_not_pick_new_assets(self):
        self.setup_engine();from factory_video_tool.workflow import Workflow
        old=self.store.items()[0]['request'];new=Path(old['material_root'])/'new.mp4';new.write_bytes(b'new')
        job=Workflow(self.repo).enqueue(old)
        self.assertEqual(len(job['request']['material_snapshot']),1)
        Path(old['material_snapshot'][0]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'改变'):Workflow(self.repo).enqueue(old)
    def test_cancel_during_generation_releases_content(self):
        self.setup_engine();original=self.flow.run
        def run(*args):
            result=original(*args);self.store.cancel_batch(self.batch['id']);return result
        self.flow.run=run;self.engine.step()
        self.assertTrue(all(i['state']=='CANCELLED' for i in self.store.items()))
        self.assertEqual(len(self.store.available_contents()),5)
    def test_generation_retries_bounded_and_other_content_continues(self):
        self.setup_engine();original=self.flow.run
        def run(*args):
            if self.flow.runs<2:
                self.flow.runs+=1;raise ValueError('bad input')
            return original(*args)
        self.flow.run=run
        for _ in range(5):self.engine.step()
        self.assertEqual(self.flow.runs,3)
        self.assertEqual([i['state'] for i in self.store.items()],['FAILED','READY'])
        self.clock[0]=self.store.items()[1]['due_at'];self.engine.step();self.engine.step()
        self.assertEqual(self.store.batch(self.batch['id'])['status'],'NEEDS_ATTENTION')
    def test_preclick_pause_prevents_submission(self):
        self.seed();self.repo.save_setting('fixed_account',{'provider_id':'douyin_browser','platform_user_id':'fixture','verified':True})
        batch=BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'live',self.now)
        flow=FakeWorkflow(self.repo);clock=[self.now];calls=[];store=self.store
        class Service:
            def _account(s,expected):return expected
            def prepare(s,*args,scheduled_for=None):return {'publication_id':'fake','status':'PREPARED'}
            def submit(s,ident,cancel,progress,before_submit):
                store.pause_batch(batch['id']);before_submit();calls.append(ident)
        engine=BatchEngine(self.store,flow,Service(),clock=lambda:clock[0]);engine.step()
        clock[0]=self.store.items()[0]['due_at'];engine.step()
        self.assertEqual(calls,[]);self.assertFalse(self.store.items()[0]['submitted_at'])
        self.assertEqual(self.store.batch(batch['id'])['status'],'PAUSED')
    def test_account_change_stops_before_upload(self):
        self.seed();self.repo.save_setting('fixed_account',{'provider_id':'douyin_browser','platform_user_id':'fixture','verified':True})
        batch=BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'live',self.now)
        from factory_video_tool.browser_publishing import BrowserPublicationService
        service=BrowserPublicationService(self.repo,None)
        flow=FakeWorkflow(self.repo);clock=[self.now];engine=BatchEngine(self.store,flow,service,clock=lambda:clock[0]);engine.step()
        self.repo.save_setting('fixed_account',{'provider_id':'douyin_browser','platform_user_id':'other','verified':True})
        clock[0]=self.store.items()[0]['due_at'];engine.step()
        self.assertEqual(self.repo.publications(),[]);self.assertEqual(self.store.batch(batch['id'])['status'],'NEEDS_ATTENTION')
    def test_recovery_finds_publication_before_item_link_was_saved(self):
        self.setup_engine();self.engine.step();i=self.store.items()[0]
        pub=self.repo.create_publication(i['attempt_id'],{},{});self.repo.set_publication(pub['publication_id'],'SUBMITTING',{'click_intent_at':'fixture'})
        self.store.update_item(i['id'],state='PREPARING');self.store.recover()
        self.assertEqual(self.store.item(i['id'])['state'],'UNKNOWN')
        self.assertEqual(self.store.item(i['id'])['publication_id'],pub['publication_id'])
