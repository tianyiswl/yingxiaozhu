"""Finite user-started batches; content reservations and slots are atomic."""
import json
import random
import time
import uuid
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .batch_store import decode
from .models import frozen,file_hash

SHANGHAI=ZoneInfo('Asia/Shanghai')

class BatchPlanner:
    def __init__(self,store):self.store=store
    def create(self,spec,request_key,now_ts=None):
        now_ts=time.time() if now_ts is None else now_ts;spec=dict(frozen(spec))
        with self.store.repo.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM daily_batches WHERE request_key=?',(request_key,)).fetchone()
            if old:return decode(old)
            count=spec['count'];days=spec['days'];interval=spec['interval']
            if any(type(x)!=int for x in (count,days,interval)) or not 1<=count<=100 or not 1<=days<=30 or not 1<=interval<=1440:raise ValueError('数量1—100，天数1—30，间隔1—1440分钟')
            mode=spec.get('mode','publish')
            if mode not in ('publish','simulate','generate'):raise ValueError('未知运行模式')
            account=self.store.repo.setting('fixed_account',{}) if mode=='publish' else {'platform_user_id':'local-'+mode,'display_name':'本地模拟' if mode=='simulate' else '仅制作'}
            if mode=='publish' and (not account.get('platform_user_id') or account.get('provider_id')!='douyin_browser'):raise ValueError('请先登录抖音账号')
            if mode=='publish':
                unresolved=db.execute("SELECT 1 FROM daily_items i JOIN daily_batches b ON b.id=i.batch_id WHERE b.mode='publish' AND i.state IN ('UNKNOWN','SUBMITTING') AND json_extract(b.account,'$.platform_user_id')=? LIMIT 1",(account['platform_user_id'],)).fetchone()
                if unresolved:raise ValueError('已有抖音提交待核对，请先在任务与视频中核对原记录')
            settings=spec['settings']
            settings.setdefault('avoid_recent_clips',True)
            settings.pop('platform_schedule_delay_hours',None)
            settings.pop('platform_schedule_not_before',None)
            immediate=bool(spec.get('immediate',False))
            if immediate:
                start=datetime.fromtimestamp(now_ts,SHANGHAI)
                # Keep the actual creation time for audit, while the flag records that no time was selected.
                spec['start']=start.isoformat()
            else:
                start=datetime.fromisoformat(spec['start'])
                if start.tzinfo is None:start=start.replace(tzinfo=SHANGHAI)
                start=start.astimezone(SHANGHAI)
                if start.timestamp()<now_ts:raise ValueError('首条发布时间已经过去，请选择之后的时间')
            if not settings.get('voice'):raise ValueError('系统中文声音尚未就绪')
            materials=[dict(r) for r in db.execute('SELECT * FROM library_materials WHERE active=1 ORDER BY created_at,id')]
            materials=[m for m in materials if Path(m['path']).is_file()]
            if not materials:raise ValueError('没有可用素材，原文件可能已移动或硬盘未连接；请在素材库重新定位')
            use_bgm=bool(settings.get('bgm_enabled',settings.get('bgm')))
            bgm_mode=settings.get('bgm_mode','specified')
            music=[dict(row) for row in db.execute('SELECT * FROM library_bgm WHERE active=1 ORDER BY created_at,id')]
            music=[m for m in music if Path(m['path']).is_file()]
            if bgm_mode not in ('random','sequence','specified'):raise ValueError('背景音乐选择方式无效')
            if use_bgm and not music:raise ValueError('请先在背景音库添加音乐')
            selected_music=None
            if use_bgm and bgm_mode=='specified':
                selected_music=next((item for item in music if item['path']==settings.get('bgm')),None)
                if selected_music is None:raise ValueError('请在设置中指定一首可用背景音乐')
            if not use_bgm:settings['bgm']='';settings.pop('bgm_sha256',None)
            content_mode=spec.get('content_selection_mode','sequence')
            if content_mode not in ('sequence','random'):raise ValueError('文案使用方式无效')
            contents=[decode(r) for r in db.execute('SELECT * FROM library_contents c WHERE active=1 AND used=0 AND NOT EXISTS(SELECT 1 FROM daily_items i WHERE i.content_id=c.id AND reservation=1) ORDER BY created_at,id')]
            needed=count*days
            if len(contents)<needed:raise ValueError('未用文案不足，还缺%s条'%(needed-len(contents)))
            if content_mode=='random':contents=random.Random('content:'+request_key).sample(contents,needed)
            else:contents=contents[:needed]
            # “立即提交”表示立刻制作并提交到抖音；平台侧的发布时间另由
            # platform_schedule_not_before 依次排开，不能用本地制作时间互相阻塞。
            # A late first slot and a normal publishing interval may naturally cross
            # midnight.  That is an expected schedule, not an error condition.
            slots=[(start+timedelta(days=d,minutes=i*interval)).timestamp() for d in range(days) for i in range(count)]
            for row in db.execute("SELECT i.due_at,b.account,b.interval_seconds FROM daily_items i JOIN daily_batches b ON b.id=i.batch_id WHERE i.state IN ('QUEUED','GENERATING','READY','PREPARING','SUBMITTING') AND b.mode=?",(mode,)):
                if json.loads(row['account']).get('platform_user_id')==account['platform_user_id']:
                    if any(abs(t-row['due_at'])<max(interval*60,row['interval_seconds']) for t in slots):raise ValueError('与正在制作或提交的任务冲突，请稍后重试')
            ident=uuid.uuid4().hex;last_slot=datetime.fromtimestamp(max(slots),SHANGHAI);deadline=last_slot.replace(hour=23,minute=59,second=59).timestamp()
            db.execute('INSERT INTO daily_batches(id,request_key,spec,account,mode,status,created_at,deadline,interval_seconds) VALUES(?,?,?,?,?,?,?,?,?)',(ident,request_key,json.dumps(spec,ensure_ascii=False),json.dumps(account,ensure_ascii=False),mode,'ACTIVE',now_ts,deadline,interval*60))
            randomizer=random.Random('background-music:'+request_key)
            for n,(content,due) in enumerate(zip(contents,slots)):
                item_settings=dict(settings)
                if use_bgm:
                    choice=selected_music if bgm_mode=='specified' else music[n%len(music)] if bgm_mode=='sequence' else randomizer.choice(music)
                    item_settings.update(bgm=choice['path'],bgm_sha256=choice['id'])
                request=dict(item_settings,content=content['request'],workspace=str(self.store.root),material_root=str(self.store.root/'library/assets'),material_snapshot=[{'path':m['path'],'sha256':m['id']} for m in materials])
                request['schedule_policy']='hybrid_v1'
                db.execute('INSERT INTO daily_items(id,batch_id,content_id,ordinal,due_at,request,state) VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,ident,content['id'],n,due,json.dumps(request,ensure_ascii=False),'QUEUED'))
        return self.store.batch(ident)
