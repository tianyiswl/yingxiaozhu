"""Shared native controls and visual tokens for the warm studio interface."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QPushButton,QScrollArea,QFrame,QStyle

STYLE='''
QWidget { background:#fffdf9; color:#192633; font-family:"PingFang SC","Microsoft YaHei",sans-serif; font-size:16px; }
QLabel { background:transparent; }
QLabel#heading { font-size:30px; font-weight:700; }
QLabel#section { font-size:20px; font-weight:600; }
QLabel#muted { color:#667381; }
QLabel#error { color:#a43714; padding:6px 0; }
QFrame#sidebar { background:#faf6ef; border-right:1px solid #eee7df; }
QFrame#feedbackCard { background:#173654; border:1px solid #244765; border-radius:10px; }
QPushButton { border:1px solid #dcdedc; border-radius:8px; padding:10px 18px; background:#fffdf9; min-height:22px; }
QPushButton:hover { background:#f8efe4; border-color:#cfaa8e; }
QPushButton:focus { border:2px solid #b64412; }
QPushButton:disabled { color:#87919a; background:#eeece8; border-color:#eeece8; }
QPushButton[primary="true"] { background:#dc5014; color:white; border:1px solid #dc5014; font-weight:600; }
QPushButton[primary="true"]:hover { background:#c7440d; }
QPushButton[primary="true"]:disabled { background:#e1d4c7; color:#605d58; border-color:#e1d4c7; }
QPushButton[nav="true"] { text-align:left; border:0; background:transparent; padding:13px 18px; }
QPushButton[nav="true"]:checked { background:#fde9d8; color:#b74212; font-weight:600; }
QPushButton[link="true"] { border:0; color:#ba4310; background:transparent; text-align:left; padding:5px 0; }
QLineEdit,QPlainTextEdit,QComboBox,QSpinBox { background:#ffffff; border:1px solid #dcdedc; border-radius:6px; padding:9px; selection-background-color:#f1c5a8; selection-color:#192633; }
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus { border:2px solid #d65a22; }
QCheckBox,QRadioButton { spacing:8px; color:#243646; font-weight:500; }
QCheckBox::indicator { width:18px; height:18px; border:2px solid #7b8790; border-radius:4px; background:#ffffff; }
QCheckBox::indicator:hover { border-color:#b64412; background:#fff6ee; }
QCheckBox::indicator:checked { border-color:#dc5014; background:#dc5014; }
QCheckBox::indicator:disabled { border-color:#c9cfce; background:#f0f0ee; }
QCheckBox::indicator:checked:disabled { border-color:#dfb49d; background:#dfb49d; }
QRadioButton::indicator { width:18px; height:18px; border:2px solid #7b8790; border-radius:10px; background:#ffffff; }
QRadioButton::indicator:hover { border-color:#b64412; background:#fff6ee; }
QRadioButton::indicator:checked { border:5px solid #dc5014; background:#ffffff; }
QRadioButton::indicator:disabled { border-color:#c9cfce; background:#f0f0ee; }
QRadioButton::indicator:checked:disabled { border-color:#dfb49d; background:#f0f0ee; }
QPushButton[mode="true"] { min-width:104px; min-height:22px; background:#ffffff; border:1px solid #c7ced2; border-radius:7px; padding:10px 14px; color:#45535e; font-weight:600; }
QPushButton[mode="true"]:hover { background:#fff6ee; border-color:#c86a3a; }
QPushButton[mode="true"]:checked { background:#fff0e4; border:2px solid #dc5014; color:#af3f10; }
QListWidget { background:#fffdf9; border:0; outline:0; }
QListWidget::item { padding:12px; border-bottom:1px solid #eee9e2; }
QListWidget::item:selected { color:#192633; background:#fcebdc; }
QTableWidget { background:#ffffff; border:1px solid #e7e4df; border-radius:8px; gridline-color:#eee9e2; selection-background-color:#fcebdc; selection-color:#192633; }
QTableWidget::item { padding:9px 10px; border-bottom:1px solid #f0ece6; }
QHeaderView::section { background:#f7f4ef; color:#667381; border:0; border-bottom:1px solid #e6e1da; padding:10px; font-weight:600; }
QFrame#taskSummary { background:#f4f0e9; border:1px solid #e9e1d6; border-radius:12px; }
QFrame#taskMetricCard { background:#fffdf9; border:1px solid #e9e1d6; border-radius:9px; }
QFrame#taskPanel { background:#fffdf9; border:1px solid #eee4d9; border-radius:12px; }
QFrame#librarySummary { background:#edf4f1; border:1px solid #d8e5df; border-radius:12px; }
QFrame#libraryMetricCard { background:#fffdf9; border:1px solid #d8e5df; border-radius:9px; }
QFrame#materialPanel { background:#fffdf9; border:1px solid #e3e9e5; border-radius:12px; }
QFrame#materialDrop { background:#f7faf8; border:1px dashed #b7cbc1; border-radius:8px; }
QFrame#contentSummary { background:#f5f0eb; border:1px solid #e7dcd1; border-radius:12px; }
QFrame#contentMetricCard { background:#fffdf9; border:1px solid #e7dcd1; border-radius:9px; }
QFrame#contentPanel { background:#fffdf9; border:1px solid #e9e1da; border-radius:12px; }
QFrame#settingsAccount { background:#edf4f1; border:1px solid #d6e4dc; border-radius:12px; }
QFrame#settingsPanel { background:#fffdf9; border:1px solid #e5e2dc; border-radius:12px; }
QFrame#settingsRuntime { background:#f7f4ef; border:1px solid #e7e1d9; border-radius:12px; }
QFrame#taskStatus { background:#fcebdc; border:1px solid #f0c9ae; border-radius:10px; }
QLabel#taskPanelTitle { color:#192633; font-size:19px; font-weight:700; }
QLabel#taskHint { color:#667381; font-size:14px; }
QLabel#timeUnit { color:#667381; font-size:14px; }
QLabel#schedulePlaceholder { color:#8a959e; background:#f4f1eb; border:1px solid #ded8cf; border-radius:6px; font-size:14px; }
QLabel#feedbackTitle { color:#ffffff; font-size:17px; font-weight:700; }
QLabel#feedbackBody { color:#c8d7e5; font-size:13px; }
QLabel#feedbackContact { color:#ffffff; font-size:14px; font-weight:600; }
QLabel#feedbackPrompt { color:#f4d3bb; font-size:14px; font-weight:700; }
QLabel#taskStatusTitle { color:#192633; font-size:19px; font-weight:700; }
QLabel#taskMetric { color:#192633; font-size:20px; font-weight:700; }
QLabel#taskMetricHint { color:#667381; font-size:13px; }
QLabel#libraryMetric { color:#155a4b; font-size:18px; font-weight:700; }
QLabel#libraryMetricHint { color:#5d7770; font-size:12px; }
QLabel#contentMetric { color:#9d4b20; font-size:18px; font-weight:700; }
QLabel#editorFieldLabel { color:#243646; font-size:15px; font-weight:600; margin-top:3px; }
QPlainTextEdit#editorVoiceText,QPlainTextEdit#editorBody { line-height:1.45; }
QComboBox#taskFilter { min-width:172px; padding-right:30px; }
QComboBox#taskFilter::drop-down { width:30px; border-left:1px solid #e4ddd4; }
QComboBox#taskFilter QAbstractItemView { min-width:190px; background:#fffdf9; color:#192633; border:1px solid #dcd6ce; selection-background-color:#fce0ca; selection-color:#192633; padding:5px; }
QLineEdit#materialSearch { min-width:260px; }
QComboBox#materialSort { min-width:154px; padding-right:28px; }
QTableWidget#materialTable { border-color:#dfe8e3; }
QTableWidget#materialTable::item { padding:5px 10px; }
QTableWidget#materialTable QHeaderView::section { padding:7px 10px; }
QLineEdit#contentSearch { min-width:260px; }
QComboBox#contentFilter { min-width:140px; padding-right:28px; }
QTableWidget#contentTable { border-color:#e8dfd6; }
QTableWidget#contentTable::item { padding:5px 10px; }
QTableWidget#contentTable QHeaderView::section { padding:7px 10px; }
QScrollArea { border:0; }
QProgressBar { border:0; background:#eee7de; height:8px; border-radius:4px; }
QProgressBar::chunk { background:#de581b; border-radius:4px; }
QSplitter::handle { background:#eee7df; width:1px; }
QSlider::groove:horizontal { height:5px; background:#e2dcd4; }
QSlider::handle:horizontal { background:#da5014; width:14px; margin:-5px 0; border-radius:7px; }
'''

def label(text='',kind=None):
    w=QLabel(text);w.setWordWrap(True)
    if kind:w.setObjectName(kind)
    return w

def button(text,slot=None,primary=False,icon=None):
    w=QPushButton(text);w.setMinimumHeight(44)
    if primary:w.setProperty('primary',True)
    if icon is not None:w.setIcon(w.style().standardIcon(icon))
    if slot:w.clicked.connect(slot)
    return w

def page(title,subtitle=''):
    w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(30,24,30,24);l.setSpacing(18)
    if title:l.addWidget(label(title,'heading'))
    if subtitle:l.addWidget(label(subtitle,'muted'))
    return w,l

def scroll(widget):
    s=QScrollArea();s.setWidgetResizable(True);s.setWidget(widget);return s

def divider():
    d=QFrame();d.setFrameShape(QFrame.Shape.HLine);d.setStyleSheet('color:#eee7df;');return d


class PortraitPreview(QWidget):
    """Fit the vertical video without filling unused space with black bars."""
    def __init__(self,video):
        super().__init__();self.video=video;video.setParent(self);video.setMinimumSize(90,160)
    def resizeEvent(self,event):
        height=min(self.height(),int(self.width()*16/9));width=int(height*9/16)
        self.video.setGeometry((self.width()-width)//2,(self.height()-height)//2,width,height)
        super().resizeEvent(event)
