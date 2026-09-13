import json
import random
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from factory_video_tool.repository import Repository
from factory_video_tool.batch_store import BatchStore
from factory_video_tool.library import Library
from factory_video_tool.batch_planner import BatchPlanner

class DailyBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Repository(self.tmp.name);self.store=BatchStore(self.repo);self.lib=Library(self.store)
        self.now=datetime(2026,9,11,0,0,tzinfo=timezone.utc).timestamp()
        self.settings={'voice':'Tingting','rate':1.0,'width':720,'font':'PingFang SC','bgm':'','bgm_volume':.15}
        self.spec={'count':2,'days':1,'start':'2026-09-11T10:00:00+08:00','interval':60,'mode':'simulate','settings':self.settings}
    def content(self,i):
        return {'title':'工厂镜头'+str(i),'voice_text':'这是工厂里的真实生产镜头。'+str(i),'body':'车间记录','tags':''}
    def seed(self,n=5):
        # Library import decoding has a separate real-media test. This fixture is only for planning.
        asset=Path(self.tmp.name)/'library/assets/test.mp4';asset.parent.mkdir(parents=True,exist_ok=True);asset.write_bytes(b'fixture')
        from factory_video_tool.models import file_hash
        with self.repo.connect() as db:db.execute('INSERT INTO library_materials(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',(file_hash(asset),str(asset),'test',1,self.now))
        for i in range(n):self.lib.add_content(self.content(i))
    def seed_bgm(self,n=3):
        from factory_video_tool.models import file_hash
        records=[]
        for index in range(n):
            path=Path(self.tmp.name)/'library/bgm'/('music-%s.mp3'%index);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(('music-%s'%index).encode())
            record={'id':file_hash(path),'path':str(path),'name':path.name,'duration':10,'created_at':self.now+index};records.append(record)
            with self.repo.connect() as db:db.execute('INSERT INTO library_bgm(id,path,name,duration,created_at) VALUES(?,?,?,?,?)',(record['id'],record['path'],record['name'],record['duration'],record['created_at']))
        return records
    def test_document_import_appends_and_deduplicates(self):
        p=Path(self.tmp.name)/'a.txt';p.write_text('工厂记录\n这是一条口播。',encoding='utf-8')
        a=self.lib.import_documents([str(p)],threading.Event(),lambda _:None)
        p2=Path(self.tmp.name)/'b.txt';p2.write_text('另一个标题\n另一条口播。',encoding='utf-8')
        self.lib.import_documents([str(p),str(p2)],threading.Event(),lambda _:None)
        self.assertEqual(a['added'],1);self.assertEqual(len(self.store.contents()),2)
        self.assertEqual(len(BatchStore(Repository(self.tmp.name)).contents()),2)
    def test_batch_is_atomic_and_click_idempotent(self):
        self.seed();planner=BatchPlanner(self.store)
        b=planner.create(self.spec,'click',self.now)
        self.assertEqual(b['id'],planner.create(self.spec,'click',self.now)['id'])
        items=self.store.items(b['id']);self.assertEqual(len(items),2)
        self.assertEqual(items[1]['due_at']-items[0]['due_at'],3600)
        self.assertEqual(len(self.store.batches()),1)
        self.assertEqual(len(self.store.available_contents()),3)
    def test_avoidance_default_is_frozen_for_new_batch(self):
        self.seed();b=BatchPlanner(self.store).create(self.spec,'avoidance',self.now)
        self.assertTrue(self.store.items(b['id'])[0]['request']['avoid_recent_clips'])
        self.assertEqual(self.store.items(b['id'])[0]['request']['schedule_policy'],'hybrid_v1')
        self.assertNotIn('platform_schedule_delay_hours',self.store.items(b['id'])[0]['request'])
        self.settings['avoid_recent_clips']=False
        self.assertTrue(self.store.items(b['id'])[0]['request']['avoid_recent_clips'])

    def test_background_music_modes_are_frozen_per_video(self):
        self.seed();music=self.seed_bgm();planner=BatchPlanner(self.store)
        specified=dict(self.spec,settings=dict(self.settings,bgm_enabled=True,bgm_mode='specified',bgm=music[1]['path']))
        batch=planner.create(specified,'specified',self.now);items=self.store.items(batch['id'])
        self.assertEqual({item['request']['bgm'] for item in items},{music[1]['path']})
        self.store.cancel_batch(batch['id'])
        sequence=dict(self.spec,settings=dict(self.settings,bgm_enabled=True,bgm_mode='sequence',bgm=''))
        batch=planner.create(sequence,'sequence',self.now);items=self.store.items(batch['id'])
        self.assertEqual([item['request']['bgm'] for item in items],[music[0]['path'],music[1]['path']])
        self.store.cancel_batch(batch['id'])
        random_mode=dict(self.spec,settings=dict(self.settings,bgm_enabled=True,bgm_mode='random',bgm=''))
        batch=planner.create(random_mode,'random',self.now);items=self.store.items(batch['id'])
        self.assertTrue(all(item['request']['bgm'] in {entry['path'] for entry in music} for item in items))
        self.assertTrue(all(item['request']['bgm_sha256'] in {entry['id'] for entry in music} for item in items))

    def test_content_selection_mode_is_frozen_as_sequence_or_random(self):
        self.seed(6);planner=BatchPlanner(self.store);available=[entry['id'] for entry in self.store.available_contents()]
        sequence=planner.create(dict(self.spec,content_selection_mode='sequence'),'content-sequence',self.now)
        self.assertEqual([item['content_id'] for item in self.store.items(sequence['id'])],available[:2])
        self.store.cancel_batch(sequence['id'])
        random_batch=planner.create(dict(self.spec,content_selection_mode='random'),'content-random',self.now)
        expected=random.Random('content:content-random').sample(available,2)
        self.assertEqual([item['content_id'] for item in self.store.items(random_batch['id'])],expected)
        self.assertEqual(self.store.batch(random_batch['id'])['spec']['content_selection_mode'],'random')

    def test_immediate_batch_has_no_selected_local_time_and_is_due_now(self):
        self.seed();spec=dict(self.spec,immediate=True);spec.pop('start')
        batch=BatchPlanner(self.store).create(spec,'immediate',self.now)
        self.assertTrue(batch['spec']['immediate'])
        self.assertEqual(datetime.fromisoformat(batch['spec']['start']).timestamp(),self.now)
        self.assertEqual(self.store.items(batch['id'])[0]['due_at'],self.now)
    def test_insufficient_contents_does_not_partially_reserve(self):
        self.seed(1)
        with self.assertRaisesRegex(ValueError,'文案'):BatchPlanner(self.store).create(self.spec,'a',self.now)
        self.assertEqual(self.store.batches(),[]);self.assertEqual(len(self.store.available_contents()),1)
    def test_daily_times_can_continue_after_midnight(self):
        self.seed(6);spec=dict(self.spec,days=2)
        b=BatchPlanner(self.store).create(spec,'a',self.now);items=self.store.items(b['id'])
        self.assertEqual(items[2]['due_at']-items[0]['due_at'],86400)
        overnight=BatchPlanner(self.store).create(dict(self.spec,start='2026-09-11T23:30:00+08:00'),'b',self.now)
        overnight_items=self.store.items(overnight['id'])
        self.assertEqual(overnight_items[1]['due_at']-overnight_items[0]['due_at'],3600)
        self.assertGreaterEqual(overnight['deadline'],overnight_items[-1]['due_at'])
    def test_collisions_and_real_account_requirement(self):
        self.seed();planner=BatchPlanner(self.store);planner.create(self.spec,'a',self.now)
        with self.assertRaisesRegex(ValueError,'冲突'):planner.create(self.spec,'b',self.now)
        with self.assertRaisesRegex(ValueError,'账号'):planner.create(dict(self.spec,mode='publish'),'c',self.now)
    def test_immediate_publish_keeps_its_own_interval_after_existing_platform_schedule(self):
        self.seed();account={'platform_user_id':'123','provider_id':'douyin_browser','verified':True}
        self.repo.save_setting('fixed_account',account)
        first=BatchPlanner(self.store).create(dict(self.spec,mode='publish',immediate=True,count=1),'first',self.now)
        old_item=self.store.items(first['id'])[0]
        publication=self.repo.create_publication(old_item['attempt_id'] or 'old-attempt',account,{'video_sha256':'old'})
        self.repo.set_publication(publication['publication_id'],'ACCEPTED',{'scheduled_for':'2026-09-12T09:00:00+08:00'})
        self.store.update_item(old_item['id'],state='ACCEPTED',publication_id=publication['publication_id'],reservation=0)
        later=BatchPlanner(self.store).create(dict(self.spec,mode='publish',immediate=True),'second',self.now)
        planned=self.store.items(later['id'])
        self.assertEqual([i['due_at'] for i in planned],[self.now,self.now+3600])
        self.assertEqual(planned[0]['request']['schedule_policy'],'hybrid_v1')
        self.assertNotIn('platform_schedule_not_before',planned[0]['request'])

    def test_unresolved_submission_blocks_new_publish_batch_with_a_clear_message(self):
        self.seed();self.repo.save_setting('fixed_account',{'platform_user_id':'123','provider_id':'douyin_browser','verified':True})
        batch=BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'first',self.now)
        self.store.update_item(self.store.items(batch['id'])[0]['id'],state='UNKNOWN')
        with self.assertRaisesRegex(ValueError,'待核对'):
            BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'second',self.now)

    def test_saved_account_can_plan_before_background_revalidates_it(self):
        self.seed()
        self.repo.save_setting('fixed_account',{'platform_user_id':'123','provider_id':'douyin_browser','verified':False})
        batch=BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'saved-account',self.now)
        self.assertEqual(batch['mode'],'publish')
        self.assertEqual(batch['account']['platform_user_id'],'123')

    def test_unfinished_live_batch_blocks_account_switch(self):
        self.seed();self.repo.save_setting('fixed_account',{'platform_user_id':'123','provider_id':'douyin_browser','verified':True})
        batch=BatchPlanner(self.store).create(dict(self.spec,mode='publish'),'switch-blocker',self.now)
        self.assertEqual(self.store.account_switch_blocker(),'ACTIVE')
        self.store.pause_batch(batch['id'])
        self.assertEqual(self.store.account_switch_blocker(),'PAUSED')
        self.store.cancel_batch(batch['id'])
        self.assertIsNone(self.store.account_switch_blocker())

    def test_cancel_releases_only_unsubmitted_reservations(self):
        self.seed();b=BatchPlanner(self.store).create(self.spec,'a',self.now)
        self.store.cancel_batch(b['id']);self.assertEqual(len(self.store.available_contents()),5)
        self.assertEqual(self.store.batch(b['id'])['status'],'CANCELLED')
    def test_frozen_inputs_survive_library_changes(self):
        self.seed();b=BatchPlanner(self.store).create(self.spec,'a',self.now)
        old=self.store.items(b['id'])[0]['request'];self.lib.add_content(self.content(99))
        self.assertEqual(self.store.items(b['id'])[0]['request'],old)
        self.assertTrue(old['material_snapshot']);self.assertEqual(old['voice'],'Tingting')
