"""Additive durable libraries, finite batches and background commands."""
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

JSON_FIELDS={'spec','account','request','payload','result_data'}

def decode(row):
    if row is None:raise ValueError('记录不存在')
    out=dict(row)
    for key in JSON_FIELDS:
        if key in out:out[key]=json.loads(out[key])
    return out

class BatchStore:
    def __init__(self,repo):
        self.repo=repo;self.root=repo.root
        with repo.connect() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_schema'").fetchone()
            if not exists:
                backup=self.root/'backups';backup.mkdir(exist_ok=True)
                # sqlite3 connection contexts commit/rollback but do not close the
                # connection. Closing the backup promptly is required on Windows,
                # otherwise the workspace cannot be cleaned up or moved.
                with closing(sqlite3.connect(str(backup/('before-daily-'+uuid.uuid4().hex+'.sqlite')))) as dest:
                    db.backup(dest)
            db.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS daily_schema(version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS library_materials(id TEXT PRIMARY KEY,path TEXT NOT NULL,name TEXT NOT NULL,duration REAL NOT NULL,created_at REAL NOT NULL,active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS library_bgm(id TEXT PRIMARY KEY,path TEXT NOT NULL,name TEXT NOT NULL,duration REAL NOT NULL,created_at REAL NOT NULL,active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS library_contents(id TEXT PRIMARY KEY,request TEXT NOT NULL,created_at REAL NOT NULL,active INTEGER NOT NULL DEFAULT 1,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS daily_batches(id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,spec TEXT NOT NULL,account TEXT NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,deadline REAL NOT NULL,interval_seconds INTEGER NOT NULL,error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS daily_items(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,content_id TEXT NOT NULL,ordinal INTEGER NOT NULL,due_at REAL NOT NULL,request TEXT NOT NULL,state TEXT NOT NULL,reservation INTEGER NOT NULL DEFAULT 1,attempt_id TEXT,result TEXT,publication_id TEXT,submitted_at REAL,error TEXT NOT NULL DEFAULT '',tries INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS daily_content_reservation ON daily_items(content_id) WHERE reservation=1;
            CREATE TABLE IF NOT EXISTS deleted_task_records(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS daily_commands(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,result_data TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '');
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(library_materials)')}
            if 'storage_mode' not in columns:db.execute("ALTER TABLE library_materials ADD COLUMN storage_mode TEXT NOT NULL DEFAULT 'managed'")
            if 'old_copy_path' not in columns:db.execute("ALTER TABLE library_materials ADD COLUMN old_copy_path TEXT NOT NULL DEFAULT ''")
            columns={r[1] for r in db.execute('PRAGMA table_info(library_bgm)')}
            if 'storage_mode' not in columns:db.execute("ALTER TABLE library_bgm ADD COLUMN storage_mode TEXT NOT NULL DEFAULT 'managed'")
            if 'old_copy_path' not in columns:db.execute("ALTER TABLE library_bgm ADD COLUMN old_copy_path TEXT NOT NULL DEFAULT ''")
            version=db.execute('SELECT version FROM daily_schema').fetchone()
            if version and version[0]!=1:raise RuntimeError('批次数据库版本不匹配')
            if not version:db.execute('INSERT INTO daily_schema VALUES(1)')
    def _read(self,query,args=()):
        with self.repo.connect() as db:return [decode(r) for r in db.execute(query,args)]
    def deleted_task_ids(self):
        return {r['id'] for r in self._read('SELECT id FROM deleted_task_records')}
    def delete_task_record(self,ident):
        with self.repo.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state FROM daily_items WHERE id=?',(ident,)).fetchone()
            if not row:raise ValueError('任务不存在')
            if row['state'] not in ('FAILED','CANCELLED','ACCEPTED','PUBLISHED','SIMULATED','GENERATED'):
                raise ValueError('任务执行中或提交结果待核对，请先暂停并取消任务或核对结果')
            if row['state']=='FAILED':db.execute("UPDATE daily_items SET state='CANCELLED',reservation=0 WHERE id=?",(ident,))
            db.execute('INSERT OR IGNORE INTO deleted_task_records(id) VALUES(?)',(ident,))
    def materials(self):return self._read('SELECT * FROM library_materials WHERE active=1 ORDER BY created_at,id')
    def bgms(self,include_inactive=False):return self._read('SELECT * FROM library_bgm'+('' if include_inactive else ' WHERE active=1')+' ORDER BY created_at,id')
    def contents(self):return self._read('SELECT * FROM library_contents ORDER BY created_at,id')
    def available_contents(self):
        return self._read('SELECT * FROM library_contents c WHERE active=1 AND used=0 AND NOT EXISTS(SELECT 1 FROM daily_items i WHERE i.content_id=c.id AND reservation=1) ORDER BY created_at,id')
    def library_delete_blocker(self,kind,ident):
        """Return the first unfinished item that still needs a library record."""
        if kind not in ('material','bgm','content'):raise ValueError('未知资料库类型')
        final={'ACCEPTED','PUBLISHED','SIMULATED','GENERATED','CANCELLED'}
        for batch in self.batches():
            if batch['status'] in ('COMPLETED','CANCELLED'):continue
            for item in self.items(batch['id']):
                if item['state'] in final:continue
                request=item['request']
                used=(item['content_id']==ident if kind=='content' else
                      request.get('bgm_sha256')==ident if kind=='bgm' else
                      any(entry.get('sha256')==ident for entry in request.get('material_snapshot',[])))
                if used:return item
        return None
    def batches(self):return self._read('SELECT * FROM daily_batches ORDER BY created_at DESC')
    def account_switch_blocker(self):
        """A browser profile belongs to one account, so live unfinished work owns it."""
        with self.repo.connect() as db:
            row=db.execute("SELECT status FROM daily_batches WHERE mode='publish' AND status IN ('ACTIVE','PAUSED','NEEDS_ATTENTION') ORDER BY created_at LIMIT 1").fetchone()
        return row[0] if row else None
    def batch(self,ident):return self._read('SELECT * FROM daily_batches WHERE id=?',(ident,))[0]
    def items(self,ident=None):
        return self._read('SELECT * FROM daily_items'+(' WHERE batch_id=?' if ident else '')+' ORDER BY due_at,ordinal', (ident,) if ident else ())
    def item(self,ident):return self._read('SELECT * FROM daily_items WHERE id=?',(ident,))[0]
    def update_item(self,ident,**fields):
        allowed={'state','reservation','attempt_id','result','publication_id','submitted_at','error','tries','due_at'}
        if not fields or not set(fields)<=allowed:raise ValueError('非法字段')
        with self.repo.connect() as db:db.execute('UPDATE daily_items SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',tuple(fields.values())+(ident,))
    def set_batch(self,ident,status,error=''):
        with self.repo.connect() as db:db.execute('UPDATE daily_batches SET status=?,error=? WHERE id=?',(status,error,ident))
    def pause_batch(self,ident):
        with self.repo.connect() as db:db.execute("UPDATE daily_batches SET status='PAUSED' WHERE id=? AND status='ACTIVE'",(ident,))
    def resume_batch(self,ident,now_ts=None):
        b=self.batch(ident)
        if b['status'] not in ('PAUSED','NEEDS_ATTENTION'):raise ValueError('此任务不能继续')
        if (time.time() if now_ts is None else now_ts)>b['deadline']:raise ValueError('任务日期已过，请取消未执行项并新建排期')
        if any(i['state'] in ('UNKNOWN','SUBMITTING') for i in self.items(ident)):raise ValueError('提交结果尚未核对，不能重复发布；请先查询原记录')
        with self.repo.connect() as db:
            for i in self.items(ident):
                if i['state']=='FAILED' and i['publication_id']:
                    pub=self.repo.get_publication(i['publication_id'])
                    if pub['receipt'].get('click_intent_at') or pub['status'] in ('UNKNOWN','ACCEPTED','SUBMITTING'):raise ValueError('需先核对原提交记录')
                    db.execute("UPDATE publications SET status='CANCELLED' WHERE publication_id=?",(i['publication_id'],))
            db.execute("UPDATE daily_items SET state='QUEUED',tries=0,error='' WHERE batch_id=? AND state='FAILED' AND publication_id IS NULL",(ident,))
            db.execute("UPDATE daily_items SET state='READY',publication_id=NULL,tries=0,error='' WHERE batch_id=? AND state='FAILED' AND publication_id IS NOT NULL",(ident,))
            db.execute("UPDATE daily_batches SET status='ACTIVE',error='' WHERE id=?",(ident,))
    def cancel_batch(self,ident):
        with self.repo.connect() as db:
            db.execute("UPDATE daily_batches SET status='CANCELLED' WHERE id=? AND status NOT IN ('COMPLETED','CANCELLED')",(ident,))
            db.execute("UPDATE daily_items SET state='CANCELLED',reservation=0 WHERE batch_id=? AND state IN ('QUEUED','READY','FAILED') AND submitted_at IS NULL",(ident,))
    def command(self,kind,payload=None):
        ident=uuid.uuid4().hex
        with self.repo.connect() as db:db.execute('INSERT INTO daily_commands(id,kind,payload,status,created_at) VALUES(?,?,?,?,?)',(ident,kind,json.dumps(payload or {},ensure_ascii=False),'QUEUED',time.time()))
        return ident
    def commands(self):return self._read('SELECT * FROM daily_commands ORDER BY created_at DESC LIMIT 30')
    def complete_if_done(self,ident):
        items=self.items(ident)
        if items and all(i['state'] in ('ACCEPTED','PUBLISHED','SIMULATED','GENERATED','CANCELLED') for i in items):
            with self.repo.connect() as db:db.execute("UPDATE daily_batches SET status='COMPLETED' WHERE id=? AND status='ACTIVE'",(ident,))
    def recover(self):
        """Only the exclusive worker may call this, never a newly opened UI."""
        self.repo.recover()
        with self.repo.connect() as db:
            db.execute("UPDATE daily_commands SET status='FAILED',error='后台重启，请重新操作' WHERE status='RUNNING'")
            db.execute("UPDATE daily_items SET state='QUEUED',error='上次制作中断，将创建新尝试' WHERE state='GENERATING'")
            for row in db.execute("SELECT * FROM daily_items WHERE state IN ('PREPARING','SUBMITTING')").fetchall():
                pub=db.execute('SELECT publication_id,status,receipt FROM publications WHERE publication_id=? OR attempt_id=? ORDER BY created_at DESC LIMIT 1',(row['publication_id'],row['attempt_id'])).fetchone()
                if pub:db.execute('UPDATE daily_items SET publication_id=? WHERE id=?',(pub['publication_id'],row['id']))
                receipt=json.loads(pub['receipt']) if pub else {}
                if row['submitted_at'] or receipt.get('click_intent_at') or (pub and pub['status'] in ('UNKNOWN','ACCEPTED')):
                    state='ACCEPTED' if pub and pub['status']=='ACCEPTED' else 'UNKNOWN'
                    db.execute('UPDATE daily_items SET state=?,submitted_at=COALESCE(submitted_at,?) WHERE id=?',(state,time.time(),row['id']))
                    if state=='UNKNOWN':db.execute("UPDATE daily_batches SET status='NEEDS_ATTENTION',error='提交结果不明，已停止后续发布' WHERE id=?",(row['batch_id'],))
                else:
                    if pub:db.execute("UPDATE publications SET status='CANCELLED' WHERE publication_id=?",(pub['publication_id'],))
                    db.execute("UPDATE daily_items SET state='READY',publication_id=NULL WHERE id=?",(row['id'],))

            db.execute("UPDATE library_contents SET used=1 WHERE id IN (SELECT content_id FROM daily_items WHERE state IN ('ACCEPTED','PUBLISHED','SIMULATED','GENERATED'))")
            db.execute("UPDATE daily_items SET reservation=0 WHERE state IN ('ACCEPTED','PUBLISHED','SIMULATED','GENERATED')")
            db.execute("UPDATE daily_items SET state='CANCELLED',reservation=0 WHERE batch_id IN (SELECT id FROM daily_batches WHERE status='CANCELLED') AND submitted_at IS NULL AND state IN ('QUEUED','READY','FAILED')")
