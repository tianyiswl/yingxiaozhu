"""Hash + source-time avoidance; not visual similarity or originality detection."""
import json
import math
import random
from pathlib import Path

LOOKBACK=20

def recent_clip_history(repo,limit=LOOKBACK):
    history=[]
    with repo.connect() as db:
        rows=db.execute("SELECT attempt_id,result FROM jobs WHERE status='SUCCEEDED' AND result IS NOT NULL ORDER BY updated_at DESC LIMIT ?",(limit,)).fetchall()
    for row in rows:
        try:
            data=json.loads(Path(row['result']).with_name('manifest.json').read_text())
            if data.get('status')!='SUCCESS':continue
            hashes={str(Path(x['path']).resolve()):x['sha256'] for x in data.get('material_snapshot',[])}
            for path,start,length in data.get('clips',[]):
                sha=hashes.get(str(Path(path).resolve()))
                if sha and math.isfinite(float(start)) and math.isfinite(float(length)) and start>=0 and length>0:
                    history.append({'sha256':sha,'start':float(start),'duration':float(length),'attempt_id':row['attempt_id']})
        except (OSError,ValueError,TypeError,KeyError):continue
    return history

def merged(intervals):
    out=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if out and start<=out[-1][1]:out[-1]=(out[-1][0],max(end,out[-1][1]))
        else:out.append((start,end))
    return out

def free_spans(duration,blocked):
    cursor=0;out=[]
    for start,end in merged([(max(0,a-.05),min(duration,b+.05)) for a,b in blocked]):
        if start>cursor:out.append((cursor,start))
        cursor=max(cursor,end)
    if duration>cursor:out.append((cursor,duration))
    return out

def overlap(start,length,intervals):
    return sum(max(0,min(start+length,b)-max(start,a)) for a,b in merged(intervals))

def choose_diverse_clips(materials,target,hashes,history,rng=None):
    rng=rng or random.Random()
    if not materials or not math.isfinite(target) or target<=0:raise ValueError('没有可用素材或目标时长无效')
    # Identical bytes must share usage even if stored under different names.
    sources={}
    for path,duration in materials:
        sha=hashes.get(str(path)) or hashes.get(path.as_posix())
        if not sha:raise ValueError('素材哈希记录缺失：'+str(path))
        if not math.isfinite(duration) or duration<=0:raise ValueError('素材时长无效')
        sources.setdefault(sha,(path,duration))
    recent={sha:[] for sha in sources};current={sha:[] for sha in sources};counts={sha:0 for sha in sources}
    for h in history:
        if h['sha256'] in recent:recent[h['sha256']].append((h['start'],h['start']+h['duration']))
    clips=[];remaining=target;old_seconds=local_seconds=0;last=None
    while remaining>1e-7:
        required=min(3,remaining);candidates=[];tier=0
        for tier in range(3):
            for sha,(path,total) in sources.items():
                spans=free_spans(total,current[sha]+recent[sha]) if tier==0 else free_spans(total,current[sha]) if tier==1 else [(0,total)]
                for lo,hi in spans:
                    if hi-lo+1e-7<required and tier<2:continue
                    length=min(rng.uniform(3,7),hi-lo,remaining)
                    if length<=1e-7:continue
                    starts=[rng.uniform(lo,max(lo,hi-length)) for _ in range(5)]
                    starts.extend([lo,max(lo,hi-length)])
                    # History edges give good candidates when a whole fresh clip no longer fits.
                    for a,b in recent[sha]:starts.extend([max(lo,min(hi-length,b+.05)),max(lo,min(hi-length,a-length-.05))])
                    start=min(starts,key=lambda x:overlap(x,length,current[sha])*100+overlap(x,length,recent[sha]))
                    score=(overlap(start,length,current[sha])/length*100,
                           overlap(start,length,recent[sha])/length,
                           counts[sha],sha==last,len(recent[sha]),rng.random())
                    candidates.append((score,sha,path,start,length))
            if candidates:break
        if not candidates:raise ValueError('无法分配有效镜头')
        _,sha,path,start,length=min(candidates,key=lambda x:x[0])
        old_seconds+=overlap(start,length,recent[sha]);local_seconds+=overlap(start,length,current[sha])
        current[sha].append((start,start+length));counts[sha]+=1;last=sha
        clips.append((path,start,length));remaining-=length
    warnings=[]
    if old_seconds>.001:warnings.append('近期未用片段不足，本条复用了约%.1f秒近期镜头；补充素材可减少重复。'%old_seconds)
    if local_seconds>.001:warnings.append('素材总时长不足，本条内部复用了约%.1f秒片段。'%local_seconds)
    return clips,{'strategy':'source_hash_time_ranges_v1','lookback':LOOKBACK,'history_videos':len({x.get('attempt_id','unknown') for x in history}),
        'recent_overlap_seconds':round(old_seconds,3),'within_overlap_seconds':round(local_seconds,3),'warnings':warnings}
