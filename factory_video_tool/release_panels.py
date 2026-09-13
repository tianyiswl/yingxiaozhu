"""Small editing, history and fixed-account publication surfaces."""
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QPlainTextEdit,QDialog,QLineEdit,QDialogButtonBox,QMessageBox,QInputDialog)
from .studio_widgets import page,button,label,divider
from .browser_login import DouyinBrowserSession
from .credentials import CredentialStore
from .providers.douyin_publisher import DouyinPublisher
from .publishing import PublicationService
from .providers.douyin_browser import DouyinBrowserPublisher
from .browser_publishing import BrowserPublicationService

LABELS={'QUEUED':'待开始','RUNNING':'生成中','SUCCEEDED':'已生成','FAILED':'失败','CANCELLED':'取消','INTERRUPTED':'中断',
        'READY':'待发布','UPLOADING':'上传中','SUBMITTING':'提交中','ACCEPTED':'平台已接受','UNKNOWN':'结果不明',
        'PREPARING':'上传预检中','PREPARED':'预检完成，待确认','COMMIT_CHECK':'提交前核对'}

class ContentEditor(QDialog):
    def __init__(self,content,parent=None):
        super().__init__(parent);self.setWindowTitle('核对本地文案');self.resize(820,700);self.setMinimumWidth(700)
        layout=QVBoxLayout(self);layout.setContentsMargins(28,24,28,24);layout.setSpacing(12);self.fields={}
        layout.addWidget(label('编辑文案','section'));layout.addWidget(label('标题用于作品展示；口播内容会用于配音。保存后会作为一个新版本保留。','muted'))
        specs=[('title','标题','输入视频标题',QLineEdit,42),('voice_text','口播文案（原文配音）','输入需要配音的完整口播内容',QPlainTextEdit,170),('body','发布正文','输入发布页展示的补充正文，可留空',QPlainTextEdit,130),('tags','标签','例如：工厂日常 产品展示；用空格或逗号分隔',QLineEdit,42)]
        for key,title,placeholder,kind,height in specs:
            layout.addWidget(label(title,'editorFieldLabel'));widget=kind();widget.setObjectName('editor'+key.title().replace('_',''));widget.setPlaceholderText(placeholder);widget.setMinimumHeight(height)
            if isinstance(widget,QLineEdit):widget.setText(content.get(key,''))
            else:widget.setPlainText(content.get(key,''))
            self.fields[key]=widget;layout.addWidget(widget)
        actions=QHBoxLayout();actions.addStretch();actions.addWidget(button('取消',self.reject));actions.addWidget(button('保存文案',self.accept,True));layout.addLayout(actions)
    def values(self):
        return {k:(w.text() if isinstance(w,QLineEdit) else w.toPlainText()).strip() for k,w in self.fields.items()}

class ReleasePanel(QWidget):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.repo=window.repository
        self.browser_session=DouyinBrowserSession(self.repo)
        self.browser_publisher=DouyinBrowserPublisher(self.browser_session)
        self.login_running=False
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(16)
        self.jobs_page,jobs_layout=page('我的视频','制作记录保留在电脑里，选中一条即可预览或继续处理。')
        self.jobs=QComboBox();jobs_layout.addWidget(self.jobs)
        row=QHBoxLayout();self.open_button=button('预览视频');self.retry_button=button('继续 / 重试这条任务')
        row.addWidget(self.open_button);row.addWidget(self.retry_button);jobs_layout.addLayout(row)
        self.open_button.clicked.connect(lambda:self.open_job(self.jobs.currentData()))
        self.retry_button.clicked.connect(self.retry)
        self.account_page,account_layout=page('账号与设置','制作视频无需登录；需要发布时，再连接抖音账号。')
        account_layout.addWidget(label('抖音账号','section'))
        self.account_label=label();account_layout.addWidget(self.account_label)
        self.connect_button=button('登录抖音',self.connect_account);account_layout.addWidget(self.connect_button)
        self.login_status=label('', 'muted');account_layout.addWidget(self.login_status)
        account_layout.addWidget(label('请在抖音官方窗口扫码。需要验证码时，在原窗口完成即可。','muted'))
        self.cancel_login_button=button('停止等待登录',window.cancel_job);self.cancel_login_button.setEnabled(False);account_layout.addWidget(self.cancel_login_button)
        self.inspect_button=button('检查抖音连接',self.inspect_editor);account_layout.addWidget(self.inspect_button)
        account_layout.addStretch()
        layout.addWidget(label('发布这条视频','section'))
        layout.addWidget(label('请先核对视频和账号。上传检查与公开发布是两个独立步骤。','muted'))
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setMinimumHeight(125);self.preview.setMaximumHeight(200);layout.addWidget(self.preview)
        self.prepare_button=button('上传到抖音并检查',self.prepare_publish);layout.addWidget(self.prepare_button)
        layout.addWidget(label('此按钮会上传视频，不会公开发布。','muted'))
        layout.addWidget(divider());layout.addWidget(label('已创建的发布记录','section'))
        self.publications=QComboBox();layout.addWidget(self.publications);self.publications.currentIndexChanged.connect(self.show_publication)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMinimumHeight(180);layout.addWidget(self.details)
        row=QHBoxLayout();self.publish_button=button('确认公开发布',self.publish,primary=True);self.discard_button=button('取消这次准备',self.discard_prepared)
        row.addWidget(self.publish_button);row.addWidget(self.discard_button);layout.addLayout(row)
        row=QHBoxLayout();self.query_button=button('检查发布结果',self.query);self.reconcile_button=button('手动核对作品',self.reconcile);row.addWidget(self.query_button);row.addWidget(self.reconcile_button);layout.addLayout(row)
        self.controls=[self.open_button,self.retry_button,self.connect_button,self.publish_button,self.query_button,self.reconcile_button,self.jobs,self.publications,self.inspect_button,self.prepare_button,self.discard_button]
        self.session_timer=QTimer(self);self.session_timer.timeout.connect(self.refresh_account);self.session_timer.start(1000)
        self.refresh()
    def service(self):
        account=self.repo.setting('fixed_account',{})
        if account.get('provider_id')=='douyin_browser' or not account.get('credential_ref'):
            return BrowserPublicationService(self.repo,self.browser_publisher)
        return PublicationService(self.repo,DouyinPublisher(lambda:CredentialStore().get(account.get('credential_ref',''))))
    def refresh(self):
        selected=self.jobs.currentData();pub_selected=self.publications.currentData()
        jobs=self.repo.jobs()
        self.jobs.blockSignals(True)
        for i,job in enumerate(jobs):
            text=LABELS.get(job['status'],job['status'])+' · '+job['request']['content'].get('title','')+' · '+job['created_at'][:19]
            if i>=self.jobs.count():self.jobs.addItem(text,job['attempt_id'])
            else:self.jobs.setItemText(i,text);self.jobs.setItemData(i,job['attempt_id'])
        self.jobs.blockSignals(False)
        index=self.jobs.findData(selected)
        if index>=0:self.jobs.setCurrentIndex(index)
        self.publications.blockSignals(True)
        for i,pub in enumerate(self.repo.publications()):
            text=LABELS.get(pub['status'],pub['status'])+' · '+pub['snapshot'].get('text','').split('\n')[0][:25]
            if i>=self.publications.count():self.publications.addItem(text,pub['publication_id'])
            else:self.publications.setItemText(i,text);self.publications.setItemData(i,pub['publication_id'])
        index=self.publications.findData(pub_selected)
        if index>=0:self.publications.setCurrentIndex(index)
        self.publications.blockSignals(False)
        account=self.repo.setting('fixed_account',{})
        self.refresh_account()
        ready=False
        ident=getattr(self.window,'current_attempt_id',None)
        if ident:
            job=self.repo.get_job(ident);c=job['request']['content'];text='\n'.join(c.get(k,'') for k in ('title','body','tags') if c.get(k))
            self.preview.setPlainText('目标：'+account.get('display_name','未连接')+'\n视频：'+c.get('title','未命名视频')+'\n\n实际发布文字：\n'+text)
            ready=job['status']=='SUCCEEDED' and bool(account.get('verified')) and bool(account.get('credential_ref')) and account.get('provider_id')!='douyin_browser'
        else:self.preview.setPlainText('先生成一条视频，或打开历史成功结果。')
        self.publish_button.setEnabled(ready and self.window.worker is None)
        self.show_publication()
    def refresh_account(self):
        account=self.repo.setting('fixed_account',{})
        status=self.repo.setting('douyin_session',{})
        self.account_label.setText('固定抖音账号：'+account.get('display_name','未连接'))
        self.login_status.setText(status.get('message','点击登录抖音'))
        self.connect_button.setText('检查登录 / 打开抖音' if account.get('verified') else '登录抖音')
        busy=self.window.worker is not None
        self.inspect_button.setEnabled(bool(account.get('verified')) and not busy)
        current=getattr(self.window,'current_attempt_id',None)
        generated=bool(current and self.repo.get_job(current)['status']=='SUCCEEDED')
        self.prepare_button.setEnabled(bool(account.get('verified')) and generated and not busy)
        selected=self.publications.currentData() if hasattr(self,'publications') else None
        record=self.repo.get_publication(selected) if selected else {}
        self.publish_button.setText('确认公开发布这条记录')
        self.publish_button.setEnabled(record.get('status')=='PREPARED' and bool(account.get('verified')) and not busy)
        self.discard_button.setEnabled(record.get('status') in ('PREPARED','INTERRUPTED','FAILED') and not record.get('receipt',{}).get('click_intent_at') and not busy)
        self.cancel_login_button.setEnabled(busy)
        self.cancel_login_button.setText('停止当前操作' if busy and not self.login_running else '停止等待登录')
    def set_busy(self,busy):
        for w in self.controls:w.setEnabled(not busy)
        self.cancel_login_button.setEnabled(busy and self.login_running)
        if not busy:self.refresh()
    def open_job(self,ident):
        if not ident:return
        job=self.repo.get_job(ident)
        if job['status']=='SUCCEEDED' and job['result'] and Path(job['result']).is_file():
            self.window.current_attempt_id=ident;self.window.generated(job['result']);self.refresh()
        else:self.window.on_error(job.get('error') or '此任务没有成功成片，可查看状态或重试。')
    def retry(self):
        ident=self.jobs.currentData()
        if not ident:return
        job=self.repo.get_job(ident)
        if job['status'] not in ('FAILED','CANCELLED','INTERRUPTED','QUEUED'):
            self.window.on_error('该任务无需重试，请先核对现有结果。');return
        def action(cancel,progress):
            if job['status']=='QUEUED':new=job
            else:new=self.window.workflow.enqueue(job['request'],job_id=job['job_id'])
            return self.window.workflow.run([new['attempt_id']],cancel,progress)
        self.window.run_job(action,self.window.batch_generated)
    def connect_account(self):
        if self.window.worker is not None:return
        self.login_running=True
        self.window.status.setText('请在抖音窗口扫码登录')
        self.window.run_job(self.browser_session.login,self.account_connected)
        self.window.worker.finished.connect(self.login_finished)
    def account_connected(self,account):
        self.refresh();self.window.status.setText('已登录抖音：'+account['display_name'])
    def login_finished(self):
        self.login_running=False;self.cancel_login_button.setEnabled(False);self.refresh_account()
    def inspect_editor(self):
        def done(result):
            from .models import atomic_json
            atomic_json(self.window.workspace/'douyin-editor-check.json',result)
            self.window.status.setText('发布入口已核对：'+str(result['upload_inputs'])+'个上传控件；未上传、未发布')
            self.login_status.setText('发布入口已核对，未上传视频')
        self.window.run_job(self.browser_publisher.inspect_editor,done)
    def prepare_publish(self):
        ident=getattr(self.window,'current_attempt_id',None)
        if not ident:return
        self.window.run_job(lambda c,p:self.service().prepare(ident,c,p),self.published)
    def discard_prepared(self):
        ident=self.publications.currentData()
        if ident:self.window.run_job(lambda c,p:self.service().cancel_prepared(ident,c,p),self.published)
    def close(self):
        self.session_timer.stop();self.browser_session.close()
        return super().close()
    def publish(self):
        account=self.repo.setting('fixed_account',{})
        if account.get('provider_id')=='douyin_browser':
            ident=self.publications.currentData()
            if not ident:return
            record=self.repo.get_publication(ident)
            if record['status']!='PREPARED':return
            snap=record['snapshot'];content=snap['content']
            message='账号：'+record['account']['display_name']+'\n成片：'+snap['video_path']+'\n标题：'+content['title']+'\n正文：'+content['body']+'\n话题：'+'、'.join(content['topics'])+'\n\n已核对视频及官方页面内容，确认公开发布这一条？'
            answer=QMessageBox.question(self,'确认公开发布这一条',message,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if answer==QMessageBox.StandardButton.Yes:self.window.run_job(lambda c,p:self.service().submit(ident,c,p),self.published)
            return
        ident=getattr(self.window,'current_attempt_id',None)
        if not ident:return
        account=self.repo.setting('fixed_account',{})
        if account.get('provider_id')=='douyin_browser' or not account.get('verified'):
            self.window.on_error('网页自动发布尚待登录后的真实页面联调，当前可先预览和导出视频');return
        self.refresh()
        answer=QMessageBox.question(self,'确认公开提交到固定抖音账号',self.preview.toPlainText()+'\n\n确认已完整预览本条视频，并提交这一条？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        account=self.repo.setting('fixed_account',{});service=self.service()
        def action(cancel,progress):
            record=service.prepare(ident,account)
            return service.submit(record['publication_id'],cancel,progress)
        self.window.run_job(action,self.published)
    def published(self,result):
        self.refresh();i=self.publications.findData(result['publication_id']);self.publications.setCurrentIndex(i)
        self.refresh_account()
        self.window.status.setText('当前状态：'+LABELS.get(result['status'],result['status'])+('；请核对官方页面后确认发布。' if result['status']=='PREPARED' else '；请核对记录。'))
        if hasattr(self.window,'refresh_studio'):self.window.refresh_studio()
    def query(self):
        ident=self.publications.currentData()
        if ident:self.window.run_job(lambda c,p:self.service().query(ident,c),self.published)
    def reconcile(self):
        ident=self.publications.currentData()
        if not ident:return
        item,ok=QInputDialog.getText(self,'核对结果不明的提交','粘贴该账号实际作品ID（只查询，不重新提交）：')
        if ok and item.strip():self.window.run_job(lambda c,p:self.service().reconcile(ident,item,c),self.published)
    def show_publication(self,*_):
        ident=self.publications.currentData()
        if not ident:self.details.setPlainText('暂无发布记录。');return
        record=self.repo.get_publication(ident);receipt=record['receipt'];query=receipt.get('query',{})
        not_submitted=record['status'] in ('CANCELLED','READY','PREPARING','PREPARED','FAILED','INTERRUPTED') and not receipt.get('click_intent_at') and not receipt.get('submitted') and not receipt.get('item_id')
        state='已取消，未提交' if record['status']=='CANCELLED' and not_submitted else LABELS.get(record['status'],record['status'])
        snapshot=record['snapshot'];content=snapshot.get('content',{})
        message=['账号：'+record['account'].get('display_name',''),
                 '视频：'+content.get('title',snapshot.get('text','未命名视频')).split('\n')[0],
                 '提交：'+state]
        if content:message.extend(['发布正文：'+content.get('body',''),'话题：'+'、'.join(content.get('topics',[]))])
        if not_submitted:message.append('公开：未提交发布')
        else:
            def friendly(value):return '尚未确认' if value in (None,'UNKNOWN','') else str(value)
            message.extend(['作品ID：'+str(receipt.get('item_id','尚未取得')),
                '审核：'+friendly(query.get('review')),'公开：'+friendly(query.get('visibility')),
                '文字核对：'+('一致' if query.get('content_matches') is True else '尚未确认')])
        if receipt.get('error'):message.append('原因：'+receipt['error'])
        if query.get('candidate_url'):message.append('作品地址（需打开核对）：'+query['candidate_url'])
        if receipt.get('preflight'):message.append('页面预检：标题、正文、官方话题已回读；提交情况见上方状态')
        if receipt.get('reason'):message.append('待核对：'+receipt['reason'])
        self.details.setPlainText('\n'.join(message))
        self.refresh_account()
