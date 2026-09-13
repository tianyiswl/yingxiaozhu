import json
import os
import threading
import uuid
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QTimer, QUrl, Qt, QLockFile
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QPushButton,QLineEdit,QComboBox,QLabel,QPlainTextEdit,QFileDialog,QMessageBox,QSpinBox,QSlider,QTabWidget,QDialog)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from .core import Runner,load_excel,executable,Cancelled
from .platforms import PathResolver,tts_provider,resolve_font
from .generation import generate,export_video
from .repository import Repository
from .workflow import Workflow
from .providers.local_documents import LocalDocuments
from .providers.system_speech import SystemSpeech
from .release_panels import ReleasePanel,ContentEditor
from .studio_shell import build_shell
from .studio_actions import StudioActions

class Worker(QThread):
    progress=Signal(str)
    succeeded=Signal(object)
    failed=Signal(str)
    def __init__(self,fn,parent=None):
        super().__init__(parent);self.fn=fn;self.cancel=threading.Event()
    def run(self):
        try:self.succeeded.emit(self.fn(self.cancel,self.progress.emit))
        except Exception as e:self.failed.emit(str(e))

class Window(StudioActions,QWidget):
    def __init__(self,workspace=None):
        super().__init__();self.setWindowTitle('映小助 · 商家短视频助手');self.resize(1366,860);self.setMinimumSize(1000,700)
        self.workspace=PathResolver(workspace).prepare();self.worker=None;self.contents=[];self.result=None;self.close_pending=False;self.heartbeats=0;self.closed=False
        self.instance_lock=QLockFile(str(self.workspace/'app.lock'));self.instance_lock.setStaleLockTime(0)
        if not self.instance_lock.tryLock(0):raise RuntimeError('该工作目录已打开，请回到已有窗口')
        self.repository=Repository(self.workspace);self.repository.recover();self.workflow=Workflow(self.repository);self.current_attempt_id=None
        self.draft_step=0;self.editing_content=False;self.material_scan=None;self.material_errors_accepted=False;self.restored_state={}
        build_shell(self)
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(50)
        self.font=resolve_font();self.restore();self.refresh_studio()
        self.play.setEnabled(False);self.export.setEnabled(False);self.folder.setEnabled(False);self.to_publish.setEnabled(False)
        QTimer.singleShot(0,self.check_environment)
    def tick(self):self.heartbeats+=1
    def run_job(self,fn,success):
        if self.worker is not None:return
        for w in self.inputs:w.setEnabled(False)
        self.export.setEnabled(False);self.confirm_save.setEnabled(False);self.cancel_button.setEnabled(True);self.cancel_button.show();self.progress.setRange(0,0);self.progress.show();self.release_panel.set_busy(True)
        self.worker=Worker(fn,self);self.worker.progress.connect(self.log.appendPlainText)
        self.worker.progress.connect(self.show_progress);self.worker.succeeded.connect(success);self.worker.failed.connect(self.on_error);self.worker.finished.connect(self.job_finished);self.worker.start()
    def on_error(self,message):
        self.status.setText('操作未完成，输入已保留。'+str(message).split('\n')[0][:140]);self.step_error.setText(str(message).split('\n')[0][:220]);self.log.appendPlainText(message)
    def job_finished(self):
        self.worker.deleteLater();self.worker=None
        for w in self.inputs:w.setEnabled(True)
        self.cancel_button.setEnabled(False);self.cancel_button.hide();self.progress.hide();self.export.setEnabled(bool(self.result));self.confirm_save.setEnabled(bool(self.result));self.release_panel.set_busy(False);self.refresh_studio()
        if self.close_pending:self.close()
    def check_environment(self):
        if self.closed or self.close_pending:return
        def check(cancel,progress):
            r=Runner(cancel)
            for name in ('ffmpeg','ffprobe'):r.run([executable(name),'-version'],timeout=15)
            enc=r.run([executable('ffmpeg'),'-hide_banner','-encoders'])
            if 'libx264' not in enc:raise RuntimeError('FFmpeg 缺少 libx264 编码器')
            voices=tts_provider(r).list_voices()
            if not voices:raise RuntimeError('系统没有可用音色；请启用系统中文音色')
            return voices
        def done(voices):
            self.voice.clear();self.voice.addItems(voices)
            if 'Tingting' in voices:self.voice.setCurrentText('Tingting')
            saved=self.restored_state.get('voice')
            if saved in voices:self.voice.setCurrentText(saved)
            self.status.setText('本地制作已就绪。')
        self.run_job(check,done)
    def load_demo(self):
        root=Path(__file__).resolve().parents[1]/'runtime'/'合成 测试资料'
        def done(contents):
            self.material.setText(str(root/'产品 测试画面'));self.bgm.setText(str(root/'合成 测试音调.wav'));self.accept_contents(contents)
        self.run_job(lambda c,p:load_excel(root/'人工内容包 测试.xlsx'),done)
    def import_excel(self):
        path,_=QFileDialog.getOpenFileName(self,'导入人工准备的内容包',str(self.workspace),'本地文档 (*.txt *.docx *.xlsx)')
        if path:self.load_path(path)
    def load_path(self,path):
        self.run_job(lambda c,p:LocalDocuments().load(path),self.accept_contents)
    def accept_contents(self,contents):
        self.contents=contents;self.selector.clear()
        for c in contents:self.selector.addItem(('' if c.get('enabled',True) else '[停用] ')+c['external_id']+' · '+c['title'])
        self.show_content();self.persist();self.refresh_studio();self.status.setText('文案已导入，可直接修改标题与口播。')
    def show_content(self,*_):
        i=self.selector.currentIndex()
        if 0<=i<len(self.contents):
            c=self.contents[i];self.content_view.setPlainText('口播：'+c['voice_text']+'\n标题：'+c['title']+'\n正文：'+c.get('body','')+'\n标签：'+c.get('tags',''))
        self.sync_content_editor()
    def pick_material(self):
        p=QFileDialog.getExistingDirectory(self,'仅选择本条产品的素材目录',str(self.workspace))
        if p:
            self.material.setText(p);self.material_scan=None;self.material_errors_accepted=False;self.persist();self.scan_current_material()
    def pick_bgm(self):
        p,_=QFileDialog.getOpenFileName(self,'选择有权使用的音乐',str(self.workspace),'Audio (*.mp3 *.wav *.m4a *.aac)')
        if p:self.bgm.setText(p)
    def request(self):
        if not self.contents or self.selector.currentIndex()<0:raise ValueError('请先导入或填写文案')
        c=self.contents[self.selector.currentIndex()]
        if not c.get('title','').strip() or not c.get('voice_text','').strip():raise ValueError('请填写视频标题和口播文案')
        if not self.contents[self.selector.currentIndex()].get('enabled',True):raise ValueError('该条内容已停用，请先选择启用内容')
        if not self.material.text().strip():raise ValueError('请选择对应产品素材目录')
        if self.material_scan and self.material_scan['root']==str(Path(self.material.text()).resolve()) and self.material_scan.get('errors') and not self.material_errors_accepted:raise ValueError('请先核对不可用素材，再决定是否继续')
        if not self.voice.currentText():raise ValueError('请先完成系统音色检测')
        return dict(workspace=str(self.workspace),content=self.contents[self.selector.currentIndex()],material_root=self.material.text().strip(),bgm=self.bgm.text().strip(),voice=self.voice.currentText(),rate=float(self.rate.currentText()),bgm_volume=self.volume.value()/100,width=1080 if self.resolution.currentIndex()==0 else 720,font=self.font)
    def edit_content(self):
        i=self.selector.currentIndex()
        if i<0:return
        dialog=ContentEditor(self.contents[i],self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:self.save_edited_content(dialog.values())
            except Exception as exc:self.on_error(str(exc))
    def save_edited_content(self,values):
        if not values.get('title','').strip() or not values.get('voice_text','').strip():raise ValueError('标题和口播必填')
        i=self.selector.currentIndex()
        if i<0:raise ValueError('请先导入文案')
        self.contents[i]=dict(self.contents[i],**values)
        self.selector.setItemText(i,self.contents[i]['external_id']+' · '+values['title'])
        self.show_content();self.persist()
    def audition(self):
        if not self.voice.currentText():return
        content=self.contents[self.selector.currentIndex()] if self.contents and self.selector.currentIndex()>=0 else {'voice_text':'这是系统配音试听，请核对声音与语速。'}
        request={'content':dict(content,voice_text=content['voice_text'][:100]),'voice':self.voice.currentText(),'rate':float(self.rate.currentText())}
        def run(cancel,progress):
            work=self.workspace/'auditions'/uuid.uuid4().hex;work.mkdir(parents=True)
            return SystemSpeech().synthesize(request,work,cancel,progress).path
        def done(path):
            self.player.pause();self.audition_player.setSource(QUrl.fromLocalFile(path));self.audition_player.play();self.status.setText('正在试听口播前100字；视频预览保持不变。')
        self.run_job(run,done)
    def start_generation(self):self.start_requests(False)
    def start_batch(self):self.start_requests(True)
    def start_requests(self,batch):
        if self.worker is not None:return
        self.inline_content_changed()
        self.audition_player.stop()
        try:
            if batch:
                first=next((i for i,c in enumerate(self.contents) if c.get('enabled',True)),None)
                if first is None:raise ValueError('没有启用内容')
                self.selector.setCurrentIndex(first)
            req=self.request();self.persist()
            contents=[c for c in self.contents if c.get('enabled',True)] if batch else [req['content']]
            if any(not c.get('title','').strip() or not c.get('voice_text','').strip() for c in contents):raise ValueError('请补全每条勾选文案的标题和口播')
            requests=[dict(req,content=dict(c)) for c in contents]
        except Exception as exc:self.on_error(str(exc));return
        self.player.stop();self.result=None;self.current_attempt_id=None;self.play.setEnabled(False);self.folder.setEnabled(False)
        def run(cancel,progress):
            progress('保存文案与素材快照…')
            jobs=[self.workflow.enqueue(r) for r in requests]
            return self.workflow.run([j['attempt_id'] for j in jobs],cancel,progress)
        self.run_job(run,self.batch_generated)
    def batch_generated(self,results):
        success=[r for r in results if r['status']=='SUCCEEDED']
        if success:
            self.current_attempt_id=success[-1]['attempt_id'];self.generated(success[-1]['result'])
        counts={name:sum(r['status']==name for r in results) for name in ('SUCCEEDED','FAILED','CANCELLED')}
        self.status.setText('生成结束：成功 %s，失败 %s，取消 %s。请预览核对。'%(counts['SUCCEEDED'],counts['FAILED'],counts['CANCELLED']))
        self.release_panel.refresh()
    def generated(self,path):
        self.audition_player.stop();self.video_list.blockSignals(True);self.video_list.setCurrentRow(-1);self.video_list.blockSignals(False);self.pages.setCurrentWidget(self.videos_page)
        for key,b in self.nav.items():b.setChecked(key=='videos')
        self.preview_stack.setCurrentWidget(self.video_page)
        self.video_filter.blockSignals(True);self.video_filter.setCurrentIndex(0);self.video_filter.blockSignals(False)
        self.result=path;self.player.setSource(QUrl.fromLocalFile(path));self.play.setEnabled(True);self.folder.setEnabled(True);self.status.setText('成片已生成，可播放核对并导出。');self.player.play();self.export.setEnabled(self.worker is None);self.release_panel.refresh();self.refresh_studio();self.select_video_by_id(self.current_attempt_id)
    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:self.player.play()
    def open_folder(self):
        if self.result:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result).parent)))
    def export_result(self):
        if not self.result or self.worker:return
        import re
        self.player.pause()
        title=self.video_title.text() or '视频'
        self.save_name.setText(re.sub(r'[\\/:*?"<>|]', '_', title)+'.mp4')
        self.confirm_save.setEnabled(True);self.save_panel.show()
        for widget in (self.play,self.export,self.folder,self.to_publish):widget.hide()
        self.save_name.setFocus();self.save_name.selectAll()
    def hide_save_panel(self):
        self.save_panel.hide()
        for widget in (self.play,self.export,self.folder,self.to_publish):widget.show()
    def save_export(self):
        if not self.result or self.worker:return
        try:
            name=self.save_name.text().strip()
            if not name or name in ('.','..') or Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('请填写文件名，不要在文件名中填写文件夹路径')
            if not name.lower().endswith('.mp4'):name+='.mp4'
            folder=self.save_directory.text().strip() if self.save_destination.currentIndex()==3 else self.save_destination.currentData()
            if not folder or not Path(folder).expanduser().is_dir():raise ValueError('保存文件夹不存在，请选择已有文件夹')
            path=str(Path(folder).expanduser()/name);source=self.result
            self.run_job(lambda c,p:export_video(source,path,c,p),lambda p:self.status.setText('已保存到电脑：'+p))
        except Exception as exc:self.on_error(str(exc))
    def cancel_job(self):
        if self.worker:self.worker.cancel.set();self.status.setText('正在取消，请稍候…')
    def persist(self):
        state=dict(contents=self.contents,material=self.material.text(),bgm=self.bgm.text(),draft_step=self.draft_step,
                   voice=self.voice.currentText() or self.restored_state.get('voice',''),rate=self.rate.currentText(),resolution=self.resolution.currentIndex(),volume=self.volume.value(),selected_content=self.selector.currentIndex())
        temp=self.workspace/'input.tmp';temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.workspace/'input.json')
    def restore(self):
        path=self.workspace/'input.json'
        if path.exists():
            try:
                state=json.loads(path.read_text(encoding='utf-8'));self.restored_state=state
                self.material.setText(state.get('material',''));self.bgm.setText(state.get('bgm',''))
                self.rate.setCurrentText(state.get('rate','1.0'));self.resolution.setCurrentIndex(state.get('resolution',0));self.volume.setValue(state.get('volume',15))
                self.draft_step=max(0,min(2,state.get('draft_step',0)))
                self.accept_contents(state.get('contents',[]));self.selector.setCurrentIndex(state.get('selected_content',0))
            except Exception as e:self.log.appendPlainText('保存的输入无法读取：'+str(e))
    def closeEvent(self,event):
        if self.closed:event.accept();return
        self.persist()
        if self.worker:self.close_pending=True;self.cancel_job();event.ignore()
        else:self.closed=True;self.timer.stop();self.player.stop();self.audition_player.stop();self.release_panel.close();self.instance_lock.unlock();event.accept()
