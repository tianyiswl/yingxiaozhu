"""Managed copies and additive, validated document versions."""
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from .core import Runner,probe,duration,executable
from .models import file_hash,text_hash
from .providers.local_documents import LocalDocuments
from .providers.douyin_browser import validate_content

VIDEO_EXT={'.mp4','.mov','.mkv','.avi'}
AUDIO_EXT={'.mp3','.wav','.m4a','.aac','.flac','.ogg'}
DOC_EXT={'.txt','.docx','.xlsx'}

class Library:
    def __init__(self,store):
        self.store=store;self.assets=store.root/'library/assets';self.bgm_assets=store.root/'library/bgm'
        self.assets.mkdir(parents=True,exist_ok=True);self.bgm_assets.mkdir(parents=True,exist_ok=True)
    @staticmethod
    def expand(paths,extensions):
        files=[];seen=set()
        for raw in paths:
            p=Path(raw).expanduser().resolve()
            if not p.exists():raise ValueError('文件不存在：'+str(p))
            if p.is_dir() and extensions==DOC_EXT:
                candidates=[]
                for directory,dirs,names in os.walk(p):
                    dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in {'_internal','site-packages','__pycache__','node_modules','venv'} and not d.endswith(('.dist-info','.egg-info')))
                    candidates.extend(Path(directory)/name for name in sorted(names))
            else:candidates=sorted(p.rglob('*')) if p.is_dir() else [p]
            for file in candidates:
                if not file.is_file() or file.suffix.lower() not in extensions:continue
                resolved=file.resolve()
                if p.is_dir() and p not in resolved.parents:continue
                if resolved not in seen:seen.add(resolved);files.append(resolved)
        return files
    def import_materials(self,paths,cancel,progress):
        files=self.expand(paths,VIDEO_EXT);out={'added':0,'duplicate':0,'errors':[]}
        if not files:raise ValueError('没有找到支持的视频文件')
        existing={m['id'] for m in self.store.materials()};runner=Runner(cancel)
        for index,source in enumerate(files):
            runner.check();progress('检查素材 %s/%s：%s'%(index+1,len(files),source.name))
            temp=None
            try:
                sha=file_hash(source)
                if sha in existing:out['duplicate']+=1;continue
                info=probe(source,runner)
                if not any(s.get('codec_type')=='video' for s in info['streams']):raise ValueError('没有视频画面')
                seconds=duration(info)
                runner.run([executable('ffmpeg'),'-v','error','-xerror','-i',source,'-map','0:v:0','-an','-f','null','-'])
                if file_hash(source)!=sha:raise ValueError('检查过程中素材发生变化，请重新导入')
                with self.store.repo.connect() as db:
                    db.execute("INSERT OR IGNORE INTO library_materials(id,path,name,duration,created_at,storage_mode) VALUES(?,?,?,?,?,'reference')",(sha,str(source),source.name,seconds,time.time()))
                existing.add(sha);out['added']+=1
            except Exception as exc:
                runner.check();out['errors'].append(source.name+'：'+str(exc))
            finally:
                # Only this operation's incomplete private copy is removed.
                if temp is not None and temp.exists():temp.unlink()
        return out
    def import_bgms(self,paths,cancel,progress):
        files=self.expand(paths,AUDIO_EXT);out={'added':0,'duplicate':0,'errors':[]}
        if not files:raise ValueError('没有找到支持的音频文件')
        existing={music['id'] for music in self.store.bgms(include_inactive=True)};runner=Runner(cancel)
        for index,source in enumerate(files):
            runner.check();progress('检查背景音乐 %s/%s：%s'%(index+1,len(files),source.name))
            temp=None
            try:
                sha=file_hash(source)
                if sha in existing:out['duplicate']+=1;continue
                info=probe(source,runner)
                if not any(stream.get('codec_type')=='audio' for stream in info['streams']):raise ValueError('没有可用音频')
                seconds=duration(info)
                runner.run([executable('ffmpeg'),'-v','error','-xerror','-i',source,'-map','0:a:0','-f','null','-'])
                if file_hash(source)!=sha:raise ValueError('检查过程中音乐发生变化，请重新导入')
                with self.store.repo.connect() as db:
                    db.execute("INSERT OR IGNORE INTO library_bgm(id,path,name,duration,created_at,storage_mode) VALUES(?,?,?,?,?,'reference')",(sha,str(source),source.name,seconds,time.time()))
                existing.add(sha);out['added']+=1
            except Exception as exc:
                runner.check();out['errors'].append(source.name+'：'+str(exc))
            finally:
                if temp is not None and temp.exists():temp.unlink()
        return out
    def add_content(self,content):
        content=dict(content)
        if not str(content.get('voice_text','')).strip():raise ValueError('口播不能为空')
        clean=validate_content(content)
        value={'title':clean['title'],'voice_text':content['voice_text'].strip(),'body':clean['body'],'tags':' '.join(clean['topics']),'enabled':True}
        ident=text_hash(json.dumps(value,ensure_ascii=False,sort_keys=True));value['external_id']=ident;value['source']=content.get('source',{})
        with self.store.repo.connect() as db:
            cur=db.execute('INSERT OR IGNORE INTO library_contents(id,request,created_at) VALUES(?,?,?)',(ident,json.dumps(value,ensure_ascii=False),time.time()))
            added=cur.rowcount==1
        return ident,added
    def _remove_record(self,table,ident,label,asset_root=None):
        record=next((item for item in self.store._read('SELECT * FROM '+table+' WHERE id=?',(ident,))),None)
        if not record:raise ValueError(label+'不存在或已删除')
        kind={'library_materials':'material','library_bgm':'bgm','library_contents':'content'}[table]
        blocker=self.store.library_delete_blocker(kind,ident)
        if blocker:
            states={'QUEUED':'待制作','GENERATING':'制作中','READY':'等待发布','PREPARING':'上传检查中','SUBMITTING':'提交中','FAILED':'失败待处理','UNKNOWN':'提交结果待核对'}
            state=blocker['state'];title=blocker['request'].get('content',{}).get('title') or '未命名任务'
            advice='请先核对该任务的抖音提交结果，再处理任务。' if state=='UNKNOWN' else '请先等待该任务完成，或在任务列表中取消该任务后重试。'
            raise ValueError('被未完成任务“%s”占用（%s，任务编号 %s）。%s'%(title,states.get(state,state),blocker['id'],advice))
        if asset_root is not None:
            path=Path(record['path']).resolve();root=asset_root.resolve()
            if path.exists():
                if root not in path.parents:raise ValueError(label+'不在软件资料库中，无法安全删除')
                try:path.unlink()
                except PermissionError as exc:raise ValueError('文件正在被其他程序占用，或没有删除权限；请关闭播放器等程序并检查文件权限后重试') from exc
        with self.store.repo.connect() as db:db.execute('DELETE FROM '+table+' WHERE id=?',(ident,))
        return record
    def remove_material(self,ident):
        return self._remove_record('library_materials',ident,'素材')
    def relink_material(self,ident,source):
        source=Path(source).expanduser().resolve()
        record=next((m for m in self.store.materials() if m['id']==ident),None)
        if not record:raise ValueError('素材不存在')
        if self.store.library_delete_blocker('material',ident):raise ValueError('素材仍被未完成任务占用，请先取消或完成任务再重新定位')
        if not source.is_file():raise ValueError('所选文件不存在或硬盘未连接')
        if file_hash(source)!=ident:raise ValueError('所选文件与原素材内容不同，请选择同一视频；新视频请使用导入功能')
        if self.assets.resolve() in source.parents:raise ValueError('请选择素材库目录之外的原文件')
        old=record.get('old_copy_path','') or (record['path'] if record.get('storage_mode')=='managed' else '')
        with self.store.repo.connect() as db:
            db.execute("UPDATE library_materials SET path=?,storage_mode='reference',old_copy_path=? WHERE id=?",(str(source),old,ident))
        return old

    def cleanup_material_copy(self,ident):
        record=next((m for m in self.store.materials() if m['id']==ident),None)
        if not record or not record.get('old_copy_path'):raise ValueError('没有待清理副本')
        if self.store.library_delete_blocker('material',ident):raise ValueError('素材仍被任务占用，请稍后清理')
        source=Path(record['path']);old=Path(record['old_copy_path'])
        if not source.is_file() or file_hash(source)!=ident:raise ValueError('原文件不可用，已保留旧副本')
        if self.assets.resolve() not in old.resolve().parents or old.resolve()==source.resolve():raise ValueError('副本路径不在软件素材库内，已停止清理')
        if old.exists():
            if file_hash(old)!=ident:raise ValueError('副本内容不匹配，已停止清理')
            old.unlink()
        with self.store.repo.connect() as db:db.execute("UPDATE library_materials SET old_copy_path='' WHERE id=?",(ident,))

    def relink_bgm(self,ident,source):
        source=Path(source).expanduser().resolve()
        record=next((m for m in self.store.bgms() if m['id']==ident),None)
        if not record:raise ValueError('音乐不存在')
        if self.store.library_delete_blocker('bgm',ident):raise ValueError('音乐仍被未完成任务占用，请先取消或完成任务再重新定位')
        if not source.is_file():raise ValueError('所选文件不存在或硬盘未连接')
        if file_hash(source)!=ident:raise ValueError('所选文件与原音乐内容不同，请选择同一音频；新音频请使用导入功能')
        if self.bgm_assets.resolve() in source.parents:raise ValueError('请选择音乐库目录之外的原文件')
        old=record.get('old_copy_path','') or (record['path'] if record.get('storage_mode')=='managed' else '')
        with self.store.repo.connect() as db:
            db.execute("UPDATE library_bgm SET path=?,storage_mode='reference',old_copy_path=? WHERE id=?",(str(source),old,ident))
        defaults=self.store.repo.setting('daily_defaults',{})
        if defaults.get('bgm')==record['path']:
            defaults['bgm']=str(source);self.store.repo.save_setting('daily_defaults',defaults)
        return old

    def cleanup_bgm_copy(self,ident):
        record=next((m for m in self.store.bgms() if m['id']==ident),None)
        if not record or not record.get('old_copy_path'):raise ValueError('没有待清理副本')
        if self.store.library_delete_blocker('bgm',ident):raise ValueError('音乐仍被任务占用，请稍后清理')
        source=Path(record['path']);old=Path(record['old_copy_path'])
        if not source.is_file() or file_hash(source)!=ident:raise ValueError('原文件不可用，已保留旧副本')
        if self.bgm_assets.resolve() not in old.resolve().parents or old.resolve()==source.resolve():raise ValueError('副本路径不在软件音乐库内，已停止清理')
        if old.exists():
            if file_hash(old)!=ident:raise ValueError('副本内容不匹配，已停止清理')
            old.unlink()
        with self.store.repo.connect() as db:db.execute("UPDATE library_bgm SET old_copy_path='' WHERE id=?",(ident,))

    def remove_content(self,ident):
        return self._remove_record('library_contents',ident,'文案')
    def remove_bgm(self,ident):
        record=self._remove_record('library_bgm',ident,'背景音乐')
        defaults=self.store.repo.setting('daily_defaults',{})
        if defaults.get('bgm')==record['path']:
            defaults['bgm']='';defaults['bgm_mode']='random' if self.store.bgms() else 'specified';defaults['bgm_enabled']=bool(self.store.bgms())
            self.store.repo.save_setting('daily_defaults',defaults)
        return record
    def import_documents(self,paths,cancel,progress):
        files=self.expand(paths,DOC_EXT);out={'added':0,'duplicate':0,'errors':[]}
        if not files:raise ValueError('没有找到TXT、DOCX或XLSX文档')
        for index,p in enumerate(files):
            Runner(cancel).check();progress('导入文案 %s/%s：%s'%(index+1,len(files),p.name))
            try:records=LocalDocuments().load(p)
            except Exception as exc:out['errors'].append(p.name+'：'+str(exc));continue
            for n,c in enumerate(records):
                if not c.get('enabled',True):continue
                try:
                    _,added=self.add_content(c);out['added' if added else 'duplicate']+=1
                except Exception as exc:out['errors'].append('%s 第%s条：%s'%(p.name,n+1,exc))
        return out
