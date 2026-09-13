"""Serial, durable production using interchangeable speech/render providers."""
from pathlib import Path
from .models import frozen,file_hash,text_hash,atomic_json
from .core import Runner,Cancelled
from .providers.system_speech import SystemSpeech
from .providers.footage_renderer import FootageRenderer

class Workflow:
    def __init__(self,repository,speech=None,renderer=None):
        self.repository=repository;self.speech=speech or SystemSpeech();self.renderer=renderer or FootageRenderer(repository)
    def enqueue(self,request,job_id=None):
        request=frozen(request);content=request['content']
        if not content.get('enabled',True):raise ValueError('该内容已停用，不能生成')
        if not content.get('title','').strip() or not content.get('voice_text','').strip():raise ValueError('标题和口播必填')
        root=Path(request.get('material_root','')).resolve()
        if not request.get('material_root') or not root.is_dir():raise ValueError('请选择有效素材目录')
        entries=[]
        if 'material_snapshot' in request:
            for entry in request['material_snapshot']:
                p=Path(entry['path']).resolve()
                if not p.is_file():raise ValueError('素材原文件失效，请在素材库重新定位')
                if root not in p.parents:
                    with self.repository.connect() as db:
                        owned=db.execute('SELECT path FROM library_materials WHERE id=?',(entry['sha256'],)).fetchone()
                    if not owned or Path(owned[0]).resolve()!=p:raise ValueError('素材引用未登记在资料库')
                if file_hash(p)!=entry['sha256']:raise ValueError('冻结素材已改变')
                entries.append(dict(entry,path=str(p)))
        else:
            for p in sorted(root.rglob('*')):
                if p.is_file() and p.suffix.lower() in {'.mp4','.mov','.mkv','.avi'}:
                    if root not in p.resolve().parents:raise ValueError('素材链接超出选定目录：'+str(p))
                    entries.append({'path':str(p.resolve()),'sha256':file_hash(p)})
        if not entries:raise ValueError('素材目录没有视频')
        request['material_snapshot']=entries
        if request.get('bgm'):
            sha=file_hash(request['bgm'])
            if request.get('bgm_sha256',sha)!=sha:raise ValueError('冻结音乐已改变')
            request['bgm_sha256']=sha
        content['revision']=text_hash('\n'.join(content.get(k,'') for k in ('title','voice_text','body','tags')))
        request['providers']={'speech':getattr(self.speech,'provider_id',type(self.speech).__name__),'renderer':getattr(self.renderer,'provider_id',type(self.renderer).__name__)}
        return self.repository.create_job(request,job_id)
    def run(self,identifiers,cancel,progress):
        results=[]
        for number,ident in enumerate(identifiers,1):
            job=self.repository.get_job(ident)
            if job['status']!='QUEUED':raise ValueError('任务不是待开始状态，请创建新的重试记录')
            work=Path(self.repository.setting('temp_dir',str(self.repository.root/'tasks')))/job['job_id']/ident;work.mkdir(parents=True,exist_ok=True)
            request=job['request'];atomic_json(work/'input.json',request)
            def stage(message):progress('[%s/%s] %s：%s'%(number,len(identifiers),request['content']['title'],message))
            try:
                Runner(cancel).check();self.repository.set_job(ident,'RUNNING')
                for entry in request['material_snapshot']:
                    Runner(cancel).check()
                    if file_hash(entry['path'])!=entry['sha256']:raise ValueError('素材已改变，请重新创建任务')
                if request.get('bgm_sha256') and file_hash(request['bgm'])!=request['bgm_sha256']:raise ValueError('BGM已改变，请重新创建任务')
                speech=self.speech.synthesize(request,work,cancel,stage)
                Runner(cancel).check()
                result=self.renderer.render(request,speech,work,cancel,stage)
                Runner(cancel).check()
                if not Path(result).is_file():raise ValueError('未找到实际成片')
                output_dir=self.repository.setting('output_dir')
                if output_dir:
                    target=Path(output_dir)/ident/'result.mp4';target.parent.mkdir(parents=True,exist_ok=True)
                    import shutil
                    shutil.move(str(result),str(target));result=str(target)
                    if (work/'manifest.json').is_file():
                        import json
                        manifest=json.loads((work/'manifest.json').read_text(encoding='utf-8'));manifest['output']=str(target)
                        atomic_json(target.with_name('manifest.json'),manifest)
                self.repository.set_job(ident,'SUCCEEDED',str(Path(result).resolve()))
            except Exception as exc:
                self.repository.set_job(ident,'CANCELLED' if isinstance(exc,Cancelled) else 'FAILED',error=str(exc))
                stage(str(exc))
            log_dir=self.repository.setting('log_dir')
            if log_dir and (work/'process.log').is_file():
                import shutil
                logs=Path(log_dir);logs.mkdir(parents=True,exist_ok=True);shutil.move(str(work/'process.log'),str(logs/(ident+'.log')))
            final=self.repository.get_job(ident)
            atomic_json(work/'task-result.json',final);results.append(final)
        return results
