"""Navigation, draft editing and presentation of durable task records."""
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListWidgetItem,QMessageBox
from .media_library import inspect_folder
from .release_panels import LABELS

def sync_rows(widget,rows):
    """Keep native accessible items alive across refresh and filtering."""
    widget.blockSignals(True)
    existing={widget.item(i).data(Qt.UserRole):widget.item(i) for i in range(widget.count())}
    visible={r['id'] for r in rows}
    for key,item in existing.items():item.setHidden(key not in visible)
    for index,row in enumerate(rows):
        item=existing.get(row['id'])
        if item is None:
            item=QListWidgetItem();item.setData(Qt.UserRole,row['id']);widget.insertItem(index,item)
        if item.text()!=row['text']:item.setText(row['text'])
        if row.get('icon') is not None:item.setIcon(row['icon'])
        if 'checked' in row:
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked if row['checked'] else Qt.Unchecked)
        item.setHidden(False)
    if widget.currentItem() and widget.currentItem().isHidden():widget.setCurrentRow(-1)
    widget.blockSignals(False)

class StudioActions:
    def navigate(self,key):
        for name,b in self.nav.items():b.setChecked(name==key)
        if key in ('materials','content'):
            self.show_wizard(0 if key=='materials' else 1);return
        targets={'home':self.home_page,'videos':self.videos_page,'publications':self.publication_scroll,
                 'account':self.account_scroll,'help':self.help_scroll}
        self.pages.setCurrentWidget(targets[key])
        if key!='videos':self.player.pause()
        self.refresh_studio()
    def begin_creation(self):self.show_wizard(self.draft_step if self.contents or self.material.text() else 0)
    def show_wizard(self,step):
        self.draft_step=max(0,min(2,int(step)));self.pages.setCurrentWidget(self.creation_page);self.wizard.setCurrentIndex(self.draft_step)
        for key,b in self.nav.items():b.setChecked(key==('materials' if step==0 else 'content' if step==1 else 'home'))
        self.step_error.clear();self.update_steps()
        if hasattr(self,'player'):self.player.pause()
    def jump_step(self,n):
        if n<=self.wizard.currentIndex():self.show_wizard(n)
        else:
            while self.wizard.currentIndex()<n:
                before=self.wizard.currentIndex();self.advance_step()
                if self.wizard.currentIndex()==before:break
    def previous_step(self):self.show_wizard(self.wizard.currentIndex()-1)
    def advance_step(self):
        n=self.wizard.currentIndex()
        if n==0:
            if not self.material.text() or not Path(self.material.text()).is_dir():self.step_error.setText('请先选择素材文件夹。');return
            if not self.material_scan or self.material_scan['root']!=str(Path(self.material.text()).resolve()):
                self.step_error.setText('请先检查素材，确认有可用视频。');self.scan_current_material(advance=True);return
            if self.material_scan.get('errors') and not self.material_errors_accepted:
                self.step_error.setText('部分素材不可用，请先核对素材检查结果。');return
        if n==1:
            self.inline_content_changed()
            active=[c for c in self.contents if c.get('enabled',True)]
            if not active or any(not c.get('title','').strip() or not c.get('voice_text','').strip() for c in active):
                self.step_error.setText('请为勾选的文案填写标题和口播。');return
        self.show_wizard(min(2,n+1));self.persist()
    def update_steps(self,*_):
        n=self.wizard.currentIndex();self.previous.setVisible(n>0);self.next_step.setVisible(n<2);self.start_button.setVisible(n==2)
        self.next_step.setText('下一步：准备文案' if n==0 else '下一步：配音与画面')
        for i,b in enumerate(self.step_labels):
            b.setStyleSheet('color:#b74212;background:#fde9d8;border-color:#f1c9a9;' if i==n else '')
        count=sum(c.get('enabled',True) for c in self.contents)
        self.batch_button.setText('生成勾选的 %s 条'%count);self.batch_button.setVisible(count>1 and n==2)
        selected=self.contents[self.selector.currentIndex()] if 0<=self.selector.currentIndex()<len(self.contents) else {}
        self.generation_summary.setText('当前文案：'+selected.get('title','尚未选择')+'\n素材：'+(Path(self.material.text()).name if self.material.text() else '尚未选择')+'\n画面：'+self.resolution.currentText()+' · 背景音乐：'+(Path(self.bgm.text()).name if self.bgm.text() else '不添加')+'\n已勾选 %s 条文案；全部使用此素材文件夹。'%count)
    def scan_current_material(self,checked=False,advance=False):
        if self.worker:return
        root=self.material.text().strip()
        if not root:self.step_error.setText('请先选择素材文件夹。');return
        def done(result):
            self.material_scan=result;self.material_errors_accepted=not result['errors']
            sync_rows(self.material_list,[dict(id=item['path'],text='%s\n%.1f 秒'%(Path(item['path']).name,item['duration']),icon=QIcon(item['thumbnail']) if item['thumbnail'] else QIcon()) for item in result['items']])
            for i in range(self.material_list.count()):self.material_list.item(i).setToolTip(self.material_list.item(i).data(Qt.UserRole))
            self.status.setText('素材检查完成，可以继续准备文案。')
            self.material_status.setText('已检查：%s 个可用视频%s'%(len(result['items']),('；%s 个问题，请核对'%len(result['errors'])) if result['errors'] else ''))
            if result['errors']:
                answer=QMessageBox.question(self,'部分素材不可用','\n'.join(result['errors'])+'\n\n仅使用已检查通过的视频继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
                self.material_errors_accepted=answer==QMessageBox.Yes
            if advance and self.material_errors_accepted:self.show_wizard(1)
            self.persist()
        self.run_job(lambda c,p:inspect_folder(root,self.workspace/'thumbnails',c,p),done)
    def create_content(self):
        import uuid
        item=dict(external_id=uuid.uuid4().hex,title='',voice_text='',body='',tags='',enabled=True)
        self.accept_contents(self.contents+[item]);self.selector.setCurrentIndex(len(self.contents)-1);self.title_edit.setFocus()
    def select_content_row(self,row):
        if not self.editing_content and row>=0:
            ident=self.content_list.item(row).data(Qt.UserRole)
            index=next((i for i,c in enumerate(self.contents) if c['external_id']==ident),-1)
            self.selector.setCurrentIndex(index)
    def set_content_enabled(self,item):
        if self.editing_content:return
        row=next((i for i,c in enumerate(self.contents) if c['external_id']==item.data(Qt.UserRole)),-1)
        if 0<=row<len(self.contents):self.contents[row]['enabled']=item.checkState()==Qt.Checked;self.persist();self.update_steps()
    def inline_content_changed(self,*_):
        if self.editing_content or self.worker:return
        i=self.selector.currentIndex()
        if not 0<=i<len(self.contents):return
        self.contents[i].update(title=self.title_edit.text(),voice_text=self.voice_edit.toPlainText())
        self.editing_content=True
        self.selector.setItemText(i,self.contents[i]['title'] or '未命名文案')
        row=next((self.content_list.item(n) for n in range(self.content_list.count()) if self.content_list.item(n).data(Qt.UserRole)==self.contents[i]['external_id']),None)
        if row:row.setText(self.contents[i]['title'] or '未命名文案')
        self.editing_content=False
        self.content_hint.setText('口播 %s 字 · 修改自动保存在本机'%len(self.contents[i]['voice_text']))
        self.persist();self.update_steps()
    def sync_content_editor(self):
        self.editing_content=True
        i=self.selector.currentIndex()
        sync_rows(self.content_list,[dict(id=c['external_id'],text=c.get('title') or '未命名文案',checked=c.get('enabled',True)) for c in self.contents])
        self.content_list.blockSignals(True)
        ident=self.contents[i]['external_id'] if 0<=i<len(self.contents) else None
        for n in range(self.content_list.count()):
            if self.content_list.item(n).data(Qt.UserRole)==ident:self.content_list.setCurrentRow(n);break
        self.content_list.blockSignals(False)
        c=self.contents[i] if 0<=i<len(self.contents) else {}
        self.title_edit.setText(c.get('title',''));self.voice_edit.setPlainText(c.get('voice_text',''))
        self.content_hint.setText(('口播 %s 字 · 修改自动保存在本机'%len(c.get('voice_text',''))) if c else '导入文档或点击“直接写一条”开始。')
        self.editing_content=False;self.update_steps()
    def open_attempt(self,ident):
        if not ident:return
        job=self.repository.get_job(ident)
        self.release_panel.jobs.setCurrentIndex(self.release_panel.jobs.findData(ident))
        if job['status']=='SUCCEEDED':self.release_panel.open_job(ident)
        else:self.navigate('videos');self.select_video_by_id(ident)
    def select_video_by_id(self,ident):
        for i in range(self.video_list.count()):
            if self.video_list.item(i).data(Qt.UserRole)==ident:self.video_list.setCurrentRow(i);return
    def video_selected(self,item,previous=None):
        if not item:return
        ident=item.data(Qt.UserRole);job=self.repository.get_job(ident)
        self.release_panel.jobs.setCurrentIndex(self.release_panel.jobs.findData(ident))
        self.video_title.setText(job['request']['content'].get('title','未命名视频'))
        self.video_error.setText((job.get('error') or '')[:240])
        success=job['status']=='SUCCEEDED' and bool(job['result']) and Path(job['result']).is_file()
        self.release_panel.retry_button.setEnabled(job['status'] in ('FAILED','CANCELLED','INTERRUPTED','QUEUED') and self.worker is None)
        if success:
            self.current_attempt_id=ident;self.result=job['result']
            from PySide6.QtCore import QUrl
            if self.player.source()!=QUrl.fromLocalFile(self.result):self.player.setSource(QUrl.fromLocalFile(self.result))
            self.preview_stack.setCurrentWidget(self.video_page)
            records=[r for r in self.repository.publications() if r['attempt_id']==ident]
            self.video_status.setText('已生成 · '+('未提交发布' if not records else '发布记录：'+LABELS.get(records[0]['status'],records[0]['status'])))
        else:
            self.player.stop();self.result=None;self.current_attempt_id=None;self.preview_stack.setCurrentWidget(self.video_empty)
            self.video_status.setText(LABELS.get(job['status'],job['status'])+(' · 成片文件不可用' if job['status']=='SUCCEEDED' else ''))
        for b in (self.play,self.export,self.folder,self.to_publish):b.setEnabled(success and self.worker is None)
        self.release_panel.refresh_account()
    def thumbnail_icon(self,job):
        cached=self.workspace/'thumbnails'/('result-'+job['attempt_id']+'.jpg')
        existing=Path(job['result']).parent/'preview.jpg' if job.get('result') else cached
        return QIcon(str(cached if cached.exists() else existing)) if cached.exists() or existing.exists() else QIcon()
    def duration_text(self,job):
        import json
        if not job.get('result'):return ''
        try:
            value=json.loads((Path(job['result']).parent/'manifest.json').read_text()).get('duration')
            return (' · %.1f 秒'%value) if value else ''
        except (OSError,ValueError,TypeError):return ''
    def capture_preview_frame(self,frame):
        ident=self.current_attempt_id
        if not ident or not frame.isValid() or not self.result:return
        if self.player.source().toLocalFile()!=self.result:return
        dest=self.workspace/'thumbnails'/('result-'+ident+'.jpg')
        if dest.exists():return
        picture=frame.toImage()
        if picture.isNull():return
        dest.parent.mkdir(parents=True,exist_ok=True)
        if picture.scaled(180,320,Qt.KeepAspectRatio,Qt.SmoothTransformation).save(str(dest)):
            for listing in (self.recent,self.video_list):
                for i in range(listing.count()):
                    item=listing.item(i)
                    if item.data(Qt.UserRole)==ident:item.setIcon(QIcon(str(dest)))
    def refresh_studio(self,*_):
        if not hasattr(self,'recent'):return
        jobs=self.repository.jobs();selected=self.video_list.currentItem().data(Qt.UserRole) if self.video_list.currentItem() else self.current_attempt_id
        sync_rows(self.recent,[dict(id=j['attempt_id'],text=j['request']['content'].get('title','未命名视频')+'    ·    '+LABELS.get(j['status'],j['status']),icon=self.thumbnail_icon(j)) for j in jobs[:3]])
        self.recent.setVisible(bool(jobs));self.home_empty.setVisible(not jobs)
        self.home_start.setText('继续制作' if self.contents or self.material.text() else '制作第一条视频')
        f=self.video_filter.currentIndex();rows=[]
        for j in jobs:
            status=j['status']
            if f==1 and status!='SUCCEEDED' or f==2 and status not in ('RUNNING','QUEUED') or f==3 and status not in ('FAILED','CANCELLED','INTERRUPTED'):continue
            rows.append(dict(id=j['attempt_id'],text=j['request']['content'].get('title','未命名视频')+'\n'+LABELS.get(status,status)+self.duration_text(j)+'  ·  '+datetime.fromisoformat(j['created_at']).astimezone().strftime('%m-%d %H:%M'),icon=self.thumbnail_icon(j)))
        sync_rows(self.video_list,rows)
        self.video_list.blockSignals(True)
        for i in range(self.video_list.count()):
            item=self.video_list.item(i)
            if item.data(Qt.UserRole)==selected and not item.isHidden():self.video_list.setCurrentItem(item);break
        self.video_list.blockSignals(False)
        if self.video_list.currentItem():self.video_selected(self.video_list.currentItem())
        elif self.pages.currentWidget()==self.videos_page:
            self.player.stop();self.result=None;self.current_attempt_id=None;self.preview_stack.setCurrentWidget(self.video_empty)
            self.video_title.setText('选择一条视频');self.video_status.setText('当前筛选下没有选中的视频。');self.video_error.clear()
            for b in (self.play,self.export,self.folder,self.to_publish,self.release_panel.retry_button):b.setEnabled(False)
        self.update_steps()
    def update_playback_time(self,*_):
        def fmt(ms):return '%02d:%02d'%(ms//60000,(ms//1000)%60)
        self.time_label.setText(fmt(self.player.position())+' / '+fmt(self.player.duration()))
    def open_publish_page(self):
        if not self.current_attempt_id:return
        self.release_panel.refresh();self.navigate('publications')
    def load_demo_confirmed(self):
        if self.contents or self.material.text():
            if QMessageBox.question(self,'载入合成示例','示例会替换当前编辑中的文案与素材选择，已生成的视频保持不变。继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.load_demo();self.show_wizard(0)
    def show_progress(self,text):
        # Process command traces stay in the diagnostic log.
        if text.startswith('[') and '"' in text:return
        clean=text.split('\n')[0]
        self.status.setText(clean[:170])
