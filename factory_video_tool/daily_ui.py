"""Daily workbench: prepare once, start a finite batch, handle only exceptions."""
import json
import time
import uuid
from functools import cmp_to_key
from pathlib import Path
from datetime import datetime,timedelta
from PySide6.QtCore import Qt,QTimer,QDate,QTime,QUrl,QLockFile,QTimeZone,QLocale,QCollator
from PySide6.QtGui import QPixmap,QDesktopServices,QFont,QFontDatabase,QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QFrame,QStackedWidget,QStackedLayout,QSpinBox,
 QDateEdit,QTimeEdit,QListWidget,QListWidgetItem,QFileDialog,QComboBox,QInputDialog,QDialog,QPlainTextEdit,QLineEdit,QFontComboBox,QColorDialog,QCheckBox,QRadioButton,QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QMenu,QGridLayout,QMessageBox)
from .repository import Repository
from .batch_store import BatchStore
from .batch_planner import BatchPlanner,SHANGHAI
from .batch_client import ensure_worker
from .library import Library,VIDEO_EXT,AUDIO_EXT,DOC_EXT
from .content_template import write_content_template
from .platforms import PathResolver
from .studio_widgets import STYLE,label,button,page,scroll,divider
from .release_panels import ContentEditor

NAMES={'ACTIVE':'进行中','PAUSED':'已暂停','NEEDS_ATTENTION':'需要处理','COMPLETED':'已完成','CANCELLED':'已取消',
 'QUEUED':'待制作','GENERATING':'制作中','READY':'等待处理：近期本机等待，远期抖音定时','PREPARING':'上传检查中','SUBMITTING':'提交中',
 'ACCEPTED':'平台已接收','PUBLISHED':'已公开','UNKNOWN':'提交结果待核对','FAILED':'需要处理','SIMULATED':'模拟完成','GENERATED':'已制作'}
def stamp(value):return datetime.fromtimestamp(value,SHANGHAI).strftime('%m月%d日 %H:%M')
def duration_text(seconds):
    seconds=max(0,round(float(seconds)))
    return '%s 秒'%seconds if seconds<60 else '%s分%02d秒'%(seconds//60,seconds%60)

def voice_label(value):
    if '\\' not in value:return value
    token=value.rsplit('\\',1)[-1]
    for key,title in [('HUIHUI','慧慧 · 中文'),('XIAOXIAO','晓晓 · 中文'),('KANGKANG','康康 · 中文'),('YAoyao','瑶瑶 · 中文'),('HANHAN','涵涵 · 中文'),('ZHIWEI','志伟 · 中文')]:
        if key.upper() in token.upper():return title
    return token.removeprefix('TTS_MS_').replace('_',' ')[:32]

class DailyWindow(QWidget):
    def __init__(self,workspace=None,simulation=False,start_worker=True):
        super().__init__();self.simulation=simulation;self.auto_worker=start_worker
        self.workspace=PathResolver(workspace).prepare();self.repository=Repository(self.workspace);self.store=BatchStore(self.repository)
        mode=self.repository.setting('daily_worker_mode')
        if mode and mode!=('simulate' if simulation else 'live'):raise ValueError('请使用与资料库一致的运行模式')
        self.instance_lock=QLockFile(str(self.workspace/'daily-window.lock'));self.instance_lock.setStaleLockTime(0)
        if not self.instance_lock.tryLock(0):raise ValueError('这个工作台已经打开，请回到已有窗口')
        self.request_key=uuid.uuid4().hex;self.defaults_loaded=False;self.last_commands=None;self._restoring=False;self.started_batch_id=None
        self.setWindowTitle('映小助 · 商家短视频助手'+(' · 本地模拟' if simulation else ''))
        self.resize(1280,860);self.setMinimumSize(1020,760);self.setStyleSheet(STYLE+'QDateEdit,QTimeEdit {background:white;color:#192633;border:1px solid #dcdedc;border-radius:6px;padding:9px;} QComboBox#settingsSelect,QComboBox#bgmChoice,QFontComboBox {padding-right:30px;} QComboBox:disabled,QSpinBox:disabled {background:#f2f1ef;color:#9ba2a7;border-color:#e1dfdc;} QLabel:disabled {color:#9ba2a7;}');self.setAcceptDrops(True)
        self.path_inputs={};self.import_dialogs=[];self.build();self.restore();self.refresh()
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1500)
        if start_worker:QTimer.singleShot(0,self.start_background)
    def build(self):
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        side=QFrame();side.setObjectName('sidebar');side.setFixedWidth(210);nav=QVBoxLayout(side);nav.setContentsMargins(20,30,20,24)
        nav.addWidget(label('映小助','section'));nav.addWidget(label('准备一次，每天一键','muted'));nav.addSpacing(30)
        self.pages=QStackedWidget();self.nav={}
        for key,title in [('home','每日工作台'),('materials','素材库'),('contents','文案库'),('bgm','背景音库'),('tasks','任务与视频'),('settings','账号与设置')]:
            b=button(title,lambda checked=False,k=key:self.navigate(k));b.setCheckable(True);b.setProperty('nav',True);nav.addWidget(b);self.nav[key]=b
        nav.addStretch()
        feedback=QFrame();feedback.setObjectName('feedbackCard');feedback_layout=QVBoxLayout(feedback);feedback_layout.setContentsMargins(14,14,14,14);feedback_layout.setSpacing(6)
        feedback_layout.addWidget(label('问题反馈','feedbackTitle'))
        feedback_layout.addWidget(label('产品仍在持续优化中。使用中遇到任何问题，或有功能优化建议，欢迎联系我。','feedbackBody'))
        feedback_layout.addWidget(label('微信：sj1337622','feedbackContact'))
        feedback_layout.addWidget(label('淘宝店铺：逆浪风','feedbackContact'))
        feedback_layout.addSpacing(4)
        feedback_layout.addWidget(label('免费定制小工具','feedbackPrompt'))
        feedback_layout.addWidget(label('告诉我你想解决的重复工作。','feedbackBody'))
        nav.addWidget(feedback)
        layout.addWidget(side);layout.addWidget(self.pages,1);self.targets={}
        self.build_home();self.build_library('materials');self.build_library('bgm');self.build_library('contents');self.build_tasks();self.build_settings()
        self.navigate('home')
    def add_page(self,key,title,subtitle):
        w,l=page(title,subtitle);s=scroll(w);self.pages.addWidget(s);self.targets[key]=s;return l
    def navigate(self,key):
        if key not in self.targets:return
        self.pages.setCurrentWidget(self.targets[key])
        for k,b in self.nav.items():b.setChecked(k==key)
    def account_action(self):
        if self.simulation or self.repository.setting('fixed_account',{}).get('verified'):self.navigate('settings')
        else:self.queue('LOGIN')
    def build_home(self):
        l=self.add_page('home','今天的视频，交给工作台','素材和文案会一直保留。每天选好数量和时间，剩下的自动完成。')
        top=QHBoxLayout();self.readiness={}
        for key,title,action in [('account','抖音账号',self.account_action),('materials','素材库',lambda:self.navigate('materials')),('contents','文案库',lambda:self.navigate('contents'))]:
            card=QFrame();card.setStyleSheet('QFrame {background:#faf4eb;border-radius:12px;}');col=QVBoxLayout(card)
            col.addWidget(label(title,'section'));info=label('准备中','muted');col.addWidget(info);self.readiness[key]=info
            b=button('查看账号' if key=='account' else '添加'+('素材' if key=='materials' else '文案'),action)
            if key=='account':self.account_button=b
            col.addWidget(b);top.addWidget(card)
        l.addLayout(top)
        main=QHBoxLayout();left=QVBoxLayout();left.addWidget(label('安排这次任务','section'));form=QFormLayout();form.setSpacing(14)
        self.count=QSpinBox();self.count.setRange(1,100);self.count.setSuffix(' 条')
        self.days=QSpinBox();self.days.setRange(1,30);self.days.setSuffix(' 天')
        locale=QLocale(QLocale.Language.Chinese,QLocale.Country.China)
        self.schedule_date=QDateEdit();self.schedule_date.setDisplayFormat('MM月dd日');self.schedule_date.setCalendarPopup(True);self.schedule_date.setFixedWidth(104);self.schedule_date.setLocale(locale);self.schedule_date.calendarWidget().setLocale(locale)
        self.schedule_time=QTimeEdit();self.schedule_time.setDisplayFormat('HH:mm');self.schedule_time.setFixedWidth(82);self.schedule_time.setLocale(locale)
        self.immediate_toggle=button('即时发布');self.immediate_toggle.setProperty('mode',True);self.immediate_toggle.setCheckable(True);self.immediate_toggle.setMinimumWidth(104);self.immediate_toggle.setMinimumHeight(44);self.immediate_toggle.setChecked(True)
        self.scheduled_toggle=button('定时发布');self.scheduled_toggle.setProperty('mode',True);self.scheduled_toggle.setCheckable(True);self.scheduled_toggle.setMinimumWidth(104);self.scheduled_toggle.setMinimumHeight(44)
        modes=QWidget();modes.setObjectName('scheduleModes');mode_layout=QHBoxLayout(modes);mode_layout.setContentsMargins(0,0,0,0);mode_layout.setSpacing(6);mode_layout.addWidget(self.immediate_toggle);mode_layout.addWidget(self.scheduled_toggle)
        self.schedule_slot=QWidget();self.schedule_slot.setFixedSize(230,44);self.schedule_stack=QStackedLayout(self.schedule_slot);self.schedule_stack.setContentsMargins(0,0,0,0)
        self.schedule_placeholder=label('选择定时后设置发布时间','schedulePlaceholder');self.schedule_placeholder.setAlignment(Qt.AlignCenter)
        schedule_controls=QWidget();schedule_layout=QHBoxLayout(schedule_controls);schedule_layout.setContentsMargins(0,0,0,0);schedule_layout.setSpacing(6);schedule_layout.addWidget(self.schedule_date);schedule_layout.addWidget(self.schedule_time);schedule_layout.addStretch()
        self.schedule_stack.addWidget(self.schedule_placeholder);self.schedule_stack.addWidget(schedule_controls);mode_layout.addWidget(self.schedule_slot);mode_layout.addStretch()
        self.interval_hours=QSpinBox();self.interval_hours.setRange(0,23);self.interval_hours.setFixedWidth(70);self.interval_hours.setAccessibleName('间隔小时')
        self.interval_minutes=QSpinBox();self.interval_minutes.setRange(0,59);self.interval_minutes.setFixedWidth(70);self.interval_minutes.setAccessibleName('间隔分钟')
        interval_controls=QWidget();interval_layout=QHBoxLayout(interval_controls);interval_layout.setContentsMargins(0,0,0,0);interval_layout.setSpacing(6);interval_layout.addWidget(self.interval_hours);interval_layout.addWidget(label('小时','timeUnit'));interval_layout.addWidget(self.interval_minutes);interval_layout.addWidget(label('分钟','timeUnit'));interval_layout.addStretch()
        for title,w in [('每天制作',self.count),('制作天数',self.days),('发布方式',modes),('间隔发布',interval_controls)]:form.addRow(title,w)
        left.addLayout(form);main.addLayout(left,3)
        art=label();pix=QPixmap(str(Path(__file__).parent/'assets/home-hero.png'));art.setPixmap(pix.scaled(280,240,Qt.KeepAspectRatio,Qt.SmoothTransformation));art.setAlignment(Qt.AlignCenter);main.addWidget(art,2);l.addLayout(main)
        self.summary=label();l.addWidget(self.summary)
        self.start_button=button('一键制作并模拟排期' if self.simulation else '一键制作并安排发布',self.create_batch,True);l.addWidget(self.start_button)
        self.issue=label('','error');l.addWidget(self.issue);self.overview=label('还没有任务。准备好资料后，就可以开始。','muted');l.addWidget(self.overview)
        for w in [self.count,self.days,self.interval_hours,self.interval_minutes]:w.valueChanged.connect(self.form_changed)
        self.schedule_date.dateChanged.connect(self.form_changed);self.schedule_time.timeChanged.connect(self.form_changed)
        self.immediate_toggle.toggled.connect(lambda checked:self.set_publish_mode(True) if checked else None)
        self.scheduled_toggle.toggled.connect(lambda checked:self.set_publish_mode(False) if checked else None)
    def build_library(self,key):
        if key=='materials':self.build_material_library()
        elif key=='bgm':self.build_bgm_library()
        elif key=='contents':self.build_content_library()

    def add_selection_toolbar(self,layout,table,kind):
        row=layout
        row.addSpacing(8)
        row.addWidget(button('全选',table.selectAll));row.addWidget(button('全不选',table.clearSelection))
        remove=button('删除选中',lambda:self.delete_selected(table,kind));remove.setEnabled(False)
        table.itemSelectionChanged.connect(lambda:remove.setEnabled(bool(table.selectionModel().selectedRows())))
        row.addWidget(remove)
        from PySide6.QtWidgets import QPushButton
        for index in range(row.count()):
            control=row.itemAt(index).widget()
            if isinstance(control,QPushButton):
                control.setMinimumHeight(34);control.setStyleSheet('QPushButton {padding:6px 9px;font-size:13px;min-height:20px;}')

    def delete_selected(self,table,kind):
        selected=[(table.item(index.row(),0).data(Qt.UserRole),table.item(index.row(),1 if kind=='task' else 0).text()) for index in table.selectionModel().selectedRows()]
        ids=[ident for ident,_ in selected]
        if not ids:return
        note='删除任务列表记录，不会删除成片或撤回抖音作品。执行中或结果待核对的任务需先处理。' if kind=='task' else '删除所选资料库内容；原始导入文件保留。正在被任务使用的内容会跳过。'
        if QMessageBox.question(self,'删除选中','确定删除选中的 %s 条？\n%s'%(len(ids),note),QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        errors=[];deleted=0
        action=self.store.delete_task_record if kind=='task' else getattr(Library(self.store),'remove_'+kind)
        for ident,name in selected:
            try:action(ident);deleted+=1
            except Exception as exc:errors.append('“%s”：%s'%(name,str(exc)))
        self.refresh()
        result=QMessageBox(self);result.setWindowTitle('删除结果')
        result.setIcon(QMessageBox.Icon.Warning if errors else QMessageBox.Icon.Information)
        result.setText('已删除 %s 条，未删除 %s 条。'%(deleted,len(errors)))
        if errors:
            result.setInformativeText('\n\n'.join(errors[:5])+('\n\n其余原因请展开下方详细信息查看。' if len(errors)>5 else ''))
            if len(errors)>5:result.setDetailedText('\n\n'.join(errors))
        result.exec()

    def build_material_library(self):
        l=self.add_page('materials','素材库','导入仅记录原文件路径，不复制视频。请保留原文件，并保持所在硬盘连接。')
        l.setContentsMargins(30,16,30,16);l.setSpacing(10)
        summary=QFrame();summary.setObjectName('librarySummary');summary.setFixedHeight(70);summary_row=QHBoxLayout(summary);summary_row.setContentsMargins(10,8,10,8);summary_row.setSpacing(8)
        self.material_total_metric=self.library_metric('0','可用素材')
        self.material_duration_metric=self.library_metric('0 秒','总时长')
        self.material_latest_metric=self.library_metric('—','最近导入')
        for metric in (self.material_total_metric,self.material_duration_metric,self.material_latest_metric):summary_row.addWidget(metric)
        summary_row.addStretch();l.addWidget(summary)
        panel=QFrame();panel.setObjectName('materialPanel');panel_layout=QVBoxLayout(panel);panel_layout.setContentsMargins(16,12,16,12);panel_layout.setSpacing(8)
        toolbar=QHBoxLayout();toolbar.addWidget(label('视频素材','taskPanelTitle'));toolbar.addStretch();import_button=button('批量添加视频',lambda:self.pick_files('materials'),True);folder_button=button('添加文件夹',lambda:self.pick_folder('materials'));import_button.setMinimumHeight(38);folder_button.setMinimumHeight(38);toolbar.addWidget(import_button);toolbar.addWidget(folder_button);panel_layout.addLayout(toolbar)
        finder=QHBoxLayout();self.material_search=QLineEdit();self.material_search.setObjectName('materialSearch');self.material_search.setPlaceholderText('搜索素材名称');self.material_search.setClearButtonEnabled(True);self.material_search.textChanged.connect(self.refresh);finder.addWidget(self.material_search,1);finder.addWidget(label('排序','taskHint'));self.material_sort=QComboBox();self.material_sort.setObjectName('materialSort');self.material_sort.addItems(['最近导入','时长：长到短','时长：短到长','名称：升序','名称：降序']);self.material_sort.currentIndexChanged.connect(self.refresh);finder.addWidget(self.material_sort);panel_layout.addLayout(finder)
        panel_layout.addWidget(label('可直接拖入视频或文件夹 · 支持 MP4、MOV、MKV、AVI · 双击预览，右键打开视频或文件夹','taskHint'))
        self.material_table=QTableWidget(0,4);self.material_table.setObjectName('materialTable');self.material_table.setHorizontalHeaderLabels(['素材名称','时长','导入时间','状态']);self.material_table.verticalHeader().setVisible(False);self.material_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.material_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.material_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.material_table.setAlternatingRowColors(True);self.material_table.setShowGrid(False)
        header=self.material_table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for column in (1,2,3):header.setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        self.material_table.cellDoubleClicked.connect(self.open_material_row);self.material_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.material_table.customContextMenuRequested.connect(self.show_material_menu);self.add_selection_toolbar(toolbar,self.material_table,'material');panel_layout.addWidget(self.material_table,1)
        self.material_import_status=label('','error');panel_layout.addWidget(self.material_import_status);l.addWidget(panel,1)

    def library_metric(self,value,hint):
        metric=QFrame();metric.setObjectName('libraryMetricCard');metric.setMinimumWidth(142);metric.setMaximumHeight(54);layout=QVBoxLayout(metric);layout.setContentsMargins(14,5,14,5);layout.setSpacing(0);value_label=label(value,'libraryMetric');value_label.setWordWrap(False);layout.addWidget(value_label);hint_label=label(hint,'libraryMetricHint');hint_label.setWordWrap(False);layout.addWidget(hint_label);metric.value_label=value_label
        return metric

    def build_bgm_library(self):
        l=self.add_page('bgm','背景音库','把背景音乐放在这里。选中的音乐会自动混入之后新制作的视频。')
        l.setContentsMargins(30,16,30,16);l.setSpacing(10)
        summary=QFrame();summary.setObjectName('librarySummary');summary.setFixedHeight(70);summary_row=QHBoxLayout(summary);summary_row.setContentsMargins(10,8,10,8);summary_row.setSpacing(8)
        self.bgm_total_metric=self.library_metric('0','可用音乐');self.bgm_duration_metric=self.library_metric('0 秒','总时长');self.bgm_default_metric=self.library_metric('未选择','默认 BGM')
        for metric in (self.bgm_total_metric,self.bgm_duration_metric,self.bgm_default_metric):summary_row.addWidget(metric)
        summary_row.addStretch();l.addWidget(summary)
        panel=QFrame();panel.setObjectName('materialPanel');panel_layout=QVBoxLayout(panel);panel_layout.setContentsMargins(16,12,16,12);panel_layout.setSpacing(8)
        toolbar=QHBoxLayout();toolbar.addWidget(label('背景音乐','taskPanelTitle'));toolbar.addStretch();import_button=button('批量添加音乐',lambda:self.pick_files('bgm'),True);folder_button=button('添加文件夹',lambda:self.pick_folder('bgm'));import_button.setMinimumHeight(38);folder_button.setMinimumHeight(38);toolbar.addWidget(import_button);toolbar.addWidget(folder_button);panel_layout.addLayout(toolbar)
        finder=QHBoxLayout();self.bgm_search=QLineEdit();self.bgm_search.setPlaceholderText('搜索音乐名称');self.bgm_search.setClearButtonEnabled(True);self.bgm_search.textChanged.connect(self.refresh);finder.addWidget(self.bgm_search,1);panel_layout.addLayout(finder)
        panel_layout.addWidget(label('支持 MP3、WAV、M4A、AAC、FLAC、OGG · 双击试听，右键可设为默认音乐或打开所在文件夹','taskHint'))
        self.bgm_table=QTableWidget(0,4);self.bgm_table.setHorizontalHeaderLabels(['音乐名称','时长','导入时间','状态']);self.bgm_table.verticalHeader().setVisible(False);self.bgm_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.bgm_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.bgm_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.bgm_table.setAlternatingRowColors(True);self.bgm_table.setShowGrid(False)
        header=self.bgm_table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for column in (1,2,3):header.setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        self.bgm_table.cellDoubleClicked.connect(self.open_bgm_row);self.bgm_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.bgm_table.customContextMenuRequested.connect(self.show_bgm_menu);self.add_selection_toolbar(toolbar,self.bgm_table,'bgm');panel_layout.addWidget(self.bgm_table,1)
        self.bgm_import_status=label('','error');panel_layout.addWidget(self.bgm_import_status);l.addWidget(panel,1)

    def build_content_library(self):
        l=self.add_page('contents','文案库','把标题、口播文案和标签统一保存在这里。未用文案会自动分配到新任务。')
        l.setContentsMargins(30,16,30,16);l.setSpacing(10)
        summary=QFrame();summary.setObjectName('contentSummary');summary.setFixedHeight(70);summary_row=QHBoxLayout(summary);summary_row.setContentsMargins(10,8,10,8);summary_row.setSpacing(8)
        self.content_total_metric=self.content_metric('0','全部文案');self.content_available_metric=self.content_metric('0','未用');self.content_reserved_metric=self.content_metric('0','已安排');self.content_used_metric=self.content_metric('0','已使用')
        for metric in (self.content_total_metric,self.content_available_metric,self.content_reserved_metric,self.content_used_metric):summary_row.addWidget(metric)
        summary_row.addStretch();l.addWidget(summary)
        panel=QFrame();panel.setObjectName('contentPanel');panel_layout=QVBoxLayout(panel);panel_layout.setContentsMargins(16,12,16,12);panel_layout.setSpacing(8)
        toolbar=QHBoxLayout();toolbar.addWidget(label('文案内容','taskPanelTitle'));toolbar.addStretch();import_button=button('批量添加文档',lambda:self.pick_files('contents'),True);folder_button=button('添加文件夹',lambda:self.pick_folder('contents'));write_button=button('写一条文案',lambda:self.edit_content())
        for control in (import_button,folder_button,write_button):control.setMinimumHeight(38);toolbar.addWidget(control)
        panel_layout.addLayout(toolbar)
        finder=QHBoxLayout();self.content_search=QLineEdit();self.content_search.setObjectName('contentSearch');self.content_search.setPlaceholderText('搜索标题、文案或标签');self.content_search.setClearButtonEnabled(True);self.content_search.textChanged.connect(self.refresh);finder.addWidget(self.content_search,1);finder.addWidget(label('状态','taskHint'));self.content_filter=QComboBox();self.content_filter.setObjectName('contentFilter');self.content_filter.addItems(['全部文案','未用','已安排','已使用','已停用']);self.content_filter.currentIndexChanged.connect(self.refresh);finder.addWidget(self.content_filter);finder.addWidget(label('制作时','taskHint'));self.content_selection=QComboBox();self.content_selection.setObjectName('contentSelection');self.content_selection.setMinimumWidth(176);self.content_selection.addItem('按导入顺序使用','sequence');self.content_selection.addItem('随机使用文案','random');self.content_selection.setCurrentIndex(max(0,self.content_selection.findData(self.repository.setting('content_selection_mode','sequence'))));self.content_selection.currentIndexChanged.connect(self.save_content_selection_mode);finder.addWidget(self.content_selection);panel_layout.addLayout(finder)
        panel_layout.addWidget(label('可直接拖入文档或文件夹 · 支持 TXT、DOCX、XLSX · 双击修改，右键可修改或停用文案','taskHint'))
        self.content_table=QTableWidget(0,5);self.content_table.setObjectName('contentTable');self.content_table.setHorizontalHeaderLabels(['标题','文案','标签','状态','导入时间']);self.content_table.verticalHeader().setVisible(False);self.content_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.content_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.content_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.content_table.setAlternatingRowColors(True);self.content_table.setShowGrid(False)
        header=self.content_table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        for column in (2,3,4):header.setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        self.content_table.cellDoubleClicked.connect(self.edit_content_row);self.content_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.content_table.customContextMenuRequested.connect(self.show_content_menu);self.add_selection_toolbar(toolbar,self.content_table,'content');panel_layout.addWidget(self.content_table,1)
        self.document_import_status=label('','error');panel_layout.addWidget(self.document_import_status);l.addWidget(panel,1)

    def content_metric(self,value,hint):
        metric=self.library_metric(value,hint);metric.setObjectName('contentMetricCard');metric.value_label.setObjectName('contentMetric')
        return metric
    def save_content_selection_mode(self):
        self.repository.save_setting('content_selection_mode',self.content_selection.currentData())
    def build_tasks(self):
        l=self.add_page('tasks','任务列表','每一行就是一条视频任务：直接查看状态，并在该行完成可用操作。')
        summary=QFrame();summary.setObjectName('taskSummary');summary_row=QHBoxLayout(summary);summary_row.setContentsMargins(16,14,16,14);summary_row.setSpacing(12)
        self.task_total_metric=self.task_metric('0','全部视频');self.task_active_metric=self.task_metric('0','正在处理');self.task_attention_metric=self.task_metric('0','需要处理');self.task_done_metric=self.task_metric('0','已结束')
        for metric in (self.task_total_metric,self.task_active_metric,self.task_attention_metric,self.task_done_metric):summary_row.addWidget(metric)
        summary_row.addStretch();l.addWidget(summary)
        panel=QFrame();panel.setObjectName('taskPanel');panel_layout=QVBoxLayout(panel);panel_layout.setContentsMargins(20,16,20,16);panel_layout.setSpacing(12)
        toolbar=QHBoxLayout();toolbar.addWidget(label('全部任务','taskPanelTitle'));toolbar.addStretch();toolbar.addWidget(label('按状态筛选','taskHint'));self.task_filter=QComboBox();self.task_filter.setObjectName('taskFilter');self.task_filter.addItems(['全部任务','需要处理','正在进行','已结束']);self.task_filter.setMinimumWidth(172);self.task_filter.view().setMinimumWidth(190);self.task_filter.setAccessibleName('任务状态筛选');self.task_filter.currentIndexChanged.connect(self.refresh);toolbar.addWidget(self.task_filter);panel_layout.addLayout(toolbar)
        panel_layout.addWidget(label('双击某一行查看标题、文案和标签；右键可播放成片、打开文件夹或处理该任务。','taskHint'))
        self.task_table=QTableWidget(0,7);self.task_table.setHorizontalHeaderLabels(['发布时间','标题','状态','账号','平台','批次','处理时间'])
        self.task_table.verticalHeader().setVisible(False);self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.task_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.task_table.setAlternatingRowColors(True);self.task_table.setShowGrid(False)
        header=self.task_table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        for column in (2,3,4,5,6):header.setSectionResizeMode(column,QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.cellDoubleClicked.connect(self.show_task_detail_row);self.task_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.task_table.customContextMenuRequested.connect(self.show_task_menu);self.add_selection_toolbar(toolbar,self.task_table,'task');panel_layout.addWidget(self.task_table,1)
        self.task_message=label('','error');panel_layout.addWidget(self.task_message);l.addWidget(panel,1)

    def task_metric(self,value,hint):
        metric=QFrame();metric.setObjectName('taskMetricCard');metric.setMinimumWidth(142);metric.setMinimumHeight(82);layout=QVBoxLayout(metric);layout.setContentsMargins(16,11,16,11);layout.setSpacing(1);value_label=label(value,'taskMetric');value_label.setWordWrap(False);layout.addWidget(value_label);hint_label=label(hint,'taskMetricHint');hint_label.setWordWrap(False);layout.addWidget(hint_label);metric.value_label=value_label
        return metric
    def build_settings(self):
        l=self.add_page('settings','账号与设置','这些准备完成一次即可，之后只有账号失效或想换效果时再来。')
        l.setContentsMargins(30,16,30,16);l.setSpacing(12)
        account_panel=QFrame();account_panel.setObjectName('settingsAccount');account_layout=QHBoxLayout(account_panel);account_layout.setContentsMargins(18,14,18,14);account_copy=QVBoxLayout();account_copy.addWidget(label('抖音账号','taskPanelTitle'));self.account_status=label();account_copy.addWidget(self.account_status);account_layout.addLayout(account_copy,1)
        self.login_button=button('登录',lambda:self.queue('LOGIN'),True);self.login_button.setMinimumHeight(40);self.login_button.setEnabled(not self.simulation);account_layout.addWidget(self.login_button)
        self.switch_account_button=button('切换账号',self.switch_account);self.switch_account_button.setMinimumHeight(40);self.switch_account_button.setEnabled(False);account_layout.addWidget(self.switch_account_button)
        self.open_backend_button=button('打开后台',lambda:self.queue('OPEN_BACKEND'));self.open_backend_button.setMinimumHeight(40);self.open_backend_button.setEnabled(not self.simulation);account_layout.addWidget(self.open_backend_button);l.addWidget(account_panel)
        settings_grid=QGridLayout();settings_grid.setHorizontalSpacing(12);settings_grid.setVerticalSpacing(12)
        voice_panel=QFrame();voice_panel.setObjectName('settingsPanel');voice_layout=QVBoxLayout(voice_panel);voice_layout.setContentsMargins(18,14,18,14);voice_layout.addWidget(label('配音','taskPanelTitle'));voice_layout.addWidget(label('选择系统声音和说话速度。','taskHint'));voice_form=QFormLayout();voice_form.setSpacing(12);self.voice=QComboBox();self.voice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.voice.setMinimumContentsLength(16);self.rate=QComboBox();self.rate.addItems(['0.9','1.0','1.1','1.2'])
        for title,w in [('系统中文声音',self.voice),('说话速度',self.rate)]:voice_form.addRow(title,w)
        voice_layout.addLayout(voice_form);settings_grid.addWidget(voice_panel,0,0)
        visual_panel=QFrame();visual_panel.setObjectName('settingsPanel');visual_layout=QVBoxLayout(visual_panel);visual_layout.setContentsMargins(18,14,18,14);visual_layout.addWidget(label('画面','taskPanelTitle'));visual_layout.addWidget(label('设置视频清晰度和镜头使用方式。','taskHint'));visual_form=QFormLayout();visual_form.setSpacing(12);self.resolution=QComboBox();self.resolution.setObjectName('settingsSelect');self.resolution.setMinimumWidth(250);self.resolution.addItem('720p · 标准清晰，处理更快',720);self.resolution.addItem('1080p · 更清晰，处理时间更长',1080);self.avoid_recent=QCheckBox('避开最近20条成片用过的片段');self.avoid_recent.setChecked(True)
        visual_form.addRow('画面清晰度',self.resolution);visual_form.addRow('镜头防重复',self.avoid_recent);visual_layout.addLayout(visual_form);settings_grid.addWidget(visual_panel,0,1)
        subtitles=QFrame();subtitles.setObjectName('settingsPanel');subtitle_layout=QVBoxLayout(subtitles);subtitle_layout.setContentsMargins(18,14,18,14);subtitle_layout.addWidget(label('字幕','taskPanelTitle'));subtitle_layout.addWidget(label('长字幕会自动换行，并保留黑色描边。点击字体框任意位置即可选择。','taskHint'));subtitle_form=QFormLayout();subtitle_form.setSpacing(12)
        self.subtitle_font=QFontComboBox();self.subtitle_font.setWritingSystem(QFontDatabase.WritingSystem.SimplifiedChinese);self.subtitle_font.setEditable(False);self.subtitle_font.setMinimumWidth(250);self.subtitle_font.setToolTip('点击任意位置展开字体列表')
        self.subtitle_size=QSpinBox();self.subtitle_size.setRange(16,72);self.subtitle_size.setValue(32);self.subtitle_size.setSuffix(' 像素（720p）');self.subtitle_max_chars=QSpinBox();self.subtitle_max_chars.setRange(6,30);self.subtitle_max_chars.setValue(14);self.subtitle_max_chars.setSuffix(' 个字')
        self.subtitle_color='#FFFFFF';self.color_button=button('字幕颜色：白色',self.choose_subtitle_color)
        subtitle_form.addRow('字幕字体',self.subtitle_font);subtitle_form.addRow('字幕大小',self.subtitle_size);subtitle_form.addRow('每行字数',self.subtitle_max_chars);subtitle_form.addRow('字幕颜色',self.color_button);subtitle_layout.addLayout(subtitle_form);settings_grid.addWidget(subtitles,1,0)
        bgm_panel=QFrame();bgm_panel.setObjectName('settingsPanel');bgm_layout=QVBoxLayout(bgm_panel);bgm_layout.setContentsMargins(18,14,18,14);bgm_layout.addWidget(label('背景音乐','taskPanelTitle'));bgm_layout.addWidget(label('背景音乐会自动循环铺满视频，并在开始和结束时平滑淡入淡出。','taskHint'));bgm_form=QFormLayout();bgm_form.setSpacing(12);self.bgm_enabled=QCheckBox('为新视频使用背景音乐');self.bgm_mode=QComboBox();self.bgm_mode.setObjectName('settingsSelect');self.bgm_mode.setMinimumWidth(250);self.bgm_mode.addItem('随机选择 · 每条自动抽取','random');self.bgm_mode.addItem('按顺序使用 · 轮流切换','sequence');self.bgm_mode.addItem('指定一首 · 全部使用同一首','specified');self.bgm_choice=QComboBox();self.bgm_choice.setObjectName('bgmChoice');self.bgm_choice.setMinimumWidth(340);self.bgm_choice.addItem('请选择背景音乐','');self.bgm_volume=QSpinBox();self.bgm_volume.setRange(0,50);self.bgm_volume.setValue(15);self.bgm_volume.setSuffix('%');self.bgm_mode_label=label('选择方式');self.bgm_choice_label=label('指定音乐');self.bgm_volume_label=label('音乐音量');bgm_form.addRow('使用背景音乐',self.bgm_enabled);bgm_form.addRow(self.bgm_mode_label,self.bgm_mode);bgm_form.addRow(self.bgm_choice_label,self.bgm_choice);bgm_form.addRow(self.bgm_volume_label,self.bgm_volume);bgm_layout.addLayout(bgm_form);settings_grid.addWidget(bgm_panel,1,1);l.addLayout(settings_grid)
        self.bgm_enabled.toggled.connect(self.set_bgm_enabled);self.bgm_mode.currentIndexChanged.connect(self.set_bgm_mode);self.set_bgm_enabled(False)
        storage_row=QHBoxLayout();self.storage_label=label('数据目录：'+str(self.workspace),'taskHint');storage_row.addWidget(self.storage_label,1);storage_row.addWidget(button('更换数据目录',self.change_data_directory));storage_row.addWidget(button('设置成品目录',self.change_output_directory));l.addLayout(storage_row)
        action_row=QHBoxLayout();self.settings_message=label('','error');action_row.addWidget(self.settings_message,1);self.restore_settings_button=button('恢复默认设置',self.restore_default_settings);self.restore_settings_button.setMinimumHeight(40);self.save_settings_button=button('保存设置',self.save_defaults,True);self.save_settings_button.setMinimumHeight(40);action_row.addWidget(self.restore_settings_button);action_row.addWidget(self.save_settings_button);l.addLayout(action_row);l.addStretch()
    def restore(self):
        self._restoring=True
        try:
            saved=self.repository.setting('daily_form',{});self.count.setValue(saved.get('count',1));self.set_interval_minutes(180);self.days.setValue(1)
            target=datetime.now(SHANGHAI)
            self.set_scheduled_datetime(target)
        finally:
            self._restoring=False
        self.set_immediate(self.immediate_toggle.isChecked())
    def set_immediate(self,immediate):
        self.schedule_stack.setCurrentIndex(0 if immediate else 1)
        if not immediate:
            self.set_scheduled_datetime(datetime.now(SHANGHAI)+timedelta(hours=3))
        if not self._restoring:self.form_changed()
    def set_publish_mode(self,immediate):
        self.immediate_toggle.setChecked(immediate);self.scheduled_toggle.setChecked(not immediate);self.set_immediate(immediate)
    def set_scheduled_datetime(self,value):
        self.schedule_date.setDate(QDate(value.year,value.month,value.day));self.schedule_time.setTime(QTime(value.hour,value.minute))
    def scheduled_datetime(self):
        date=self.schedule_date.date();clock=self.schedule_time.time()
        return datetime(date.year(),date.month(),date.day(),clock.hour(),clock.minute(),tzinfo=SHANGHAI)
    def set_interval_minutes(self,minutes):
        hours,minutes=divmod(min(1439,max(1,int(minutes))),60);self.interval_hours.setValue(hours);self.interval_minutes.setValue(minutes)
    def interval_total_minutes(self):
        return self.interval_hours.value()*60+self.interval_minutes.value()
    def form_changed(self,*_):
        if not hasattr(self,'summary'):return
        self.request_key=uuid.uuid4().hex;self.update_summary()
    def spec(self):
        spec={'count':self.count.value(),'days':self.days.value(),'interval':self.interval_total_minutes(),'immediate':self.immediate_toggle.isChecked(),'mode':'simulate' if self.simulation else 'publish','settings':self.repository.setting('daily_defaults',{}),'content_selection_mode':self.repository.setting('content_selection_mode','sequence')}
        if not spec['immediate']:spec['start']=self.scheduled_datetime().isoformat()
        return spec
    def update_summary(self):
        spec=self.spec();immediate=spec['immediate'];first=(datetime.now(SHANGHAI).timestamp() if immediate else self.scheduled_datetime().timestamp());last=first+(spec['days']-1)*86400+(spec['count']-1)*spec['interval']*60
        account=self.repository.setting('fixed_account',{}).get('display_name','待登录账号')
        first_text='立即提交（制作完成后）' if immediate else stamp(first)+' 开始'
        self.summary.setText('共 %s 条 · %s，每隔 %s 分钟一条\n最后一条：%s · %s'%(spec['count']*spec['days'],first_text,spec['interval'],stamp(last),'本地模拟，不会上传' if self.simulation else '发布至 '+account))
        missing=[]
        if not self.simulation and not (self.repository.setting('fixed_account',{}).get('platform_user_id') and self.repository.setting('fixed_account',{}).get('provider_id')=='douyin_browser'):missing.append('登录抖音')
        if not self.store.materials():missing.append('添加素材')
        shortage=spec['count']*spec['days']-len(self.store.available_contents())
        if shortage>0:missing.append('补充 %s 条未用文案'%shortage)
        if not spec['settings'].get('voice'):missing.append('等待系统配音就绪')
        if spec['interval']<=0:missing.append('设置发布时间间隔')
        self.start_button.setEnabled(not missing)
        if missing:self.issue.setText('准备好这几项就能开始：'+'、'.join(missing))
        elif self.issue.text().startswith('准备好'):self.issue.clear()
    def create_batch(self):
        self.start_button.setEnabled(False)
        try:
            if self.auto_worker:self.start_background()
            spec=self.spec()
            b=BatchPlanner(self.store).create(spec,self.request_key)
            saved={'count':spec['count'],'interval':spec['interval']}
            if not spec['immediate']:saved['time']=datetime.fromisoformat(spec['start']).strftime('%H:%M')
            self.repository.save_setting('daily_form',saved)
            self.started_batch_id=b['id']
            self.issue.setText('已开始：共 %s 条，后台会自动制作并按间隔处理。'%(spec['count']*spec['days']))
            self.refresh();self.navigate('tasks')
        except Exception as exc:self.issue.setText(str(exc));self.update_summary()
    def clear_finished_start_notice(self,batches):
        if not self.started_batch_id or not self.issue.text().startswith('已开始：'):return
        batch=next((row for row in batches if row['id']==self.started_batch_id),None)
        if batch and batch['status']!='ACTIVE':
            self.issue.clear();self.started_batch_id=None
    def queue(self,kind,payload=None):
        self.store.command(kind,payload)
        if self.auto_worker:self.start_background()
        self.refresh()
    def change_output_directory(self):
        selected=QFileDialog.getExistingDirectory(self,'选择成品视频保存目录')
        if not selected:return
        try:
            from .storage import writable
            writable(selected);self.repository.save_setting('output_dir',selected)
            QMessageBox.information(self,'设置已保存','后续成品保存到：'+selected+'\n已有成品保留原位置。')
        except OSError as exc:QMessageBox.warning(self,'目录不可写',str(exc))

    def change_data_directory(self):
        selected=QFileDialog.getExistingDirectory(self,'选择新数据位置（在其中建立 data 文件夹）')
        if not selected:return
        target=Path(selected)/'data'
        if target.resolve()==self.workspace.resolve():return
        try:
            from .storage import writable,save_config
            if target.exists() and any(target.iterdir()):raise ValueError('目标 data 文件夹非空，请选择其他位置')
            if self.workspace.resolve() in target.resolve().parents or target.resolve() in self.workspace.resolve().parents:raise ValueError('新旧目录不能互相包含')
            writable(Path(selected))
            if QMessageBox.question(self,'迁移数据','软件将完全退出。下次打开时复制并校验旧数据到新位置；旧数据保留，不自动删除。是否继续？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            save_config({'data_dir':str(target),'migrate_from':str(self.workspace)})
            self._force_full_exit=True;self.close()
        except (OSError,ValueError) as exc:QMessageBox.warning(self,'无法更换目录',str(exc))

    def download_content_template(self,parent=None):
        downloads=self.workspace/'templates'
        downloads.mkdir(parents=True,exist_ok=True)
        base=downloads/'映小助文案导入模板.xlsx';destination=base;index=2
        while destination.exists():
            destination=downloads/('映小助文案导入模板（%s）.xlsx'%index);index+=1
        try:
            saved=write_content_template(destination)
            self.document_import_status.setText('模板已下载：'+str(saved))
            dialog,open_file,open_folder=self.content_template_dialog(saved,parent);dialog.exec()
            if dialog.choice=='file':QDesktopServices.openUrl(QUrl.fromLocalFile(str(saved)))
            elif dialog.choice=='folder':QDesktopServices.openUrl(QUrl.fromLocalFile(str(saved.parent)))
        except Exception as exc:
            self.document_import_status.setText('模板下载失败：'+str(exc))
            QMessageBox.warning(parent or self,'模板下载失败',str(exc))

    def content_template_dialog(self,saved,parent=None):
        dialog=QDialog(parent or self);dialog.setWindowTitle('文案模板已下载');dialog.setMinimumWidth(560);dialog.choice=None;layout=QVBoxLayout(dialog);layout.setContentsMargins(26,22,26,22);layout.setSpacing(12)
        layout.addWidget(label('模板已下载','section'));layout.addWidget(label('文案导入模板已保存到数据目录的 templates 文件夹。可以直接打开填写，或打开所在文件夹查看。','muted'));path=QLineEdit(str(saved));path.setReadOnly(True);layout.addWidget(path)
        actions=QHBoxLayout();actions.addStretch();open_folder=button('打开所在文件夹',lambda:self.close_template_dialog(dialog,'folder'));open_file=button('打开模板',lambda:self.close_template_dialog(dialog,'file'),True);actions.addWidget(open_folder);actions.addWidget(open_file);actions.addWidget(button('关闭',dialog.reject));layout.addLayout(actions)
        return dialog,open_file,open_folder

    @staticmethod
    def close_template_dialog(dialog,choice):
        dialog.choice=choice;dialog.accept()

    def import_paths(self,key,value=None):
        from urllib.parse import urlparse,unquote
        paths=[]
        for raw in (value if value is not None else self.path_inputs[key].toPlainText()).splitlines():
            raw=raw.strip()
            if len(raw)>1 and raw[0]==raw[-1] and raw[0] in ('"',"'"):raw=raw[1:-1]
            if raw.startswith('file://'):raw=unquote(urlparse(raw).path)
            raw=raw.replace('\\ ',' ')
            if raw:paths.append(str(Path(raw).expanduser()))
        if paths:self.queue({'materials':'IMPORT_MATERIALS','bgm':'IMPORT_BGM','contents':'IMPORT_DOCUMENTS'}[key],{'paths':paths})
    def choose_subtitle_color(self):
        color=QColorDialog.getColor(QColor(self.subtitle_color),self,'选择字幕颜色')
        if color.isValid():
            self.subtitle_color=color.name();self.color_button.setText('字幕颜色：'+color.name())

    @staticmethod
    def import_path_placeholder(key,folder):
        if key=='materials':return '/Users/你的用户名/Documents/工厂素材' if folder else '/Users/你的用户名/Documents/工厂素材/车间镜头.mp4'
        if key=='bgm':return '/Users/你的用户名/Music/工厂背景音乐' if folder else '/Users/你的用户名/Music/工厂背景音乐/轻快节奏.mp3'
        return '/Users/你的用户名/Documents/文案文件夹' if folder else '/Users/你的用户名/Documents/文案.xlsx'

    def path_dialog(self,key,folder=False):
        dialog=QDialog(self);self.import_dialogs.append(dialog)
        dialog.setWindowTitle('添加文案文件夹' if folder and key=='contents' else '添加 BGM 文件夹' if folder and key=='bgm' else '添加素材文件夹' if folder else '批量添加文档' if key=='contents' else '批量添加音乐' if key=='bgm' else '批量添加视频')
        dialog.resize(720,300);layout=QVBoxLayout(dialog)
        layout.addWidget(label('选择整个文件夹后，会自动导入其中的文档（含 XLSX）；文件夹选择窗口只显示文件夹。' if folder and key=='contents' else '粘贴完整路径，每行一个；文件名中的空格无需转义。'))
        if key=='contents':
            template_row=QHBoxLayout()
            template_row.addWidget(label('导入文档需符合模板格式，请先下载模板，按示例填写后再导入。','taskHint'),1)
            template_row.addWidget(button('下载模板',lambda:self.download_content_template(dialog)))
            layout.addLayout(template_row)
        entry=QPlainTextEdit();entry.setPlaceholderText(self.import_path_placeholder(key,folder));layout.addWidget(entry)
        def browse():
            if folder:
                path=QFileDialog.getExistingDirectory(dialog,'选择文件夹')
                values=[path] if path else []
            else:
                types='视频 (*.mp4 *.mov *.mkv *.avi)' if key=='materials' else '音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)' if key=='bgm' else '支持的文档 (*.xlsx *.XLSX *.docx *.DOCX *.txt *.TXT);;Excel 表格 (*.xlsx *.XLSX);;所有文件 (*)'
                values,_=QFileDialog.getOpenFileNames(dialog,'选择文件','',types)
            if values:entry.setPlainText('\n'.join(values))
        row=QHBoxLayout();row.addWidget(button('浏览选择文件夹' if folder else '浏览选择文件',browse));row.addWidget(button('取消',dialog.reject));row.addWidget(button('导入',dialog.accept,True));layout.addLayout(row)
        if dialog.exec()==QDialog.Accepted:
            if key in self.path_inputs:self.path_inputs[key].setPlainText(entry.toPlainText())
            self.import_paths(key,entry.toPlainText())
    def pick_files(self,key):self.path_dialog(key)
    def pick_folder(self,key):self.path_dialog(key,folder=True)
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls():event.acceptProposedAction()
    def dropEvent(self,event):
        files=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        material=[];music=[];docs=[]
        for p in files:
            if Path(p).is_dir():
                (docs if self.pages.currentWidget()==self.targets['contents'] else music if self.pages.currentWidget()==self.targets['bgm'] else material).append(p)
            elif Path(p).suffix.lower() in VIDEO_EXT:material.append(p)
            elif Path(p).suffix.lower() in AUDIO_EXT:music.append(p)
            elif Path(p).suffix.lower() in DOC_EXT:docs.append(p)
        if material:self.queue('IMPORT_MATERIALS',{'paths':material})
        if music:self.queue('IMPORT_BGM',{'paths':music})
        if docs:self.queue('IMPORT_DOCUMENTS',{'paths':docs})
        event.acceptProposedAction()
    @staticmethod
    def selected(widget):return widget.currentItem().data(Qt.UserRole) if widget.currentItem() else None
    def edit_content(self,ident=None):
        content=next((c['request'] for c in self.store.contents() if c['id']==ident),{})
        editor=ContentEditor(content,self);editor.setWindowTitle('文案 · 保存为新版本')
        if editor.exec()==QDialog.Accepted:
            try:
                new,_=Library(self.store).add_content(editor.values())
                if ident and new!=ident:
                    with self.repository.connect() as db:db.execute('UPDATE library_contents SET active=0 WHERE id=?',(ident,))
                self.document_import_status.setText('已保存。已安排任务仍使用原来的文案。');self.refresh()
            except Exception as exc:self.document_import_status.setText(str(exc))
    def toggle_content(self,ident=None):
        if ident is None and self.content_table.currentRow()>=0:
            cell=self.content_table.item(self.content_table.currentRow(),0);ident=cell.data(Qt.UserRole) if cell else None
        if ident:
            with self.repository.connect() as db:db.execute('UPDATE library_contents SET active=1-active WHERE id=?',(ident,))
            self.refresh()
    def clip_summary(self,item):
        if not item.get('result'):return ''
        try:
            report=json.loads(Path(item['result']).with_name('manifest.json').read_text()).get('clip_avoidance')
            if not report:return ''
            return '\n镜头避让：参考最近%s条成片，近期复用%s秒，本条内复用%s秒。%s'%(report['history_videos'],report['recent_overlap_seconds'],report['within_overlap_seconds'],' '.join(report['warnings']))
        except (OSError,ValueError,KeyError,TypeError):return ''

    def render_material_table(self,materials):
        query=self.material_search.text().strip().casefold()
        filtered=[material for material in materials if not query or query in material['name'].casefold()]
        if self.material_sort.currentIndex()==1:filtered.sort(key=lambda material:(-material['duration'],-material['created_at'],material['id']))
        elif self.material_sort.currentIndex()==2:filtered.sort(key=lambda material:(material['duration'],-material['created_at'],material['id']))
        elif self.material_sort.currentIndex() in (3,4):
            collator=QCollator(QLocale('zh_CN'));collator.setNumericMode(True);collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            filtered.sort(key=lambda material:material['id'])
            filtered.sort(key=cmp_to_key(lambda a,b:collator.compare(a['name'],b['name'])),reverse=self.material_sort.currentIndex()==4)
        else:filtered.sort(key=lambda material:(-material['created_at'],material['id']))
        signature=tuple((material['id'],material['name'],material['duration'],material['created_at'],material['path'],Path(material['path']).is_file(),material.get('old_copy_path','')) for material in filtered)
        if getattr(self,'material_view_signature',None)==signature:return
        selected={self.material_table.item(i.row(),0).data(Qt.UserRole) for i in self.material_table.selectionModel().selectedRows()}
        self.material_view_signature=signature;self.material_records={material['id']:material for material in filtered};self.material_table.blockSignals(True);self.material_table.clearSelection();self.material_table.clearContents();self.material_table.setRowCount(len(filtered))
        for row,material in enumerate(filtered):
            values=[material['name'],duration_text(material['duration']),stamp(material['created_at']),'可用' if Path(material['path']).is_file() else '文件失效 · 请重新定位']
            for column,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setData(Qt.UserRole,material['id']);cell.setToolTip(material['path'])
                if column==3:cell.setForeground(QColor('#148060' if Path(material['path']).is_file() else '#c33218'))
                self.material_table.setItem(row,column,cell)
            self.material_table.setRowHeight(row,38)
            if material['id'] in selected:
                for col in range(self.material_table.columnCount()):self.material_table.item(row,col).setSelected(True)
        self.material_table.blockSignals(False);self.material_table.itemSelectionChanged.emit()

    def table_material(self,row):
        if row<0 or row>=self.material_table.rowCount():return None
        cell=self.material_table.item(row,0)
        return self.material_records.get(cell.data(Qt.UserRole)) if cell else None

    def open_material_row(self,row,_column):
        material=self.table_material(row)
        if material:self.open_material(material)

    def open_material(self,material,folder=False):
        path=Path(material['path'])
        if not path.is_file():self.material_import_status.setText('素材文件已找不到：'+str(path));return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if folder else path)))

    def show_material_menu(self,point):
        material=self.table_material(self.material_table.indexAt(point).row())
        if not material:return
        self.material_menu(material).exec(self.material_table.viewport().mapToGlobal(point))

    def material_menu(self,material):
        menu=QMenu(self);menu.addAction('预览素材').triggered.connect(lambda:self.open_material(material));menu.addAction('打开所在文件夹').triggered.connect(lambda:self.open_material(material,folder=True));menu.addAction('重新定位原文件').triggered.connect(lambda:self.relink_material(material));menu.addAction('删除素材').triggered.connect(lambda:self.delete_material(material))
        if material.get('old_copy_path'):menu.addAction('清理已关联的旧副本').triggered.connect(lambda:self.cleanup_material_copy(material['id']))
        return menu

    def relink_material(self,material):
        source,_=QFileDialog.getOpenFileName(self,'选择同一视频的原文件','','视频 (*.mp4 *.mov *.mkv *.avi);;所有文件 (*)')
        if not source:return
        try:
            old=Library(self.store).relink_material(material['id'],source)
            self.material_import_status.setText('已验证并关联原文件：'+source);self.refresh()
            if old:self.cleanup_material_copy(material['id'])
        except Exception as exc:QMessageBox.warning(self,'重新定位未完成',str(exc))

    def cleanup_material_copy(self,ident):
        if QMessageBox.question(self,'清理旧副本','原文件已关联。是否验证原文件后删除软件保存的旧副本，释放空间？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            Library(self.store).cleanup_material_copy(ident);self.material_import_status.setText('旧副本已清理，原文件保留。');self.refresh()
        except Exception as exc:QMessageBox.warning(self,'副本未清理',str(exc))

    def relink_bgm(self,music):
        source,_=QFileDialog.getOpenFileName(self,'选择同一音频的原文件','','音频 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;所有文件 (*)')
        if not source:return
        try:
            old=Library(self.store).relink_bgm(music['id'],source)
            self.bgm_import_status.setText('已验证并关联原文件：'+source);self.refresh()
            if old:self.cleanup_bgm_copy(music['id'])
        except Exception as exc:QMessageBox.warning(self,'重新定位未完成',str(exc))

    def cleanup_bgm_copy(self,ident):
        if QMessageBox.question(self,'清理旧副本','原文件已关联。是否验证原文件后删除软件保存的旧副本，释放空间？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            Library(self.store).cleanup_bgm_copy(ident);self.bgm_import_status.setText('旧副本已清理，原文件保留。');self.refresh()
        except Exception as exc:QMessageBox.warning(self,'副本未清理',str(exc))

    def confirm_library_delete(self,label,name):
        message='确定删除“%s”吗？'%name
        if label in ('素材','背景音乐'):message+='\n\n这会从软件%s库删除，原始导入文件不会受影响。'%label
        dialog=QMessageBox(self);dialog.setWindowTitle('删除'+label);dialog.setText(message)
        remove=dialog.addButton('删除',QMessageBox.ButtonRole.DestructiveRole);dialog.addButton('取消',QMessageBox.ButtonRole.RejectRole)
        dialog.exec();return dialog.clickedButton() is remove

    def delete_material(self,material):
        if not self.confirm_library_delete('素材',material['name']):return
        try:Library(self.store).remove_material(material['id'])
        except Exception as exc:self.material_import_status.setText(str(exc))
        else:self.material_import_status.setText('已删除素材：'+material['name'])
        self.refresh()

    def render_bgm_table(self,bgms):
        query=self.bgm_search.text().strip().casefold();selected_path=self.repository.setting('daily_defaults',{}).get('bgm','')
        records=[music for music in bgms if not query or query in music['name'].casefold()]
        records.sort(key=lambda music:(-music['created_at'],music['id']))
        signature=tuple((music['id'],music['name'],music['duration'],music['created_at'],music['path'],Path(music['path']).is_file(),music.get('old_copy_path','')) for music in records)+(selected_path,)
        if getattr(self,'bgm_view_signature',None)==signature:return
        selected={self.bgm_table.item(i.row(),0).data(Qt.UserRole) for i in self.bgm_table.selectionModel().selectedRows()}
        self.bgm_view_signature=signature;self.bgm_records={music['id']:music for music in records};self.bgm_table.blockSignals(True);self.bgm_table.clearSelection();self.bgm_table.clearContents();self.bgm_table.setRowCount(len(records))
        for row,music in enumerate(records):
            state='文件失效 · 请重新定位' if not Path(music['path']).is_file() else '默认使用' if music['path']==selected_path else '可用';values=[music['name'],duration_text(music['duration']),stamp(music['created_at']),state]
            for column,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setData(Qt.UserRole,music['id']);cell.setToolTip(music['path'])
                if column==3:cell.setForeground(QColor('#c33218' if state.startswith('文件失效') else '#148060' if state=='默认使用' else '#667381'))
                self.bgm_table.setItem(row,column,cell)
            self.bgm_table.setRowHeight(row,38)
            if music['id'] in selected:
                for col in range(self.bgm_table.columnCount()):self.bgm_table.item(row,col).setSelected(True)
        self.bgm_table.blockSignals(False);self.bgm_table.itemSelectionChanged.emit()

    def table_bgm(self,row):
        if row<0 or row>=self.bgm_table.rowCount():return None
        cell=self.bgm_table.item(row,0)
        return self.bgm_records.get(cell.data(Qt.UserRole)) if cell else None

    def open_bgm_row(self,row,_column):
        music=self.table_bgm(row)
        if music:self.open_bgm(music)

    def open_bgm(self,music,folder=False):
        path=Path(music['path'])
        if not path.is_file():self.bgm_import_status.setText('音乐文件已找不到：'+str(path));return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if folder else path)))

    def show_bgm_menu(self,point):
        music=self.table_bgm(self.bgm_table.indexAt(point).row())
        if music:self.bgm_menu(music).exec(self.bgm_table.viewport().mapToGlobal(point))

    def bgm_menu(self,music):
        menu=QMenu(self);menu.addAction('试听音乐').triggered.connect(lambda:self.open_bgm(music));menu.addAction('打开所在文件夹').triggered.connect(lambda:self.open_bgm(music,folder=True));menu.addAction('设为默认背景音乐').triggered.connect(lambda:self.set_default_bgm(music));menu.addAction('停止使用此音乐').triggered.connect(lambda:self.toggle_bgm(music['id']));menu.addAction('重新定位原文件').triggered.connect(lambda:self.relink_bgm(music));menu.addAction('删除背景音乐').triggered.connect(lambda:self.delete_bgm(music))
        if music.get('old_copy_path'):menu.addAction('清理已关联的旧副本').triggered.connect(lambda:self.cleanup_bgm_copy(music['id']))
        return menu

    def delete_bgm(self,music):
        if not self.confirm_library_delete('背景音乐',music['name']):return
        try:Library(self.store).remove_bgm(music['id'])
        except Exception as exc:self.bgm_import_status.setText(str(exc))
        else:self.bgm_import_status.setText('已删除背景音乐：'+music['name'])
        self.refresh()

    def set_default_bgm(self,music):
        defaults=self.repository.setting('daily_defaults',{});defaults.update(bgm_enabled=True,bgm_mode='specified',bgm=music['path']);defaults.setdefault('bgm_volume',.15);self.repository.save_setting('daily_defaults',defaults)
        index=self.bgm_choice.findData(music['path'])
        if index>=0:self.bgm_choice.setCurrentIndex(index)
        self.bgm_enabled.setChecked(True)
        self.bgm_mode.setCurrentIndex(self.bgm_mode.findData('specified'))
        self.bgm_import_status.setText('已设为默认背景音乐：'+music['name']);self.refresh()

    def toggle_bgm(self,ident):
        music=next((item for item in self.store.bgms(include_inactive=True) if item['id']==ident),None)
        if not music:return
        with self.repository.connect() as db:db.execute('UPDATE library_bgm SET active=1-active WHERE id=?',(ident,))
        if music['active'] and self.repository.setting('daily_defaults',{}).get('bgm')==music['path']:
            defaults=self.repository.setting('daily_defaults',{});defaults['bgm']='';self.repository.save_setting('daily_defaults',defaults)
        self.refresh()

    def content_state(self,content,reserved):
        if not content['active']:return '已停用'
        if content['used']:return '已使用'
        return '已安排' if content['id'] in reserved else '未用'

    def render_content_table(self,contents,reserved):
        query=self.content_search.text().strip().casefold();selected_filter=self.content_filter.currentText()
        records=[]
        for content in contents:
            state=self.content_state(content,reserved);request=content['request'];text=' '.join((request.get('title',''),request.get('voice_text',''),request.get('tags',''))).casefold()
            if query and query not in text:continue
            if selected_filter!='全部文案' and state!=selected_filter:continue
            records.append((content,state))
        records.sort(key=lambda record:(record[1]=='已停用',record[1]=='已使用',-record[0]['created_at'],record[0]['id']))
        signature=tuple((content['id'],content['active'],content['used'],content['created_at'],state,content['request']) for content,state in records)
        if getattr(self,'content_view_signature',None)==signature:return
        selected={self.content_table.item(i.row(),0).data(Qt.UserRole) for i in self.content_table.selectionModel().selectedRows()}
        self.content_view_signature=signature;self.content_records={content['id']:content for content,_ in records};self.content_table.blockSignals(True);self.content_table.clearSelection();self.content_table.clearContents();self.content_table.setRowCount(len(records))
        colors={'未用':'#148060','已安排':'#b46a12','已使用':'#667381','已停用':'#a43714'}
        for row,(content,state) in enumerate(records):
            request=content['request'];voice=' '.join(request.get('voice_text','').split())
            values=[request.get('title','未命名文案'),voice[:62]+('…' if len(voice)>62 else ''),request.get('tags','—') or '—',state,stamp(content['created_at'])]
            tip='标题：%s\n\n文案：\n%s\n\n标签：%s'%(request.get('title',''),request.get('voice_text',''),request.get('tags','—') or '—')
            for column,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setData(Qt.UserRole,content['id']);cell.setToolTip(tip)
                if column==3:cell.setForeground(QColor(colors[state]))
                self.content_table.setItem(row,column,cell)
            self.content_table.setRowHeight(row,38)
            if content['id'] in selected:
                for col in range(self.content_table.columnCount()):self.content_table.item(row,col).setSelected(True)
        self.content_table.blockSignals(False);self.content_table.itemSelectionChanged.emit()

    def table_content(self,row):
        if row<0 or row>=self.content_table.rowCount():return None
        cell=self.content_table.item(row,0)
        return self.content_records.get(cell.data(Qt.UserRole)) if cell else None

    def edit_content_row(self,row,_column):
        content=self.table_content(row)
        if content:self.edit_content(content['id'])

    def show_content_menu(self,point):
        content=self.table_content(self.content_table.indexAt(point).row())
        if not content:return
        self.content_menu(content).exec(self.content_table.viewport().mapToGlobal(point))

    def content_menu(self,content):
        menu=QMenu(self);menu.addAction('查看 / 修改文案').triggered.connect(lambda:self.edit_content(content['id']));menu.addAction('恢复文案' if not content['active'] else '停用文案').triggered.connect(lambda:self.toggle_content(content['id']));menu.addAction('删除文案').triggered.connect(lambda:self.delete_content(content))
        return menu

    def delete_content(self,content):
        if not self.confirm_library_delete('文案',content['request'].get('title','未命名文案')):return
        try:Library(self.store).remove_content(content['id'])
        except Exception as exc:self.document_import_status.setText(str(exc))
        else:self.document_import_status.setText('已删除文案：'+content['request'].get('title','未命名文案'))
        self.refresh()

    def render_task_table(self,records):
        signature=tuple((batch['id'],batch['status'],item['id'],item['due_at'],item['state'],item['result'],item['submitted_at'],item['error']) for batch,item in records)
        if getattr(self,'item_view_signature',None)==signature:return
        selected={self.task_table.item(i.row(),0).data(Qt.UserRole) for i in self.task_table.selectionModel().selectedRows()}
        self.item_view_signature=signature;self.task_table.blockSignals(True);self.task_table.clearSelection();self.task_table.clearContents();self.task_table.setRowCount(len(records))
        state_colors={'FAILED':'#c33218','UNKNOWN':'#c33218','ACCEPTED':'#148060','PUBLISHED':'#148060','SIMULATED':'#148060','GENERATED':'#148060','READY':'#b46a12','GENERATING':'#2867a8','PREPARING':'#2867a8','SUBMITTING':'#2867a8'}
        for row,(batch,task) in enumerate(records):
            values=[stamp(task['due_at']),task['request']['content']['title'],NAMES.get(task['state'],task['state']),batch['account'].get('display_name','本地模拟'),('本地模拟' if batch['mode']=='simulate' else '抖音'),NAMES.get(batch['status'],batch['status']),stamp(task['submitted_at']) if task['submitted_at'] else '—']
            for column,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setData(Qt.UserRole,task['id']);cell.setToolTip(self.task_detail_text(batch,task))
                if column==2:cell.setForeground(QColor(state_colors.get(task['state'],'#667381')))
                self.task_table.setItem(row,column,cell)
            self.task_table.setRowHeight(row,48)
            if task['id'] in selected:
                for col in range(self.task_table.columnCount()):self.task_table.item(row,col).setSelected(True)
        self.task_table.blockSignals(False);self.task_table.itemSelectionChanged.emit()

    def table_task(self,row):
        if row<0 or row>=self.task_table.rowCount():return None
        cell=self.task_table.item(row,0)
        return self.store.item(cell.data(Qt.UserRole)) if cell else None

    def play_task_row(self,row,_column):
        task=self.table_task(row)
        if task:self.open_item_video(task['id'])

    def show_task_detail_row(self,row,_column):
        task=self.table_task(row)
        if task:self.show_task_detail(self.store.batch(task['batch_id']),task)

    def show_task_menu(self,point):
        row=self.task_table.indexAt(point).row();task=self.table_task(row)
        if not task:return
        menu=self.task_menu(self.store.batch(task['batch_id']),task)
        menu.exec(self.task_table.viewport().mapToGlobal(point))

    def task_menu(self,batch,task):
        """Build the row menu separately so its available actions stay easy to verify."""
        has_video=bool(task.get('result') and Path(task['result']).is_file());menu=QMenu(self)
        play=menu.addAction('播放成片');play.setEnabled(has_video);play.triggered.connect(lambda:self.open_item_video(task['id']))
        folder=menu.addAction('打开所在文件夹');folder.setEnabled(has_video);folder.triggered.connect(lambda:self.open_item_video(task['id'],folder=True))
        menu.addAction('查看详细信息').triggered.connect(lambda:self.show_task_detail(batch,task))
        if task['state']=='UNKNOWN':menu.addAction('核对提交结果').triggered.connect(lambda:self.reconcile_item(task['id']))
        if batch['status']=='ACTIVE':menu.addAction('暂停本批任务').triggered.connect(lambda:self.batch_action('pause',batch['id']))
        elif batch['status'] in ('PAUSED','NEEDS_ATTENTION'):menu.addAction('恢复本批任务').triggered.connect(lambda:self.batch_action('resume',batch['id']))
        if batch['status'] not in ('CANCELLED','COMPLETED'):menu.addAction('取消本批未开始任务').triggered.connect(lambda:self.batch_action('cancel',batch['id']))
        return menu

    def task_detail_text(self,batch,task):
        content=task['request']['content'];copy=content.get('voice_text','').strip() or '—';body=content.get('body','').strip();tags=content.get('tags','').strip() or '—'
        timing='计划发布时间：'+stamp(task['due_at'])
        if task['submitted_at']:timing+='\n实际处理时间：'+stamp(task['submitted_at'])
        detail=task['error'] or self.clip_summary(task).strip() or '暂无异常信息。'
        release_body=('\n\n发布正文：\n'+body) if body else ''
        return '标题：%s\n\n文案：\n%s%s\n\n标签：\n%s\n\n状态：%s\n所属批次：%s · %s\n账号：%s\n平台：%s\n%s\n\n处理信息：\n%s'%(content['title'],copy,release_body,tags,NAMES.get(task['state'],task['state']),stamp(batch['created_at']),NAMES.get(batch['status'],batch['status']),batch['account'].get('display_name','本地模拟'),'本地模拟' if batch['mode']=='simulate' else '抖音',timing,detail)

    def show_task_detail(self,batch,task):
        dialog=QDialog(self);dialog.setWindowTitle('任务详细信息');dialog.resize(580,380);layout=QVBoxLayout(dialog);text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(self.task_detail_text(batch,task));layout.addWidget(text);layout.addWidget(button('关闭',dialog.accept));dialog.exec()
    def batch_action(self,action,ident=None):
        if not ident:return
        try:
            {'pause':self.store.pause_batch,'resume':self.store.resume_batch,'cancel':self.store.cancel_batch}[action](ident);self.task_message.clear();self.refresh()
        except Exception as exc:self.task_message.setText(str(exc))
    def open_item_video(self,ident,folder=False):
        p=self.store.item(ident).get('result')
        if not p or not Path(p).is_file():self.task_message.setText('这条视频尚未制作完成');return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(p).parent if folder else p)))
    def reconcile_item(self,ident):
        if self.store.item(ident)['state']!='UNKNOWN':self.task_message.setText('只有提交结果待核对的作品需要此操作');return
        value,ok=QInputDialog.getText(self,'核对抖音作品','输入这条作品在抖音的作品编号，后台会核对账号与标题：')
        if ok and value.strip():self.queue('RECONCILE',{'item_id':ident,'video_id':value.strip()})
    def save_defaults(self):
        saved=self.repository.setting('daily_defaults',{})
        if not self.voice.currentText():self.show_settings_message('系统声音尚未就绪');return
        saved.update(avoid_recent_clips=self.avoid_recent.isChecked(),font=self.subtitle_font.currentFont().family(),font_size=self.subtitle_size.value(),subtitle_max_chars=self.subtitle_max_chars.value(),font_color=self.subtitle_color,voice=self.voice.currentData(),rate=float(self.rate.currentText()),width=int(self.resolution.currentData() or 720),bgm_enabled=self.bgm_enabled.isChecked(),bgm_mode=self.bgm_mode.currentData(),bgm=self.bgm_choice.currentData() if self.bgm_enabled.isChecked() else '',bgm_volume=self.bgm_volume.value()/100)
        self.repository.save_setting('daily_defaults',saved);self.show_settings_message('设置已保存',success=True);self.refresh()
    def restore_default_settings(self):
        if self.voice.count():
            self.voice.setCurrentText('Tingting' if self.voice.findText('Tingting') >= 0 else self.voice.itemText(0))
        self.rate.setCurrentText('1.0');self.resolution.setCurrentIndex(max(0,self.resolution.findData(720)));self.bgm_enabled.setChecked(False);self.bgm_mode.setCurrentIndex(self.bgm_mode.findData('specified'));self.bgm_choice.setCurrentIndex(0);self.bgm_volume.setValue(15);self.subtitle_font.setCurrentFont(QFont('PingFang SC'));self.subtitle_size.setValue(32);self.subtitle_max_chars.setValue(14);self.subtitle_color='#FFFFFF';self.color_button.setText('字幕颜色：#FFFFFF');self.avoid_recent.setChecked(True);self.settings_message.clear()
    def set_bgm_enabled(self,enabled):
        if enabled:self.bgm_mode.setCurrentIndex(self.bgm_mode.findData('random'))
        self.bgm_mode.setEnabled(enabled);self.bgm_volume.setEnabled(enabled);self.bgm_mode_label.setEnabled(enabled);self.bgm_volume_label.setEnabled(enabled);self.set_bgm_mode()
    def set_bgm_mode(self,*_):
        enabled=self.bgm_enabled.isChecked() and self.bgm_mode.currentData()=='specified';self.bgm_choice.setEnabled(enabled);self.bgm_choice_label.setEnabled(enabled)
    def show_settings_message(self,text='',success=False):
        self.settings_message.setText(text);self.settings_message.setStyleSheet('color:#168467;padding:6px 0;' if success else '')
        if success:QTimer.singleShot(3000,lambda:self.clear_settings_success(text))
    def clear_settings_success(self,text):
        if self.settings_message.text()==text:
            self.settings_message.clear();self.settings_message.setStyleSheet('')
    def start_background(self):
        try:ensure_worker(self.workspace,self.simulation)
        except Exception as exc:self.show_settings_message(str(exc))
    def stop_background(self):self.store.command('STOP');self.show_settings_message('正在停止后台，未执行的任务保留。')
    def switch_account(self):
        if self.store.account_switch_blocker():
            self.show_settings_message('当前账号仍有未结束的发布任务，请先完成或取消后再切换账号')
            return
        self.queue('SWITCH_ACCOUNT')
    def refresh(self):
        materials=self.store.materials();bgms=self.store.bgms();contents=self.store.contents();available=len(self.store.available_contents());items=self.store.items();account=self.repository.setting('fixed_account',{})
        checking=self.repository.setting('douyin_session',{}).get('state')=='CHECKING'
        remembered=bool(account.get('platform_user_id') and account.get('provider_id')=='douyin_browser')
        self.account_button.setText('查看账号' if (account.get('verified') or remembered or self.simulation) else ('正在检查登录…' if checking else '登录抖音'))
        self.account_button.setEnabled(not checking)
        account_text='本地模拟，无需登录' if self.simulation else account.get('display_name','尚未登录')+(' · 已连接' if account.get('verified') else (' · 正在检查登录' if checking else ' · 已保存，发布前自动验证' if remembered else ' · 需要登录'))
        self.readiness['account'].setText(account_text)
        self.account_status.setText('本地模拟' if self.simulation else ('当前账号：'+account['display_name'] if account.get('display_name') else '未登录'))
        self.readiness['materials'].setText('%s 段可用素材'%len(materials));self.readiness['contents'].setText('%s 条未用 · %s 条已使用'%(available,sum(c['used'] for c in contents)))
        self.material_total_metric.value_label.setText(str(sum(Path(m['path']).is_file() for m in materials)))
        self.material_duration_metric.value_label.setText(duration_text(sum(material['duration'] for material in materials)))
        self.material_latest_metric.value_label.setText(stamp(max((material['created_at'] for material in materials),default=0)) if materials else '—')
        self.render_material_table(materials)
        defaults=self.repository.setting('daily_defaults',{})
        self.bgm_total_metric.value_label.setText(str(len(bgms)))
        self.bgm_duration_metric.value_label.setText(duration_text(sum(music['duration'] for music in bgms)))
        selected_bgm=next((music for music in bgms if music['path']==defaults.get('bgm')),None)
        self.bgm_default_metric.value_label.setText(selected_bgm['name'][:18] if selected_bgm else '未选择')
        self.render_bgm_table(bgms)
        reserved={i['content_id'] for i in items if i['reservation']}
        self.content_total_metric.value_label.setText(str(len(contents)))
        self.content_available_metric.value_label.setText(str(sum(self.content_state(content,reserved)=='未用' for content in contents)))
        self.content_reserved_metric.value_label.setText(str(sum(self.content_state(content,reserved)=='已安排' for content in contents)))
        self.content_used_metric.value_label.setText(str(sum(self.content_state(content,reserved)=='已使用' for content in contents)))
        self.render_content_table(contents,reserved)
        batches=self.store.batches();self.clear_finished_start_notice(batches);batches_by_id={batch['id']:batch for batch in batches}
        deleted_tasks=self.store.deleted_task_ids()
        records=[(batches_by_id[item['batch_id']],item) for item in items if item['batch_id'] in batches_by_id and item['id'] not in deleted_tasks]
        active_states=('QUEUED','GENERATING','READY','PREPARING','SUBMITTING')
        done_states=('ACCEPTED','PUBLISHED','SIMULATED','GENERATED','CANCELLED')
        self.task_total_metric.value_label.setText(str(len(records)))
        self.task_active_metric.value_label.setText(str(sum(item['state'] in active_states for _,item in records)))
        self.task_attention_metric.value_label.setText(str(sum(item['state'] in ('FAILED','UNKNOWN') for _,item in records)))
        self.task_done_metric.value_label.setText(str(sum(item['state'] in done_states for _,item in records)))
        task_filter=self.task_filter.currentIndex()
        filtered=[record for record in records if task_filter==0 or (task_filter==1 and (record[1]['state'] in ('FAILED','UNKNOWN') or record[0]['status']=='NEEDS_ATTENTION')) or (task_filter==2 and record[1]['state'] in active_states) or (task_filter==3 and record[1]['state'] in done_states)]
        rank=lambda record:0 if record[1]['state'] in ('FAILED','UNKNOWN') else 1 if record[1]['state'] in active_states else 2 if record[0]['status']=='NEEDS_ATTENTION' else 3 if record[1]['state'] in done_states else 4
        filtered.sort(key=lambda record:(record[1]['due_at'],record[0]['created_at'],record[1]['ordinal']),reverse=True)
        self.render_task_table(filtered)
        self.overview.setText('任务 %s 批 · 已制作 %s 条 · %s %s 条 · 需要处理 %s 条'%(len(batches),sum(bool(i['result']) for i in items),'模拟完成' if self.simulation else '平台已接收',sum(i['state']==('SIMULATED' if self.simulation else 'ACCEPTED') for i in items),sum(i['state'] in ('FAILED','UNKNOWN') for i in items)))
        voices=self.repository.setting('daily_voices',[])
        if not self.defaults_loaded and defaults and voices:
            self.voice.clear();[self.voice.addItem(voice_label(v),v) for v in voices];self.voice.setCurrentIndex(max(0,self.voice.findData(defaults['voice'])));self.rate.setCurrentText(str(defaults.get('rate',1.0)));self.resolution.setCurrentIndex(max(0,self.resolution.findData(defaults.get('width',720))));self.bgm_enabled.setChecked(bool(defaults.get('bgm_enabled',defaults.get('bgm'))));self.bgm_mode.setCurrentIndex(max(0,self.bgm_mode.findData(defaults.get('bgm_mode','specified'))));self.bgm_volume.setValue(round(defaults.get('bgm_volume',.15)*100));self.subtitle_font.setCurrentFont(QFont(defaults.get('font','PingFang SC')));self.subtitle_size.setValue(defaults.get('font_size',32));self.subtitle_max_chars.setValue(defaults.get('subtitle_max_chars',14));self.subtitle_color=defaults.get('font_color','#FFFFFF');self.color_button.setText('字幕颜色：'+self.subtitle_color);self.avoid_recent.setChecked(defaults.get('avoid_recent_clips',True));self.defaults_loaded=True
        self.refresh_bgm_choices(bgms,defaults)
        commands=self.store.commands()
        for kind,status in [('IMPORT_MATERIALS',self.material_import_status),('IMPORT_BGM',self.bgm_import_status),('IMPORT_DOCUMENTS',self.document_import_status)]:
            command=next((c for c in commands if c['kind']==kind),None)
            if command:
                data=command['result_data'];status.setText(command['error'] if command['status']=='FAILED' else '正在处理，请稍候…' if command['status'] in ('QUEUED','RUNNING') else '已添加 %s · 重复跳过 %s%s'%(data.get('added',0),data.get('duplicate',0),'\n'+'\n'.join(data.get('errors',[])) if data.get('errors') else ''))
        login=next((c for c in commands if c['kind'] in ('LOGIN','SWITCH_ACCOUNT','OPEN_BACKEND','RECONCILE')),None)
        busy=bool(login and login['status'] in ('QUEUED','RUNNING'))
        switching_blocked=bool(self.store.account_switch_blocker())
        self.login_button.setEnabled(not self.simulation and not busy)
        self.open_backend_button.setEnabled(not self.simulation and not busy)
        self.switch_account_button.setEnabled(not self.simulation and remembered and not busy and not switching_blocked)
        if login and login['status']=='FAILED':self.show_settings_message(login['error'])
        self.update_summary()

    def refresh_bgm_choices(self,bgms,defaults):
        signature=tuple((music['id'],music['name'],music['path']) for music in bgms)+(defaults.get('bgm',''),)
        if getattr(self,'bgm_choice_signature',None)==signature:return
        selected=defaults.get('bgm','') if not getattr(self,'bgm_choices_loaded',False) else self.bgm_choice.currentData()
        self.bgm_choice.blockSignals(True);self.bgm_choice.clear();self.bgm_choice.addItem('请选择背景音乐','')
        for music in bgms:self.bgm_choice.addItem(music['name'],music['path'])
        self.bgm_choice.setCurrentIndex(max(0,self.bgm_choice.findData(selected)))
        self.bgm_choice.blockSignals(False);self.bgm_choices_loaded=True;self.bgm_choice_signature=signature
    def worker_is_locked(self):
        lock=QLockFile(str(self.workspace/'app.lock'));lock.setStaleLockTime(0)
        if lock.tryLock(0):
            lock.unlock();return False
        return True

    def has_pending_work(self):
        return any(i['state'] in ('QUEUED','GENERATING','READY','PREPARING','SUBMITTING','FAILED','UNKNOWN') for i in self.store.items()) or any(c['status'] in ('QUEUED','RUNNING') and c['kind']!='STOP' for c in self.store.commands())

    def finish_shutdown(self):
        # The heartbeat turns false as soon as STOP is requested. The lock is
        # released only after the worker thread and browser cleanup finish.
        if self.worker_is_locked():return
        self.shutdown_timer.stop();self._exit_ready=True
        if getattr(self,'shutdown_dialog',None):self.shutdown_dialog.close()
        QTimer.singleShot(500,self.close)

    def closeEvent(self,event):
        if getattr(self,'_exit_ready',False) or not self.auto_worker or not self.worker_is_locked():
            self.timer.stop();self.instance_lock.unlock();event.accept();return
        event.ignore()
        if getattr(self,'_shutting_down',False):return
        if self.has_pending_work() and not getattr(self,'_force_full_exit',False):
            prompt=QMessageBox(self);prompt.setWindowTitle('关闭客户端')
            prompt.setText('还有未完成的任务，关闭后如何处理？')
            prompt.setInformativeText('后台继续运行：继续制作和本机定时任务。\n完全退出：停止本机处理，下次打开后继续；已提交到抖音后台的作品不受影响。')
            background=prompt.addButton('后台继续运行',QMessageBox.ButtonRole.ActionRole)
            quit_button=prompt.addButton('完全退出',QMessageBox.ButtonRole.DestructiveRole)
            prompt.addButton('取消',QMessageBox.ButtonRole.RejectRole);prompt.exec()
            if prompt.clickedButton()==background:
                self._exit_ready=True;self.close();return
            if prompt.clickedButton()!=quit_button:return
        self._shutting_down=True;self.timer.stop();self.setEnabled(False)
        self.store.command('STOP')
        self.shutdown_dialog=QMessageBox(self);self.shutdown_dialog.setWindowTitle('正在退出')
        self.shutdown_dialog.setText('正在停止后台并关闭发布会话，请稍候…')
        self.shutdown_dialog.setStandardButtons(QMessageBox.StandardButton.NoButton);self.shutdown_dialog.show()
        self.shutdown_timer=QTimer(self);self.shutdown_timer.timeout.connect(self.finish_shutdown);self.shutdown_timer.start(300)
