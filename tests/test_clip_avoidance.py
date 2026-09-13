import unittest,random,tempfile,json
from pathlib import Path
from factory_video_tool.clip_avoidance import choose_diverse_clips,recent_clip_history
from factory_video_tool.repository import Repository

class ClipAvoidanceTests(unittest.TestCase):
    def test_hash_lookup_accepts_portable_posix_paths(self):
        clips,_=choose_diverse_clips([(Path('/a'),4)],3,{'/a':'same'},[],random.Random(1))
        self.assertEqual(clips[0][0],Path('/a'))

    def test_avoids_known_ranges_even_after_source_renamed(self):
        materials=[(Path('/new/name.mp4'),20)];hashes={'/new/name.mp4':'same'}
        clips,report=choose_diverse_clips(materials,8,hashes,[{'sha256':'same','start':0,'duration':8}],random.Random(1))
        self.assertAlmostEqual(sum(x[2] for x in clips),8)
        self.assertTrue(all(start>=8 for _,start,_ in clips));self.assertEqual(report['recent_overlap_seconds'],0)
    def test_no_overlap_inside_video_when_enough_footage(self):
        clips,report=choose_diverse_clips([(Path('/a'),40)],15,{'/a':'a'},[],random.Random(7))
        for i,(_,s,d) in enumerate(clips):
            for _,t,e in clips[i+1:]:self.assertLessEqual(min(s+d,t+e)-max(s,t),.00001)
        self.assertEqual(report['within_overlap_seconds'],0)
    def test_exhaustion_is_bounded_and_explicit(self):
        clips,report=choose_diverse_clips([(Path('/a'),4)],13,{'/a':'a'},[{'sha256':'a','start':0,'duration':4}],random.Random(1))
        self.assertAlmostEqual(sum(x[2] for x in clips),13)
        self.assertGreater(report['recent_overlap_seconds'],0);self.assertGreater(report['within_overlap_seconds'],0);self.assertTrue(report['warnings'])
    def test_reads_only_successful_history_from_same_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Repository(d);p=Path(d)/'video.mp4';p.write_bytes(b'x')
            (p.parent/'manifest.json').write_text(json.dumps({'status':'SUCCESS','material_snapshot':[{'path':'/old','sha256':'same'}],'clips':[['/old',2,4]]}))
            job=repo.create_job({});repo.set_job(job['attempt_id'],'FAILED',str(p))
            self.assertEqual(recent_clip_history(repo),[])
            repo.set_job(job['attempt_id'],'SUCCEEDED',str(p))
            history=recent_clip_history(Repository(d));self.assertEqual(history[0]['sha256'],'same');self.assertEqual(history[0]['start'],2)
