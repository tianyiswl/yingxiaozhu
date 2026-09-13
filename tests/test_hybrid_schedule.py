import tempfile
import unittest
from pathlib import Path
from factory_video_tool.repository import Repository
from factory_video_tool.batch_store import BatchStore
from factory_video_tool.batch_engine import BatchEngine
from factory_video_tool.batch_planner import BatchPlanner
from factory_video_tool.library import Library

class HybridTests(unittest.TestCase):
    def test_boundaries_and_unknown_guard(self):
        with tempfile.TemporaryDirectory() as d:
            store=BatchStore(Repository(d));engine=BatchEngine(store,None,clock=lambda:10000)
            batch={'id':'x','mode':'publish','account':{'platform_user_id':'1'},'spec':{},'deadline':999999}
            for seconds,expected in [(0,True),(1,False),(7799,False),(7800,True),(10800,True),(14*86400,False)]:
                self.assertEqual(engine._eligible(batch,{'due_at':10000+seconds}),expected)
    def test_no_test_delay_in_new_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Repository(d);store=BatchStore(repo)
            asset=Path(d)/'a.mp4';asset.write_bytes(b'fixture')
            with repo.connect() as db:
                db.execute('INSERT INTO library_materials(id,path,name,duration,created_at,active) VALUES(?,?,?,?,?,?)',('hash',str(asset),'a',10,0,1))
            for n in range(2):Library(store).add_content({'title':str(n),'voice_text':'口播','body':'正文'})
            repo.save_setting('fixed_account',{'platform_user_id':'1','provider_id':'douyin_browser','verified':True})
            spec={'count':2,'days':1,'interval':180,'immediate':True,'mode':'publish','settings':{'voice':'Tingting','platform_schedule_delay_hours':24}}
            batch=BatchPlanner(store).create(spec,'test',1789084800)
            items=store.items(batch['id'])
            self.assertEqual(items[1]['due_at']-items[0]['due_at'],10800)
            self.assertNotIn('platform_schedule_delay_hours',items[0]['request'])
            self.assertEqual(items[0]['request']['schedule_policy'],'hybrid_v1')
