"""Durable snapshots. Each call owns its connection so UI/workers do not share it."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from .models import now,atomic_json

class Repository:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'app.sqlite'
        with self.connect() as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise RuntimeError('任务数据库版本较新，请使用匹配版本的软件')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS jobs(attempt_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,request TEXT NOT NULL,status TEXT NOT NULL,result TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS publications(publication_id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL,account TEXT NOT NULL,snapshot TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              PRAGMA user_version=1;
            ''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def _row(self,row):
        if row is None:raise ValueError('记录不存在')
        result=dict(row)
        for name in ('request','account','snapshot','receipt'):
            if name in result:result[name]=json.loads(result[name])
        return result
    def create_job(self,request,job_id=None):
        ident=uuid.uuid4().hex;job_id=job_id or uuid.uuid4().hex;stamp=now()
        raw=json.dumps(request,ensure_ascii=False)
        with self.connect() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(ident,job_id,raw,'QUEUED',None,None,stamp,stamp))
        return self.get_job(ident)
    def get_job(self,ident):
        with self.connect() as db:return self._row(db.execute('SELECT * FROM jobs WHERE attempt_id=?',(ident,)).fetchone())
    def jobs(self):
        with self.connect() as db:return [self._row(r) for r in db.execute('SELECT * FROM jobs ORDER BY created_at DESC')]
    def set_job(self,ident,status,result=None,error=None):
        if status not in ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'):raise ValueError('非法任务状态')
        with self.connect() as db:db.execute('UPDATE jobs SET status=?,result=?,error=?,updated_at=? WHERE attempt_id=?',(status,result,error,now(),ident))
    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE publications SET status='INTERRUPTED',updated_at=? WHERE status IN ('PREPARING','PREPARED','COMMIT_CHECK')",(now(),))
            db.execute("UPDATE jobs SET status='INTERRUPTED',error='上次执行中断',updated_at=? WHERE status='RUNNING'",(now(),))
            db.execute("UPDATE publications SET status='UNKNOWN',updated_at=? WHERE status='SUBMITTING'",(now(),))
            db.execute("UPDATE publications SET status='FAILED',updated_at=? WHERE status='UPLOADING'",(now(),))
    def setting(self,key,default=None):
        with self.connect() as db:r=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def save_setting(self,key,value):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def create_publication(self,attempt_id,account,snapshot):
        stamp=now();ident=uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Also guard the same media/account through another production attempt.
            existing=db.execute("SELECT * FROM publications WHERE status NOT IN ('FAILED','CANCELLED')").fetchall()
            for r in existing:
                old=self._row(r)
                if old['account'].get('platform_user_id')==account.get('platform_user_id') and (old['attempt_id']==attempt_id or (snapshot.get('video_sha256') and old['snapshot'].get('video_sha256')==snapshot['video_sha256'])):
                    raise ValueError('此账号已有该成片的发布记录，请查询原记录，不能重复提交')
            db.execute('INSERT INTO publications VALUES(?,?,?,?,?,?,?,?)',(ident,attempt_id,json.dumps(account,ensure_ascii=False),json.dumps(snapshot,ensure_ascii=False),'READY','{}',stamp,stamp))
        return self.get_publication(ident)
    def get_publication(self,ident):
        with self.connect() as db:return self._row(db.execute('SELECT * FROM publications WHERE publication_id=?',(ident,)).fetchone())
    def publications(self):
        with self.connect() as db:return [self._row(r) for r in db.execute('SELECT * FROM publications ORDER BY created_at DESC')]
    def set_publication(self,ident,status,receipt=None):
        with self.connect() as db:
            if receipt is None:db.execute('UPDATE publications SET status=?,updated_at=? WHERE publication_id=?',(status,now(),ident))
            else:db.execute('UPDATE publications SET status=?,receipt=?,updated_at=? WHERE publication_id=?',(status,json.dumps(receipt,ensure_ascii=False),now(),ident))
    def claim_publication(self,ident):
        with self.connect() as db:
            cur=db.execute("UPDATE publications SET status='UPLOADING',updated_at=? WHERE publication_id=? AND status='READY'",(now(),ident))
            if cur.rowcount!=1:raise ValueError('该记录已处理；请先查询结果，不能重复提交')
    def claim_prepared_publication(self,ident):
        with self.connect() as db:
            cur=db.execute("UPDATE publications SET status='COMMIT_CHECK',updated_at=? WHERE publication_id=? AND status='PREPARED'",(now(),ident))
            if cur.rowcount!=1:raise ValueError('当前记录不是待确认状态；请先核对原记录，不能重复提交')
