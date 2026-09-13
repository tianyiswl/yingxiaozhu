"""Launch the independent worker; reopening a window never recovers running jobs."""
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from .repository import Repository

def ensure_worker(root,simulation=False):
    root=Path(root).resolve();repo=Repository(root)
    mode='simulate' if simulation else 'live'
    saved=repo.setting('daily_worker_mode')
    if saved and saved!=mode:raise ValueError('模拟资料与真实账号需使用不同工作目录')
    repo.save_setting('daily_worker_mode',mode)
    beat=repo.setting('daily_worker',{})
    if beat.get('running') and time.time()-beat.get('at',0)<8:return None
    env=dict(os.environ,QT_QPA_PLATFORM='windows' if platform.system()=='Windows' else 'offscreen')
    args=[sys.executable]+(['--worker'] if getattr(sys,'frozen',False) else ['-m','factory_video_tool.batch_worker'])+['--workspace',str(root)]
    if simulation:args.append('--simulate')
    kwargs={'cwd':str(Path(__file__).resolve().parents[1]),'env':env,'stdin':subprocess.DEVNULL}
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS
    else:kwargs['start_new_session']=True
    logs=Path(repo.setting('log_dir',str(root)));logs.mkdir(parents=True,exist_ok=True)
    temp=repo.setting('temp_dir')
    if temp:env.update(TMPDIR=temp,TMP=temp,TEMP=temp)
    with (logs/'background.log').open('ab') as log:
        process=subprocess.Popen(args,stdout=log,stderr=log,**kwargs)
    return process.pid
