"""Exclusive detached Qt host, serial work thread and durable control queue."""
import argparse
import json
import os
import threading
import time
from PySide6.QtCore import QLockFile,QTimer
from PySide6.QtWidgets import QApplication
from .repository import Repository
from .batch_store import BatchStore,decode
from .library import Library
from .batch_engine import BatchEngine
from .workflow import Workflow
from .platforms import resolve_font,tts_provider
from .core import Runner

class Background:
    def __init__(self,store,simulation,font):
        self.store=store;self.repo=store.repo;self.simulation=simulation;self.font=font
        self.stop=threading.Event();self.session=None;self.service=None;self.message='后台就绪'
        self.engine=BatchEngine(store,Workflow(self.repo),self.get_service,simulation,stop=self.stop,progress=self.progress)
        self.thread=threading.Thread(target=self.run,daemon=True,name='daily-batch');self.thread.start()
    def progress(self,message):
        text=str(message)
        self.message='视频已生成，等待排期' if '生成完成：' in text else text
        self.heartbeat()
    def heartbeat(self):self.repo.save_setting('daily_worker',{'at':time.time(),'pid':os.getpid(),'mode':'simulate' if self.simulation else 'live','message':self.message,'running':not self.stop.is_set()})
    def ensure_session(self):
        if self.simulation:raise RuntimeError('模拟后台禁止连接真实发布器')
        if self.session is None:
            from .browser_login import DouyinBrowserSession
            from .providers.douyin_browser import DouyinBrowserPublisher
            from .browser_publishing import BrowserPublicationService
            self.session=DouyinBrowserSession(self.repo)
            self.service=BrowserPublicationService(self.repo,DouyinBrowserPublisher(self.session))
    def get_service(self,cancel,progress):
        self.ensure_session()
        if not self.repo.setting('fixed_account',{}).get('verified'):self.session.identity(cancel)
        return self.service
    def execute_command(self,command):
        kind=command['kind'];payload=command['payload'];lib=Library(self.store)
        if kind=='IMPORT_MATERIALS':return lib.import_materials(payload['paths'],self.stop,self.progress)
        if kind=='IMPORT_BGM':return lib.import_bgms(payload['paths'],self.stop,self.progress)
        if kind=='IMPORT_DOCUMENTS':return lib.import_documents(payload['paths'],self.stop,self.progress)
        if kind=='LOGIN':
            if self.simulation:return {'message':'本地模拟，无需登录'}
            # Login is user initiated, allowing the official QR/verification page.
            self.ensure_session()
            return self.session.login(self.stop,self.progress)
        if kind=='SWITCH_ACCOUNT':
            if self.simulation:return {'message':'本地模拟，无需切换账号'}
            if self.store.account_switch_blocker():
                raise ValueError('当前账号仍有未结束的发布任务，请先完成或取消后再切换账号')
            self.ensure_session()
            return self.session.switch_account(self.stop,self.progress)
        if kind=='OPEN_BACKEND':
            if self.simulation:return {'message':'本地模拟，无需打开抖音后台'}
            self.ensure_session()
            return self.session.open_backend(self.stop,self.progress)
        if kind=='RECONCILE':
            service=self.get_service(self.stop,self.progress);item=self.store.item(payload['item_id'])
            if item['state']!='UNKNOWN':raise ValueError('此条不需要补录核对')
            record=service.reconcile(item['publication_id'],payload['video_id'],self.stop,self.progress)
            if record['status']=='ACCEPTED':self.engine._finish(item,'ACCEPTED')
            return {'message':'作品编号与账号、标题已核对，请继续批次'}
        raise ValueError('未知后台操作')
    def restore_account(self):
        if self.simulation or not self.repo.setting('fixed_account',{}):return
        self.progress('正在自动检查抖音登录状态')
        try:
            self.ensure_session()
            self.session.restore(self.stop)
            self.message='账号已自动连接'
        except Exception as exc:
            self.message='账号未能自动连接，请点击首页登录'
            self.repo.save_setting('daily_account_restore_error',str(exc))
        self.heartbeat()
    def run(self):
        try:
            try:
                voices=tts_provider(Runner(self.stop)).list_voices()
            except Exception as exc:
                voices=[]
                self.repo.save_setting('daily_voice_error',str(exc))
            self.repo.save_setting('daily_voices',voices)
            if not self.repo.setting('daily_defaults'):
                old=self.repo.setting('studio_state',{})
                voice=old.get('voice') if old.get('voice') in voices else ('Tingting' if 'Tingting' in voices else (voices[0] if voices else ''))
                self.repo.save_setting('daily_defaults',{'voice':voice,'rate':1.0,'width':720,'font':self.font,'subtitle_max_chars':14,'bgm_enabled':False,'bgm_mode':'specified','bgm':'','bgm_volume':.15})
            self.restore_account()
            while not self.stop.is_set():
                with self.repo.connect() as db:
                    row=db.execute("SELECT * FROM daily_commands WHERE status='QUEUED' AND kind!='STOP' ORDER BY created_at LIMIT 1").fetchone()
                    if row:db.execute("UPDATE daily_commands SET status='RUNNING' WHERE id=?",(row['id'],))
                if row:
                    command=decode(row)
                    try:
                        result=self.execute_command(command)
                        with self.repo.connect() as db:db.execute("UPDATE daily_commands SET status='DONE',result_data=? WHERE id=?",(json.dumps(result,ensure_ascii=False),command['id']))
                    except Exception as exc:
                        with self.repo.connect() as db:db.execute("UPDATE daily_commands SET status='FAILED',error=? WHERE id=?",(str(exc),command['id']))
                    self.message='后台就绪';continue
                if not self.engine.step():self.stop.wait(.5)
        except Exception as exc:self.repo.save_setting('daily_worker_error',str(exc));self.stop.set()
        finally:
            if self.session:self.session.close()
            self.message='后台已停止';self.heartbeat()
    def tick(self):
        with self.repo.connect() as db:
            row=db.execute("SELECT id FROM daily_commands WHERE status='QUEUED' AND kind='STOP' LIMIT 1").fetchone()
            if row:
                self.stop.set();db.execute("UPDATE daily_commands SET status='DONE' WHERE id=?",(row['id'],))
        self.heartbeat()
        if not self.thread.is_alive():QApplication.instance().quit()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',required=True);parser.add_argument('--simulate',action='store_true');args=parser.parse_args()
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    repo=Repository(args.workspace);lock=QLockFile(str(repo.root/'app.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(0):return 2
    mode='simulate' if args.simulate else 'live'
    if repo.setting('daily_worker_mode',mode)!=mode:return 3
    repo.save_setting('daily_worker_mode',mode)
    try:font=resolve_font()
    except RuntimeError as exc:
        font=''
        repo.save_setting('daily_font_error',str(exc))
    repo.save_setting('daily_resolved_font',font)
    store=BatchStore(repo);store.recover();background=Background(store,args.simulate,font)
    timer=QTimer();timer.timeout.connect(background.tick);timer.start(1000)
    result=app.exec();lock.unlock();return result

if __name__=='__main__':raise SystemExit(main())
