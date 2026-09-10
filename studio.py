"""A local, offline video workbench. Qt is only the UI; core/exporter own rendering."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import threading
import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, QObject, QTimer, QUrl, QPointF, QRectF, QSettings
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QShortcut, QKeySequence, QDesktopServices, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton, QStackedWidget,
    QHBoxLayout, QVBoxLayout, QFormLayout, QSplitter, QListWidget, QListWidgetItem,
    QComboBox, QCheckBox, QSlider, QSpinBox, QDoubleSpinBox, QTabWidget,
    QFileDialog, QMessageBox, QProgressBar, QScrollArea, QFrame, QSizePolicy)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
import core
import exporter
import analysis_engine
import analysis_cache
import acceleration
import color_management as colors
from playback import LatestRenderer
from project_info import VERSION, AUTHOR, ProjectDialog

VIDEO_FILTER='视频 (*.mp4 *.mov *.mkv *.avi *.m4v *.mts *.m2ts *.webm);;所有文件 (*)'
STYLE='''
QWidget {background:#15171d;color:#d8dce6;font-family:"Microsoft YaHei UI";font-size:12px;}
QMainWindow {background:#101217;}
QLabel {background:transparent;}
QLabel#muted {color:#858b9e;font-size:11px;}
QLabel#heading {font-size:15px;font-weight:600;color:#f0f2f7;}
QLabel#brand {color:#e5f8f5;font-size:17px;font-weight:700;}
QFrame#panel {background:#1a1d25;border:1px solid #2b303b;border-radius:8px;}
QWidget#inspectorPage {background:#1a1d25;}
QPushButton {background:#262b36;border:1px solid #353b49;border-radius:5px;padding:7px 12px;}
QPushButton:hover {background:#343c4b;border-color:#617086;}
QPushButton:pressed, QPushButton:checked {background:#294942;border-color:#61d8bf;color:#96f2df;}
QPushButton:disabled {color:#596170;background:#1d2129;border-color:#292e38;}
QPushButton#primary {background:#64d9c1;color:#10221d;font-weight:700;border:0;padding:8px 18px;}
QPushButton#primary:hover {background:#8debd6;}
QPushButton#primary:disabled {background:#354c47;color:#71837e;}
QComboBox,QSpinBox,QDoubleSpinBox {background:#242935;border:1px solid #383f4d;border-radius:4px;min-height:28px;padding:2px 7px;selection-background-color:#356858;}
QComboBox::drop-down {border:0;width:22px;}
QComboBox QAbstractItemView {background:#242935;selection-background-color:#356858;}
QListWidget {background:#191c23;border:0;outline:0;}
QListWidget::item {padding:10px 8px;border-bottom:1px solid #292e39;}
QListWidget::item:selected {background:#2a403d;color:#a5f3df;border-left:3px solid #64d9c1;}
QListWidget::item:hover {background:#292f3b;}
QTabWidget::pane {border:0;border-top:1px solid #343a47;}
QTabBar::tab {background:#1a1d25;color:#9ca4b7;padding:10px 15px;border-bottom:2px solid transparent;}
QTabBar::tab:selected {color:#8be6d0;border-bottom:2px solid #64d9c1;}
QScrollArea {border:0;}
QScrollBar:vertical {background:#1a1d25;width:8px;}
QScrollBar::handle:vertical {background:#414956;min-height:30px;border-radius:4px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QSlider::groove:horizontal {background:#353c49;height:4px;border-radius:2px;}
QSlider::sub-page:horizontal {background:#64d9c1;border-radius:2px;}
QSlider::handle:horizontal {background:#d7eee8;width:12px;margin:-4px 0;border-radius:6px;}
QCheckBox {spacing:8px;background:transparent;}
QCheckBox::indicator {width:15px;height:15px;border:1px solid #5d687b;border-radius:3px;background:#252b35;}
QCheckBox::indicator:checked {background:#64d9c1;border-color:#64d9c1;}
QProgressBar {border:0;background:#303641;border-radius:3px;text-align:center;min-height:6px;}
QProgressBar::chunk {background:#64d9c1;border-radius:3px;}
QSplitter::handle {background:#101217;width:6px;height:6px;}
QToolTip {background:#303845;color:#f5f7fc;border:1px solid #5c6c80;padding:5px;}
'''
_icons=(core.ROOT/'assets').as_posix()
STYLE+=f'''QComboBox::down-arrow {{image:url("{_icons}/down.svg");width:12px;height:12px;}}
QAbstractSpinBox::up-button {{width:18px;border:0;}}
QAbstractSpinBox::down-button {{width:18px;border:0;}}
QAbstractSpinBox::up-arrow {{image:url("{_icons}/up.svg");width:10px;height:10px;}}
QAbstractSpinBox::down-arrow {{image:url("{_icons}/down.svg");width:10px;height:10px;}}
'''

def label(text='',kind=None):
    result=QLabel(text)
    if kind:result.setObjectName(kind)
    return result

def button(text,slot=None,primary=False):
    result=QPushButton(text)
    if slot:result.clicked.connect(slot)
    if primary:result.setObjectName('primary')
    return result

def combo(items):
    result=QComboBox()
    for text,value in items:result.addItem(text,value)
    result.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    result.setMinimumContentsLength(8)
    result.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    return result

def timecode(seconds):
    ms=round(max(0,seconds)*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'

def qimage(rgb):
    rgb=np.ascontiguousarray(rgb,dtype=np.uint8)
    return QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format.Format_RGB888).copy()

@dataclass
class Clip:
    info:core.VideoInfo
    media:dict
    settings:core.Settings=field(default_factory=core.Settings)
    options:exporter.ExportOptions=field(default_factory=exporter.ExportOptions)
    manual:list=field(default_factory=list)
    analysis:core.Analysis|None=None
    wave:list=field(default_factory=list)
    output:str=''
    analysis_options:analysis_engine.AnalysisOptions=field(default_factory=analysis_engine.AnalysisOptions)

class Bridge(QObject):
    progress=Signal(float,str)
    done=Signal(object)
    failed=Signal(str)
    partial=Signal(str)

class Viewer(QWidget):
    rectangle=Signal(list)
    def __init__(self):
        super().__init__()
        self.image=None;self.area=QRectF();self.drawing=False;self.anchor=None
        self.drag=None;self.selection=None;self.rotation=0;self.note=''
        self.setMinimumSize(400,225)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
    def set_image(self,rgb):
        self.image=qimage(rgb);self.update()
    def point(self,pos):
        return QPointF(max(0,min(1,(pos.x()-self.area.x())/max(1,self.area.width()))),
                       max(0,min(1,(pos.y()-self.area.y())/max(1,self.area.height()))))
    def source_point(self,p):
        x,y=p.x(),p.y()
        return {0:(x,y),90:(y,1-x),180:(1-x,1-y),270:(1-y,x)}[self.rotation]
    def display_rect(self,rect):
        x0,y0,x1,y1=rect
        points=[]
        for x,y in [(x0,y0),(x1,y1)]:
            dx,dy={0:(x,y),90:(1-y,x),180:(1-x,1-y),270:(y,1-x)}[self.rotation]
            points.append(QPointF(self.area.x()+dx*self.area.width(),self.area.y()+dy*self.area.height()))
        return QRectF(*points).normalized()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#0c0e13'))
        if not self.image:
            p.setPen(QColor('#566175'));p.setFont(QFont('Microsoft YaHei UI',32))
            p.drawText(self.rect().adjusted(0,-42,0,0),Qt.AlignmentFlag.AlignCenter,'＋')
            p.setFont(QFont('Microsoft YaHei UI',13));p.setPen(QColor('#a5afc0'))
            p.drawText(self.rect().adjusted(0,58,0,0),Qt.AlignmentFlag.AlignCenter,'导入视频，开始处理')
            return
        size=self.image.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        self.area=QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())
        p.drawImage(self.area,self.image)
        if self.note:
            p.fillRect(QRectF(12,12,min(self.width()-24,460),32),QColor(15,22,30,225))
            p.setPen(QColor('#e8c786'));p.drawText(24,33,self.note)
        p.setPen(QPen(QColor('#74eed2'),2,Qt.PenStyle.DashLine))
        if self.selection:p.drawRect(self.display_rect(self.selection))
        if self.drag:p.drawRect(self.drag.normalized())
        if self.drawing:
            p.fillRect(QRectF(12,12,min(self.width()-24,365),30),QColor(15,28,28,230))
            p.setPen(QColor('#b7ffea'));p.drawText(24,32,'拖动画框；移动到其他时间可重新定位')
    def mousePressEvent(self,event):
        if self.drawing and self.image and self.area.contains(event.position()) and event.button()==Qt.MouseButton.LeftButton:
            self.anchor=event.position();self.drag=QRectF(self.anchor,self.anchor)
    def mouseMoveEvent(self,event):
        if self.anchor is not None:
            self.drag=QRectF(self.anchor,event.position());self.update()
    def mouseReleaseEvent(self,event):
        if self.anchor is None:return
        a=self.source_point(self.point(self.anchor));b=self.source_point(self.point(event.position()))
        self.anchor=None;self.drag=None
        rect=[min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])]
        if rect[2]-rect[0]>.006 and rect[3]-rect[1]>.006:self.rectangle.emit(rect)
        self.update()

class Timeline(QWidget):
    seek=Signal(int)
    seekFinished=Signal(int)
    def __init__(self):
        super().__init__();self.clip=None;self.index=0;self.cached=None;self.cache_key=None
        self.setMinimumHeight(146);self.setMouseTracking(True)
    def x(self,frame):
        return 96+frame/max(1,self.clip.info.frames)*(self.width()-118)
    def invalidate(self):
        self.cached=None;self.update()
    def paintEvent(self,event):
        c=self.clip
        key=(self.width(),self.height(),id(c),c.info.frames if c else 0,
             c.options.start_frame if c else 0,c.options.end_frame if c else 0,
             id(c.analysis.review) if c and c.analysis else 0,id(c.wave) if c else 0)
        if self.cached is None or key!=self.cache_key:
            self.cache_key=key;self.cached=QPixmap(self.size());p=QPainter(self.cached)
            self.draw_tracks(p);p.end()
        p=QPainter(self);p.drawPixmap(0,0,self.cached)
        if not c:return
        p.setPen(QPen(QColor('#eaf8f5'),1));x=self.x(self.index)
        p.drawLine(QPointF(x,23),QPointF(x,self.height()));p.fillRect(QRectF(x-4,23,8,6),QColor('#eaf8f5'))
    def draw_tracks(self,p):
        p.fillRect(self.rect(),QColor('#191c23'))
        if not self.clip:
            p.setPen(QColor('#677185'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'视频、音频与回看标记会显示在这里');return
        c=self.clip;frames=c.info.frames;end=c.options.end_frame if c.options.end_frame>=0 else frames
        p.setFont(QFont('Microsoft YaHei UI',9))
        for i in range(7):
            x=self.x(frames*i/6)
            p.setPen(QColor('#343b48'));p.drawLine(QPointF(x,27),QPointF(x,self.height()))
            p.setPen(QColor('#8692a6'));p.drawText(QRectF(x-27,4,70,22),Qt.AlignmentFlag.AlignCenter,timecode(c.info.duration*i/6).split('.')[0])
        for y,name in [(53,'V1  视频'),(94,'A1  音频'),(128,'待回看')]:
            p.setPen(QColor('#9aa8bb'));p.drawText(12,y,name)
        track=QRectF(self.x(0),32,self.x(frames)-self.x(0),33)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#314753'));p.drawRoundedRect(track,4,4)
        p.setPen(QColor('#c1d6df'));p.drawText(track.adjusted(10,0,-6,0),Qt.AlignmentFlag.AlignVCenter,Path(c.info.path).name)
        p.fillRect(QRectF(self.x(0),72,track.width(),34),QColor('#223e38'))
        if c.wave:
            p.setPen(QPen(QColor('#58bca3'),1))
            for i,value in enumerate(c.wave):
                x=self.x(frames*i/max(1,len(c.wave)-1));height=min(14,max(1,value*30))
                p.drawLine(QPointF(x,89-height),QPointF(x,89+height))
        else:
            p.setPen(QColor('#689083'));p.drawText(QRectF(self.x(0)+10,72,220,34),Qt.AlignmentFlag.AlignVCenter,'无音轨' if not c.media['audio_codec'] else '原音轨 · 随视频播放')
        if c.analysis:
            for r in c.analysis.review:
                p.fillRect(QRectF(self.x(r['start']),119,max(3,self.x(r['end']+1)-self.x(r['start'])),8),QColor('#ca9552'))
        for box in c.manual:
            p.setPen(QColor('#ad99e1'))
            for key in box.get('keyframes',[]):p.drawText(QPointF(self.x(key['frame'])-4,145),'◆')
        p.fillRect(QRectF(self.x(0),30,self.x(c.options.start_frame)-self.x(0),self.height()-30),QColor(10,12,16,165))
        p.fillRect(QRectF(self.x(end),30,self.x(frames)-self.x(end),self.height()-30),QColor(10,12,16,165))
        p.setPen(QPen(QColor('#70dec4'),2))
        for frame in [c.options.start_frame,end]:p.drawLine(QPointF(self.x(frame),30),QPointF(self.x(frame),109))
    def move(self,event):
        if self.clip:
            self.seek.emit(max(0,min(self.clip.info.frames-1,round((event.position().x()-96)/max(1,self.width()-118)*self.clip.info.frames))))
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.move(event)
    def mouseMoveEvent(self,event):
        if event.buttons()&Qt.MouseButton.LeftButton:self.move(event)
    def mouseReleaseEvent(self,event):
        if self.clip and event.button()==Qt.MouseButton.LeftButton:
            self.seekFinished.emit(max(0,min(self.clip.info.frames-1,round((event.position().x()-96)/max(1,self.width()-118)*self.clip.info.frames))))

class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'视频一键打码工具 {VERSION} · 本地视频处理')
        self.setMinimumSize(980,640);self.resize(1460,900);self.setAcceptDrops(True)
        self.clips=[];self.current=-1;self.index=0;self.raw=None;self.loading=False
        self.busy=False;self.cancel=threading.Event();self.thread=None;self.callback=None
        self.project_path=None;self.last_output=None;self.last_paint=0;self.draw_target=-1
        self.pending_seek=None;self.pending_play=False
        self.epoch=0;self.job_kind='';self.analysis_target=None;self.exact_pending=False
        self.ui_stamp=0.;self.render_count=0;self.fps_stamp=time.monotonic();self.preview_fps=0
        self.renderer=LatestRenderer();self.renderer.events.ready.connect(self.render_ready)
        self.renderer.events.failed.connect(self.render_failed)
        self.seek_timer=QTimer(self);self.seek_timer.setSingleShot(True);self.seek_timer.setInterval(140);self.seek_timer.timeout.connect(self.request_still)
        self.bridge=Bridge();self.bridge.progress.connect(self.on_progress)
        self.bridge.done.connect(self.on_done);self.bridge.failed.connect(self.on_failed)
        self.bridge.partial.connect(self.on_partial)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self)
        self.player.setAudioOutput(self.audio);self.native_video=QVideoWidget();self.player.setVideoOutput(self.native_video);self.sink=self.native_video.videoSink()
        self.sink.videoFrameChanged.connect(self.video_frame)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.playbackStateChanged.connect(lambda state:self.play_button.setText('暂停' if state==QMediaPlayer.PlaybackState.PlayingState else '播放'))
        self.player.errorOccurred.connect(lambda *_:self.preview_error())
        self.build_ui();self.bind_shortcuts()
    @property
    def clip(self):return self.clips[self.current] if 0<=self.current<len(self.clips) else None
    def needs_analysis(self,c):
        return not(c.analysis and c.analysis.completed and c.analysis.stats.get('profile')==c.analysis_options.profile)
    def analysis_controls_changed(self,*_):
        if self.loading:return
        profile=self.analysis_profile.currentData()
        self.analysis_hint.setText(analysis_engine.PROFILES[profile][1])
        if self.clip:
            self.clip.analysis_options=analysis_engine.AnalysisOptions(profile,self.analysis_device.currentData(),self.analysis_decode.currentData(),tracking=self.clip.analysis_options.tracking)
            current=self.clip.analysis.stats.get('profile') if self.clip.analysis else None
            if current and current!=profile:self.analysis_hint.setText(analysis_engine.PROFILES[profile][1]+' 当前结果尚未切换；点击“开始打码”应用新方案。')
            self.update_actions()
    def on_partial(self,path):
        c=self.analysis_target
        if c is None:return
        old=c.analysis
        if old and getattr(old.faces,'path',None)==Path(path):
            old.faces.refresh();old.completed=old.faces.complete;old.review=old.faces.review()
            old.stats=dict(old.faces.state.get('stats',{}),face_frames=old.faces.state.get('face_frames',0))
        else:
            self.epoch+=1;self.renderer.discard()
            c.analysis=analysis_engine.open_analysis(c.info,path)
            if old:old.close()
        if c is self.clip:
            self.bin.item(self.current).setText(self.clip_text(c));self.rebuild_review();self.refresh_frame()
        self.update_actions()
    def rebuild_review(self):
        self.review_list.clear();c=self.clip
        if c and c.analysis:
            for r in c.analysis.review[:500]:
                item=QListWidgetItem(f'{timecode(r["start"]/c.info.fps)}   {r["reason"]}');item.setToolTip(r['reason']);item.setData(Qt.ItemDataRole.UserRole,r['start']);self.review_list.addItem(item)
            if len(c.analysis.review)>500:
                item=QListWidgetItem('列表显示前 500 段，其余可在时间线上查看');item.setData(Qt.ItemDataRole.UserRole,0);self.review_list.addItem(item)
    def clear_analysis_cache(self):
        c=self.clip
        if not c or self.busy:return
        self.epoch+=1;self.renderer.discard();self.seek_timer.stop()
        target=analysis_cache.cache_path(c.info,c.analysis_options)
        if c.analysis:c.analysis.close();c.analysis=None
        analysis_cache.clear_for(target);self.rebuild_review();self.bin.item(self.current).setText(self.clip_text(c));self.refresh_frame()
        self.status.setText('当前方案的识别缓存已清除。原视频与手动画框仍保留；点击“开始打码”可重新识别。')
    def wants_native(self):
        c=self.clip
        return bool(c and c.options.input_color=='auto' and c.options.input_range=='auto' and c.options.output_color=='preserve' and not self.viewer.drawing and not c.options.rotation and
                    (self.compare.isChecked() or (not c.manual and (not c.analysis or self.index>=len(c.analysis.faces) or not c.options.auto_mask))))
    def render_task(self,source,index):
        c=self.clip
        if not c:return
        settings,options,manual=copy.deepcopy((c.settings,c.options,c.manual))
        limit=self.preview_quality.currentData() or max(c.info.width,c.info.height)
        self.renderer.submit((self.epoch,index,source,c.info,c.analysis,settings,options,manual,self.compare.isChecked(),limit,c.media))
    def request_still(self):
        if not self.clip or self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:return
        self.exact_pending=True;self.render_task(None,self.index)
    def render_ready(self,result):
        if result['epoch']!=self.epoch or not self.clip:return
        self.index=result['index'];self.raw=result['raw'];self.raw_index=self.index
        self.viewer.image=result['image'];self.viewer.rotation=self.clip.options.rotation
        pending=self.clip.analysis is not None and not result['known']
        self.viewer.note='此处尚未分析 · 当前显示原片与手动遮挡' if pending and not self.compare.isChecked() else ''
        self.monitor.setCurrentWidget(self.viewer);self.viewer.update()
        self.update_monitor_labels(result['faces'],throttle=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState)
    def render_failed(self,epoch,message):
        if epoch==self.epoch:self.status.setText('预览暂时不可用：'+message[:160]);self.exact_pending=False
    def preview_error(self):
        if self.clip:
            self.status.setText('原片实时播放不可用，可用左右键逐帧检查。'+self.player.errorString())
            self.request_still()
    def update_monitor_labels(self,faces=None,throttle=False):
        c=self.clip
        if not c:return
        now=time.monotonic();self.render_count+=1
        if now-self.fps_stamp>=1:
            self.preview_fps=self.render_count/(now-self.fps_stamp);self.render_count=0;self.fps_stamp=now
        self.timeline.index=self.index;self.timeline.update()
        if throttle and now-self.ui_stamp<.1:return
        self.ui_stamp=now
        known=c.analysis is not None and self.index<len(c.analysis.faces)
        if self.compare.isChecked():text='原片对比'
        elif not c.analysis:text='原片预览 · 点击“开始打码”后识别'
        elif not known:text='此处尚未分析 · 当前显示原片'
        else:text=f'打码预览 · {len(c.analysis.faces[self.index]) if faces is None else faces} 张脸'
        self.detection.setText(text+f'   ·   第 {self.index+1} 帧')
        self.clock_label.setText(f'{timecode(self.index/c.info.fps)}  /  {timecode(c.info.duration)}'+(f'   ·   预览 {self.preview_fps:.0f} fps' if throttle else ''))
    def build_ui(self):
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);layout.setContentsMargins(14,12,14,10);layout.setSpacing(9)
        top=QHBoxLayout();top.addWidget(label('视频一键打码工具','brand'));top.addSpacing(12)
        top.addWidget(label(f'by {AUTHOR}  /  {VERSION}','muted'));top.addStretch()
        top.addWidget(button('作者 / 支持 / 反馈',lambda:ProjectDialog(self).exec()))
        self.import_button=button('＋ 导入视频',self.import_dialog);top.addWidget(self.import_button)
        self.open_project_button=button('打开项目',self.open_project);top.addWidget(self.open_project_button)
        top.addWidget(button('保存项目',self.save_project));top.addSpacing(8)
        self.analyze_button=button('开始打码',self.analyze_current,True);top.addWidget(self.analyze_button)
        self.export_button=button('导出视频  ↗',self.export_current);top.addWidget(self.export_button);layout.addLayout(top)
        self.vertical=QSplitter(Qt.Orientation.Vertical);layout.addWidget(self.vertical,1)
        upper=QSplitter(Qt.Orientation.Horizontal);self.vertical.addWidget(upper)
        left=QFrame();left.setObjectName('panel');left.setMinimumWidth(180);ll=QVBoxLayout(left);ll.setContentsMargins(10,13,10,10)
        row=QHBoxLayout();row.addWidget(label('素材','heading'));row.addStretch();self.clip_count=label('0 个','muted');row.addWidget(self.clip_count);ll.addLayout(row)
        note=label('拖入视频 · 支持多选导入','muted');ll.addWidget(note)
        self.bin=QListWidget();self.bin.currentRowChanged.connect(self.select_clip);ll.addWidget(self.bin,1)
        self.clear_cache_button=button('清除当前识别缓存',self.clear_analysis_cache);ll.addWidget(self.clear_cache_button)
        self.remove_button=button('移除素材',self.remove_clip);ll.addWidget(self.remove_button)
        self.batch_button=button('批量导出全部素材',self.export_batch);ll.addWidget(self.batch_button)
        ll.addWidget(label('离线处理 · 视频保留在本机','muted'));upper.addWidget(left)
        center=QFrame();center.setObjectName('panel');cl=QVBoxLayout(center);cl.setContentsMargins(10,11,10,8)
        row=QHBoxLayout();self.video_title=label('预览','heading');row.addWidget(self.video_title,1)
        self.compare=QCheckBox('对比原画');self.compare.toggled.connect(self.refresh_frame);row.addWidget(self.compare);cl.addLayout(row)
        self.meta=label('原分辨率导出 · 预览使用轻量缓存','muted');cl.addWidget(self.meta)
        self.viewer=Viewer();self.viewer.rectangle.connect(self.draw_rectangle)
        self.monitor=QStackedWidget();self.monitor.addWidget(self.viewer);self.monitor.addWidget(self.native_video);cl.addWidget(self.monitor,1)
        row=QHBoxLayout();self.detection=label('等待导入视频','muted');row.addWidget(self.detection);row.addStretch()
        self.snapshot_button=button('保存此帧',self.snapshot);self.snapshot_button.setToolTip('保存原尺寸 8 位 PNG 预览帧；高位深无损请导出视频。');row.addWidget(self.snapshot_button);cl.addLayout(row)
        row=QHBoxLayout();row.addStretch();row.addWidget(button('❮',lambda:self.seek(self.index-1)))
        self.play_button=button('播放',self.toggle_play);row.addWidget(self.play_button)
        row.addWidget(button('❯',lambda:self.seek(self.index+1)));row.addStretch();cl.addLayout(row)
        self.clock_label=label('00:00:00.000  /  00:00:00.000','muted');self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter);cl.addWidget(self.clock_label)
        upper.addWidget(center)
        right=QFrame();right.setObjectName('panel');right.setMinimumWidth(300);rl=QVBoxLayout(right);rl.setContentsMargins(0,4,0,8)
        self.tabs=QTabWidget();rl.addWidget(self.tabs);self.build_mask_tab();self.build_clip_tab();self.build_export_tab();upper.addWidget(right)
        upper.setSizes([210,860,340]);upper.setStretchFactor(1,1)
        bottom=QFrame();bottom.setObjectName('panel');bl=QVBoxLayout(bottom);bl.setContentsMargins(8,7,8,3);bl.setSpacing(2)
        row=QHBoxLayout();row.addWidget(label('时间线','heading'));row.addSpacing(12)
        self.in_button=button('设为起点  I',self.mark_in);self.out_button=button('设为终点  O',self.mark_out)
        row.addWidget(self.in_button);row.addWidget(self.out_button);row.addWidget(button('恢复整段',self.reset_range));row.addStretch()
        self.range_label=label('','muted');row.addWidget(self.range_label);bl.addLayout(row)
        self.timeline=Timeline();self.timeline.seek.connect(lambda i:self.seek(i,exact=False));self.timeline.seekFinished.connect(self.seek);bl.addWidget(self.timeline);self.vertical.addWidget(bottom)
        self.vertical.setSizes([640,190]);self.vertical.setStretchFactor(0,1)
        statusrow=QHBoxLayout();self.status=label('拖入先看原片，点击“开始打码”再分析。Space 播放，← → 逐帧，I / O 截取','muted');statusrow.addWidget(self.status,1)
        self.progress=QProgressBar();self.progress.setFixedWidth(170);self.progress.setTextVisible(False);self.progress.hide();statusrow.addWidget(self.progress)
        self.cancel_button=button('取消处理',lambda:self.cancel.set());self.cancel_button.hide();statusrow.addWidget(self.cancel_button)
        self.folder_button=button('打开成片文件夹',self.open_output);self.folder_button.setEnabled(False);statusrow.addWidget(self.folder_button);layout.addLayout(statusrow)
        self.update_actions()
    def page(self,title):
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);page=QWidget();page.setObjectName('inspectorPage')
        box=QVBoxLayout(page);box.setContentsMargins(16,16,16,12);box.setSpacing(13);scroll.setWidget(page);self.tabs.addTab(scroll,title)
        return box
    def hint(self,box,text):
        hint=label(text,'muted');hint.setWordWrap(True);box.addWidget(hint);return hint
    def build_mask_tab(self):
        box=self.page('遮挡')
        self.analysis_profile=combo([(v[0],k) for k,v in analysis_engine.PROFILES.items()]);self.analysis_profile.setCurrentIndex(1)
        devices=[('自动 · 实测选择较快设备','auto'),('CPU · 兼容模式','cpu')]
        devices += [(f'显卡 {a["id"]} · {a["name"]}',f'gpu:{a["id"]}') for a in acceleration.adapters()]
        self.analysis_device=combo(devices)
        self.analysis_decode=combo([('自动硬件解码 · 不可用时回退','auto'),('CPU 解码','cpu')])
        self.preview_quality=combo([('流畅 · 打码预览最高 720p',1280),('精细 · 打码预览原尺寸',0)])
        form=QFormLayout();form.addRow('识别方案',self.analysis_profile);form.addRow('计算设备',self.analysis_device)
        form.addRow('分析解码',self.analysis_decode);form.addRow('预览质量',self.preview_quality);box.addLayout(form)
        self.analysis_hint=self.hint(box,analysis_engine.PROFILES['balanced'][1])
        self.hint(box,'拖入只播放原片。点击顶部“开始打码”后才进行识别；分析中可继续预览。预览质量不影响导出。')
        self.auto=QCheckBox('自动遮挡检测到的人脸');self.auto.setChecked(True);box.addWidget(self.auto)
        form=QFormLayout();form.setSpacing(12)
        self.region=combo([('整张脸','full'),('眼睛区域','eyes'),('上半张脸','upper'),('下半张脸','lower')]);form.addRow('遮挡区域',self.region)
        self.effect=combo([('马赛克','mosaic'),('柔化模糊','blur'),('实色遮挡','solid')]);form.addRow('显示样式',self.effect)
        self.strength=QSlider(Qt.Orientation.Horizontal);self.strength.setRange(1,5);self.strength.setTickInterval(1);self.strength.setTickPosition(QSlider.TickPosition.TicksBelow);self.strength.setValue(4)
        self.strength_label=label('强度 4 / 5');form.addRow(self.strength_label,self.strength)
        self.coverage=QDoubleSpinBox();self.coverage.setRange(.75,2.0);self.coverage.setSingleStep(.05);self.coverage.setValue(1.15);self.coverage.setSuffix(' ×');form.addRow('覆盖范围',self.coverage)
        self.eye_height=QDoubleSpinBox();self.eye_height.setRange(.4,3);self.eye_height.setSingleStep(.1);self.eye_height.setValue(1);self.eye_height.setSuffix(' ×');form.addRow('眼睛条高度',self.eye_height);box.addLayout(form)
        box.addWidget(label('未检测到人脸时'))
        self.missing=combo([('保留画面 · 请人工检查','keep'),('整帧遮黑 · 保守模式','full_frame')]);box.addWidget(self.missing)
        self.hint(box,'整脸、半脸、眼睛均随五官角度变化。实色遮挡不受强度档位影响。')
        box.addWidget(label('需要回看的片段','heading'));self.review_list=QListWidget();self.review_list.setMinimumHeight(75);self.review_list.setMaximumHeight(160)
        self.review_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.review_list.itemClicked.connect(lambda item:self.seek(item.data(Qt.ItemDataRole.UserRole)));box.addWidget(self.review_list)
        self.hint(box,'点击跳转。橙色标记提示可能的问题，不能发现所有漏检。');box.addStretch()
        box=self.page('补码')
        box.addWidget(label('手动补码与关键帧','heading'))
        row=QHBoxLayout();self.add_box=button('＋ 画框',self.new_box);self.key_box=button('重画关键帧',self.reposition_box);row.addWidget(self.add_box);row.addWidget(self.key_box);box.addLayout(row)
        self.box_list=QListWidget();self.box_list.setMaximumHeight(95);self.box_list.currentRowChanged.connect(self.select_box);box.addWidget(self.box_list)
        row=QFormLayout();self.box_start=QDoubleSpinBox();self.box_end=QDoubleSpinBox()
        for control in (self.box_start,self.box_end):control.setDecimals(3);control.setRange(0,999999);control.setSuffix(' s')
        row.addRow('开始时间',self.box_start);row.addRow('结束时间',self.box_end);box.addLayout(row)
        row=QHBoxLayout();row.addWidget(button('删除框',self.delete_box));row.addWidget(button('删当前关键帧',self.delete_key));box.addLayout(row)
        self.hint(box,'画框默认覆盖所选导出区间。在不同时间重新画同一个框，位置和大小会在关键帧之间平滑过渡。')
        box.addStretch()
        for widget in (self.region,self.effect,self.missing):widget.currentIndexChanged.connect(self.controls_changed)
        for widget in (self.strength,self.coverage,self.eye_height):widget.valueChanged.connect(self.controls_changed)
        self.auto.toggled.connect(self.controls_changed)
        for control in (self.analysis_profile,self.analysis_device,self.analysis_decode):control.currentIndexChanged.connect(self.analysis_controls_changed)
        self.preview_quality.currentIndexChanged.connect(self.refresh_frame)
        self.box_start.valueChanged.connect(self.box_timing);self.box_end.valueChanged.connect(self.box_timing)
    def build_clip_tab(self):
        box=self.page('画面 / 声音');box.addWidget(label('片段处理','heading'))
        self.hint(box,'在时间线上定位，用 I 设置起点、O 设置终点。导出仅包含所选区间。')
        self.trim_detail=label('尚未导入素材');self.trim_detail.setWordWrap(True);box.addWidget(self.trim_detail)
        form=QFormLayout();self.rotation=combo([('保持原方向',0),('顺时针 90°',90),('旋转 180°',180),('逆时针 90°',270)]);form.addRow('画面旋转',self.rotation);box.addLayout(form)
        box.addWidget(label('音频','heading'))
        self.audio_mode=combo([('原音频直拷 · 不再压缩','copy'),('高质量 AAC · 320 kbps','aac'),('移除音轨','mute')]);box.addWidget(self.audio_mode)
        self.volume=QSlider(Qt.Orientation.Horizontal);self.volume.setRange(0,200);self.volume.setValue(100);box.addWidget(label('音量 · 仅 AAC 导出可调整'));box.addWidget(self.volume)
        self.volume_label=label('100%','muted');box.addWidget(self.volume_label)
        self.hint(box,'预览音量最高为正常音量；放大仅应用于导出。原音频保留编码，PCM 等音轨自动使用 MOV。压缩音轨直拷的截取边界按音频包对齐，可能相差数十毫秒。')
        self.extract_button=button('提取完整原片音轨 → WAV',self.extract_audio);box.addWidget(self.extract_button)
        self.hint(box,'WAV 使用 24 位 PCM，保留完整音轨的采样率和声道；不会让原有的有损音频恢复细节。')
        box.addStretch()
        self.rotation.currentIndexChanged.connect(self.controls_changed);self.audio_mode.currentIndexChanged.connect(self.controls_changed);self.volume.valueChanged.connect(self.controls_changed)
    def build_export_tab(self):
        box=self.page('导出');box.addWidget(label('画质优先','heading'))
        self.profile=combo([(v[0],k) for k,v in exporter.PROFILES.items()]);box.addWidget(self.profile)
        self.profile_hint=self.hint(box,exporter.PROFILES['native'][2])
        box.addWidget(label('色彩','heading'))
        self.source_color_info=self.hint(box,'导入后显示原片色彩信息。')
        form=QFormLayout()
        self.input_color=combo([('自动 · 读取原片标记','auto')]+[(v[0],k) for k,v in colors.SPACES.items()])
        self.input_range=combo([('自动 · 读取原片标记','auto'),('有限范围 · 视频电平','tv'),('全范围 · 数据电平','pc')])
        self.output_color=combo([('保留源色彩','preserve')]+[(v[0],k) for k,v in colors.SPACES.items()])
        form.addRow('输入色彩',self.input_color);form.addRow('输入范围',self.input_range);form.addRow('输出色彩',self.output_color);box.addLayout(form)
        self.color_note=self.hint(box,'一般保持自动即可。HDR / Log 的完整色彩管理暂未提供。')
        self.hint(box,'预览用于检查遮挡，不作为校准色彩监看。转换后的成片按输出色彩标记播放。')
        for widget in (self.input_color,self.input_range,self.output_color):widget.currentIndexChanged.connect(self.controls_changed)
        self.encoder=combo([('CPU · 画质优先','cpu'),('自动选择可用硬件编码','auto'),('AMD AMF','amf'),('NVIDIA NVENC','nvenc'),('Intel Quick Sync','qsv')])
        self.export_decode=combo([('CPU · 保守解码','cpu'),('自动硬件解码 · 可回退','auto')])
        hwform=QFormLayout();hwform.addRow('编码设备',self.encoder);hwform.addRow('导出解码',self.export_decode);box.addLayout(hwform)
        self.hint(box,'硬件编码通常更快，画质和体积与 CPU 预设不完全相同。启动测试失败会回退 CPU；ProRes / FFV1 使用 CPU 编码。')
        self.encoder.currentIndexChanged.connect(self.controls_changed);self.export_decode.currentIndexChanged.connect(self.controls_changed)
        form=QFormLayout();self.resolution=combo([('保持源分辨率',0),('最高 2160p',2160),('最高 1080p',1080),('最高 720p',720)]);form.addRow('输出尺寸',self.resolution)
        self.framerate=combo([('保持源帧率',0),('60 fps',60),('50 fps',50),('30 fps',30),('25 fps',25),('24 fps',24)]);form.addRow('输出帧率',self.framerate);box.addLayout(form)
        self.hint(box,'不会放大低分辨率素材，也不会用重复帧伪造高帧率。4K / 高帧率逐帧处理，速度取决于电脑与遮挡设置。')
        self.export_summary=label('导入素材后显示输出信息');self.export_summary.setWordWrap(True);box.addWidget(self.export_summary)
        box.addWidget(button('导出当前素材',self.export_current,True))
        self.hint(box,'源像素无损：完整原片，未遮挡样本保持不变；色度边界包含共用样本。RGB 无损：允许处理，编码不再丢失画面。两者文件都较大；字幕、附件及全部相机元数据不作保留承诺。')
        box.addWidget(label('项目与批量','heading'))
        box.addWidget(button('将当前参数应用到全部素材',self.apply_all))
        self.hint(box,'复制遮挡、画质、旋转和音频设置。各素材的截取区间、手动框分别保留。项目文件只记录引用路径与操作参数，不包含视频。')
        box.addStretch()
        self.profile.currentIndexChanged.connect(self.export_profile_changed)
        for widget in (self.resolution,self.framerate):widget.currentIndexChanged.connect(self.controls_changed)
    def export_profile_changed(self,*_):
        if self.loading:return
        if self.profile.currentData()=='native':
            for widget,value in ((self.input_color,'auto'),(self.input_range,'auto'),(self.output_color,'preserve'),(self.resolution,0),(self.framerate,0),(self.rotation,0),(self.export_decode,'cpu')):
                widget.blockSignals(True);widget.setCurrentIndex(widget.findData(value));widget.blockSignals(False)
            if self.audio_mode.currentData()=='aac':
                self.audio_mode.blockSignals(True);self.audio_mode.setCurrentIndex(self.audio_mode.findData('copy'));self.audio_mode.blockSignals(False)
            if self.clip:self.clip.options.start_frame=0;self.clip.options.end_frame=-1
            self.status.setText('源像素无损：已恢复完整原片、原尺寸和自动色彩；需要截取或转换时可切换 RGB 无损。')
        self.controls_changed()
    def bind_shortcuts(self):
        self.shortcuts=[]
        for key,slot in [('Space',self.toggle_play),('Left',lambda:self.seek(self.index-1)),('Right',lambda:self.seek(self.index+1)),('I',self.mark_in),('O',self.mark_out),('Ctrl+S',self.save_project),('Ctrl+O',self.import_dialog),('Escape',self.stop_drawing)]:
            shortcut=QShortcut(QKeySequence(key),self);shortcut.activated.connect(slot);self.shortcuts.append(shortcut)
    def message(self,text):QMessageBox.information(self,'视频一键打码工具',str(text))
    def update_actions(self):
        c=self.clip
        for widget in (self.import_button,self.open_project_button,self.remove_button,self.analyze_button,self.batch_button,self.clear_cache_button):widget.setEnabled(not self.busy)
        self.analyze_button.setEnabled(bool(c and not self.busy))
        self.clear_cache_button.setEnabled(bool(c and not self.busy))
        for widget in (self.analysis_profile,self.analysis_device,self.analysis_decode):widget.setEnabled(not self.busy)
        self.bin.setEnabled(not self.busy)
        complete=bool(c and not self.needs_analysis(c))
        self.export_button.setEnabled(bool(c and not self.busy and (complete or not c.options.auto_mask)))
        self.snapshot_button.setEnabled(bool(c and not self.busy))
        self.extract_button.setEnabled(bool(c and c.media['audio_codec'] and not self.busy))
        self.play_button.setEnabled(bool(c));self.add_box.setEnabled(bool(c));self.key_box.setEnabled(bool(c))
    def start_job(self,description,worker,callback,keep_preview=False):
        if self.busy:return
        if not keep_preview:self.player.pause()
        self.busy=True;self.cancel=threading.Event();self.callback=callback;self.started=time.monotonic();self.job_description=description
        self.progress.setValue(0);self.progress.show();self.cancel_button.show();self.status.setText(description);self.update_actions()
        self.cancel_button.setText('暂停分析' if self.job_kind=='analysis' else '取消处理')
        def run():
            try:self.bridge.done.emit(worker())
            except core.Cancelled:self.bridge.failed.emit('处理已暂停；已保存的分析进度可以继续。' if self.job_kind=='analysis' else '处理已取消，源文件未改动。')
            except Exception as error:self.bridge.failed.emit(str(error))
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def on_progress(self,value,text):
        self.progress.setValue(round(value*100))
        elapsed=time.monotonic()-self.started
        eta=f' · 预计剩余 {round(elapsed*(1-value)/value)} 秒' if value>.03 and elapsed>2 else ''
        self.status.setText(f'{text} · {value:.0%}{eta}')
    def finish_job(self):
        self.busy=False;self.job_kind='';self.progress.hide();self.cancel_button.hide();self.update_actions()
    def on_done(self,result):
        callback=self.callback;self.finish_job()
        try:callback(result)
        except Exception as error:self.on_failed(str(error))
    def on_failed(self,text):
        self.finish_job();self.status.setText(text)
        if '取消' not in text and '暂停' not in text:self.message(text)
    def import_dialog(self):
        if self.busy:return
        paths,_=QFileDialog.getOpenFileNames(self,'导入视频','',VIDEO_FILTER)
        if paths:self.import_paths(paths)
    def import_paths(self,paths,analyze=False):
        if self.busy:return
        existing={c.info.path for c in self.clips};paths=list(dict.fromkeys(str(Path(p).resolve()) for p in paths))
        self.job_kind='import'
        def worker():
            added=[];errors=[]
            for path in paths:
                core.check_cancel(self.cancel)
                if path in existing:continue
                try:
                    clip=Clip(core.probe(path),exporter.details(path))
                    clip.options.profile='native' if exporter.native_export.supported(clip.media['pixel_format']) else 'lossless'
                    added.append(clip)
                except Exception as error:errors.append(f'{Path(path).name}: {error}')
            return added,errors
        def done(result):
            added,errors=result;first=len(self.clips)
            self.bin.blockSignals(True)
            for c in added:self.clips.append(c);self.bin.addItem(self.clip_text(c))
            self.bin.blockSignals(False);self.clip_count.setText(f'{len(self.clips)} 个')
            if added:self.bin.setCurrentRow(first)
            self.status.setText('原片已就绪 · 可先预览、定位或画框，点击“开始打码”才运行识别。')
            if errors:self.message('\n'.join(errors))
            if analyze and added:self.analyze_current()
        self.start_job('读取素材信息 · 不进行人脸分析',worker,done,keep_preview=True)
    def clip_text(self,c):
        status='原片 · 尚未打码'
        if c.analysis:status='识别完成' if c.analysis.completed else f'已分析 {len(c.analysis.faces)}/{c.info.frames} 帧'
        return f'{Path(c.info.path).name}\n{c.info.width} × {c.info.height} · {c.info.fps:g} fps\n{timecode(c.info.duration)}  ·  {status}'
    def select_clip(self,index):
        self.epoch+=1;self.renderer.discard();self.seek_timer.stop();self.exact_pending=False
        self.pending_seek=None;self.pending_play=False
        self.player.stop();self.player.setSource(QUrl());self.current=index;self.index=0;self.raw=None;self.raw_index=-1;self.stop_drawing()
        c=self.clip
        if not c:self.viewer.image=None;self.monitor.setCurrentWidget(self.viewer);self.viewer.update();self.timeline.clip=None;self.timeline.update();self.update_actions();return
        self.loading=True
        try:
            for widget,value in [(self.region,c.settings.region),(self.effect,c.settings.style),(self.missing,c.settings.missing),
                    (self.rotation,c.options.rotation),(self.audio_mode,c.options.audio),(self.profile,c.options.profile),(self.resolution,c.options.resolution),(self.framerate,c.options.fps),
                    (self.analysis_profile,c.analysis_options.profile),(self.analysis_device,c.analysis_options.device),(self.analysis_decode,c.analysis_options.decode),
                    (self.encoder,c.options.encoder),(self.export_decode,c.options.decode),
                    (self.input_color,c.options.input_color),(self.input_range,c.options.input_range),(self.output_color,c.options.output_color)]:
                widget.setCurrentIndex(max(0,widget.findData(value)))
            self.auto.setChecked(c.options.auto_mask);self.strength.setValue(c.settings.strength);self.coverage.setValue(c.settings.coverage);self.eye_height.setValue(c.settings.eye_height);self.volume.setValue(round(c.options.volume*100))
            self.video_title.setText(Path(c.info.path).name)
            self.meta.setText(f'{c.info.width} × {c.info.height}   /   {c.info.fps:g} fps   /   {c.media["audio_codec"] or "无音轨"}     ·     直接播放原片')
            self.box_list.clear()
            for i,box in enumerate(c.manual):self.box_list.addItem(self.box_text(i,box))
            self.rebuild_review()
            self.timeline.clip=c;self.monitor.setCurrentWidget(self.native_video)
            self.player.setSource(QUrl.fromLocalFile(c.info.path))
        finally:self.loading=False
        self.controls_changed();self.analysis_controls_changed();self.update_actions();self.update_monitor_labels()
    def analyze_clip(self,c,progress):
        result=analysis_engine.analyze(c.info,copy.deepcopy(c.analysis_options),self.cancel,progress)
        c.info.frames=result['frames'];c.info.duration=result['frames']/c.info.fps
        return analysis_engine.open_analysis(c.info,result['path']),[]
    def analyze_current(self):
        c=self.clip
        if not c or self.busy:return
        self.analysis_controls_changed();self.analysis_target=c;self.job_kind='analysis';options=copy.deepcopy(c.analysis_options)
        def worker():return analysis_engine.analyze(c.info,options,self.cancel,lambda v,*_:self.bridge.progress.emit(v,'正在识别人脸 · 可继续预览'),self.bridge.partial.emit)
        def done(result):
            self.on_partial(result['path']);c.info.frames=result['frames'];c.info.duration=result['frames']/c.info.fps
            self.bin.item(self.clips.index(c)).setText(self.clip_text(c));self.update_range();self.refresh_frame();self.update_actions()
            stats=c.analysis.stats;count=stats.get('face_frames',0)
            text='已复用本机分析缓存' if result['cache_hit'] else f'完成 · {stats.get("elapsed_seconds",0):.1f} 秒 · {stats.get("frames_per_second",0):.1f} 帧/秒'
            self.status.setText(f'{text} · {count}/{c.info.frames} 帧有遮挡 · {stats.get("inference", "")} · {stats.get("decoder", "")}')
        self.start_job('准备识别 · 检查本机缓存和计算设备',worker,done,keep_preview=True)
    def controls_changed(self,*_):
        if self.loading:return
        c=self.clip
        if c:
            c.settings=core.Settings(self.region.currentData(),self.effect.currentData(),self.strength.value(),self.coverage.value(),self.eye_height.value(),self.missing.currentData())
            c.options.profile=self.profile.currentData();c.options.audio=self.audio_mode.currentData();c.options.rotation=self.rotation.currentData();c.options.auto_mask=self.auto.isChecked()
            c.options.resolution=self.resolution.currentData();c.options.fps=self.framerate.currentData();c.options.volume=self.volume.value()/100 if c.options.audio=='aac' else 1
            c.options.input_color=self.input_color.currentData();c.options.input_range=self.input_range.currentData();c.options.output_color=self.output_color.currentData()
        self.strength_label.setText(f'强度 {self.strength.value()} / 5');self.strength.setEnabled(self.effect.currentData()!='solid')
        self.eye_height.setEnabled(self.region.currentData()=='eyes');self.volume.setEnabled(self.audio_mode.currentData()=='aac');self.volume_label.setText(f'{self.volume.value()}%')
        self.profile_hint.setText(exporter.PROFILES[self.profile.currentData()][2])
        if c:
            c.options.encoder=self.encoder.currentData();c.options.decode=self.export_decode.currentData()
        self.encoder.setEnabled(self.profile.currentData() in ('quality','compact'))
        native=self.profile.currentData()=='native'
        for widget in (self.input_color,self.input_range,self.output_color,self.resolution,self.framerate,self.rotation,self.export_decode):widget.setEnabled(not native)
        if c:
            self.source_color_info.setText(colors.description(c.media))
            self.color_note.setText(colors.policy_note(c.media,c.options))
            if native:
                try:exporter.validate(c.info,c.options,c.media)
                except ValueError as error:self.color_note.setText(str(error)+'\n切换 RGB 无损可继续调整。')
        self.audio.setMuted(self.audio_mode.currentData()=='mute');self.audio.setVolume(min(1,c.options.volume) if c else 1)
        self.update_range();self.refresh_frame()
    def update_range(self):
        c=self.clip
        if not c:return
        end=c.options.end_frame if c.options.end_frame>=0 else c.info.frames
        duration=(end-c.options.start_frame)/c.info.fps
        text=f'{timecode(c.options.start_frame/c.info.fps)} → {timecode(end/c.info.fps)}   ·   {duration:.3f} s'
        self.range_label.setText(text);self.trim_detail.setText(text)
        w,h=exporter.geometry(c.info,c.options)
        if c.options.rotation in (90,270):w,h=h,w
        suffix=exporter.suffix_for(c.options,c.media)
        self.export_summary.setText(f'输出 {w} × {h}  ·  {c.options.fps or c.info.fps:g} fps\n时长 {duration:.3f} 秒  ·  {suffix.upper()[1:]}\n'+('音频保留原编码' if c.options.audio=='copy' else '无音轨' if c.options.audio=='mute' else f'AAC 320 kbps · 音量 {c.options.volume:.0%}'))
        self.timeline.update()
    def refresh_frame(self,*_):
        self.timeline.invalidate()
        c=self.clip
        if not c or self.loading:return
        self.epoch+=1;self.renderer.discard();self.exact_pending=False
        self.viewer.rotation=c.options.rotation
        row=self.box_list.currentRow()
        self.viewer.selection=core.manual_rect(c.manual[row],self.index) if 0<=row<len(c.manual) and c.manual[row]['start']<=self.index<=c.manual[row]['end'] else None
        if self.wants_native():
            self.monitor.setCurrentWidget(self.native_video);self.update_monitor_labels()
        else:
            self.monitor.setCurrentWidget(self.viewer)
            if self.raw is not None and getattr(self,'raw_index',-1)==self.index:self.render_task(self.raw,self.index)
            elif self.player.playbackState()!=QMediaPlayer.PlaybackState.PlayingState:self.seek_timer.start()
        self.viewer.update();self.update_actions()
    def video_frame(self,frame):
        c=self.clip
        if not c or not frame.isValid():return
        playing=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        if playing:
            stamp=frame.startTime()/1000000 if frame.startTime()>=0 else self.player.position()/1000
            index=max(0,min(c.info.frames-1,round(stamp*c.info.fps)))
            end=c.options.end_frame if c.options.end_frame>=0 else c.info.frames
            if index>=end:self.player.pause();self.seek(end-1);return
        else:index=self.index
        if self.wants_native() and not self.exact_pending:
            self.index=index;self.monitor.setCurrentWidget(self.native_video);self.update_monitor_labels(throttle=playing)
        elif not self.exact_pending:
            # Map hardware video frames while their Qt rendering context is on
            # the GUI thread; only CPU images cross to the mask worker.
            im=frame.toImage().copy()
            if not im.isNull():self.render_task(im,index)
    def media_status(self,status):
        if self.player.isSeekable() and self.pending_seek is not None:
            position=self.pending_seek;self.pending_seek=None;self.player.setPosition(position)
        if self.pending_play and self.player.isSeekable():
            self.pending_play=False;self.player.play()
    def seek(self,index,exact=True):
        c=self.clip
        if not c:return
        self.pending_play=False;self.player.pause();self.epoch+=1;self.renderer.discard()
        self.index=max(0,min(c.info.frames-1,index));self.exact_pending=exact
        position=round(self.index/c.info.fps*1000)
        if self.player.isSeekable():self.pending_seek=None;self.player.setPosition(position)
        else:self.pending_seek=position
        if exact:self.seek_timer.start()
        else:self.seek_timer.stop()
        self.update_monitor_labels()
    def toggle_play(self):
        c=self.clip
        if not c:return
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause();return
        self.stop_drawing();self.seek_timer.stop();self.exact_pending=False;self.epoch+=1;self.renderer.discard()
        end=c.options.end_frame if c.options.end_frame>=0 else c.info.frames
        if self.index>=end-1 or self.index<c.options.start_frame:self.index=c.options.start_frame
        position=round(self.index/c.info.fps*1000)
        if self.wants_native():self.monitor.setCurrentWidget(self.native_video)
        if self.player.isSeekable():self.pending_seek=None;self.player.setPosition(position);self.player.play()
        else:self.pending_seek=position;self.pending_play=True;self.player.play()
    def mark_in(self):
        c=self.clip
        if not c:return
        end=c.options.end_frame if c.options.end_frame>=0 else c.info.frames
        c.options.start_frame=min(self.index,end-1);self.update_range()
    def mark_out(self):
        c=self.clip
        if not c:return
        c.options.end_frame=max(c.options.start_frame+1,self.index+1);self.update_range()
    def reset_range(self):
        if self.clip:self.clip.options.start_frame=0;self.clip.options.end_frame=-1;self.update_range()
    def new_box(self):
        if not self.clip:return
        self.player.pause();self.draw_target=-1;self.viewer.drawing=True;self.viewer.setCursor(Qt.CursorShape.CrossCursor)
        self.refresh_frame();self.request_still()
    def reposition_box(self):
        if self.box_list.currentRow()<0:return
        self.player.pause();self.draw_target=self.box_list.currentRow();self.viewer.drawing=True;self.viewer.setCursor(Qt.CursorShape.CrossCursor);self.viewer.update()
    def stop_drawing(self):
        if hasattr(self,'viewer'):
            self.viewer.drawing=False;self.viewer.setCursor(Qt.CursorShape.ArrowCursor);self.viewer.update()
    def box_text(self,i,box):return f'遮挡 {i+1}   ·   {len(box.get("keyframes",[]))} 个关键帧'
    def draw_rectangle(self,rect):
        c=self.clip
        if not c:return
        if self.draw_target<0 or self.draw_target>=len(c.manual):
            end=c.options.end_frame if c.options.end_frame>=0 else c.info.frames
            box={'start':c.options.start_frame,'end':end-1,'rect':rect,'keyframes':[{'frame':self.index,'rect':rect}]}
            c.manual.append(box);row=len(c.manual)-1;self.box_list.addItem(self.box_text(row,box))
        else:
            row=self.draw_target;box=c.manual[row];keys=[k for k in box.get('keyframes',[]) if k['frame']!=self.index]
            keys.append({'frame':self.index,'rect':rect});box['keyframes']=sorted(keys,key=lambda k:k['frame'])
            box['start']=min(box['start'],self.index);box['end']=max(box['end'],self.index);self.box_list.item(row).setText(self.box_text(row,box))
        self.stop_drawing();self.box_list.setCurrentRow(row);self.select_box(row);self.refresh_frame()
    def select_box(self,row):
        c=self.clip
        if not c or not 0<=row<len(c.manual):self.viewer.selection=None;self.viewer.update();return
        self.loading=True
        try:self.box_start.setValue(c.manual[row]['start']/c.info.fps);self.box_end.setValue((c.manual[row]['end']+1)/c.info.fps)
        finally:self.loading=False
        self.refresh_frame()
    def box_timing(self):
        c=self.clip;row=self.box_list.currentRow()
        if self.loading or not c or not 0<=row<len(c.manual):return
        start=min(c.info.frames-1,max(0,round(self.box_start.value()*c.info.fps)))
        end=max(start,min(c.info.frames-1,round(self.box_end.value()*c.info.fps)-1))
        c.manual[row]['start']=start;c.manual[row]['end']=end;self.refresh_frame()
    def delete_box(self):
        row=self.box_list.currentRow()
        if self.clip and 0<=row<len(self.clip.manual):
            del self.clip.manual[row];self.box_list.takeItem(row)
            for i,box in enumerate(self.clip.manual):self.box_list.item(i).setText(self.box_text(i,box))
            self.refresh_frame()
    def delete_key(self):
        row=self.box_list.currentRow()
        if not self.clip or not 0<=row<len(self.clip.manual):return
        box=self.clip.manual[row];keys=box.get('keyframes',[])
        if len(keys)<=1:self.status.setText('最后一个关键帧用于确定位置；可删除整个遮挡框。');return
        box['keyframes']=[key for key in keys if key['frame']!=self.index]
        self.box_list.item(row).setText(self.box_text(row,box));self.refresh_frame()
    def remove_clip(self):
        if self.busy or not self.clip:return
        row=self.current;c=self.clip;self.player.stop();self.player.setSource(QUrl())
        self.bin.blockSignals(True);self.bin.takeItem(row);del self.clips[row];self.bin.blockSignals(False)
        if c.analysis:c.analysis.close()
        self.current=-1;self.bin.setCurrentRow(min(row,len(self.clips)-1));self.select_clip(self.bin.currentRow());self.clip_count.setText(f'{len(self.clips)} 个')
    def apply_all(self):
        if not self.clip:return
        for c in self.clips:
            start,end=c.options.start_frame,c.options.end_frame
            c.settings=copy.deepcopy(self.clip.settings);c.options=copy.deepcopy(self.clip.options)
            c.analysis_options=copy.deepcopy(self.clip.analysis_options)
            c.options.start_frame=start;c.options.end_frame=end
        self.status.setText('当前遮挡、画质、旋转和音频参数已应用到全部素材。')
    def export_current(self):
        c=self.clip
        if not c or self.busy:return
        if c.options.auto_mask and self.needs_analysis(c):self.message('请先点击“开始打码”完成当前方案的识别，或关闭自动遮挡后仅导出手动画框。');return
        settings,options,manual=copy.deepcopy((c.settings,c.options,c.manual))
        try:exporter.validate(c.info,options,c.media)
        except Exception as error:self.tabs.setCurrentIndex(self.tabs.count()-1);self.message(error);return
        suffix=exporter.suffix_for(options,c.media)
        suggested=str(Path(c.info.path).with_name(Path(c.info.path).stem+'_处理'+suffix))
        path,_=QFileDialog.getSaveFileName(self,'导出视频',suggested,f'视频 (*{suffix})')
        if not path:return
        if not Path(path).suffix:path+=suffix
        analysis=c.analysis if options.auto_mask else analysis_engine.manual_analysis(c.info)
        def worker():
            result=exporter.export(analysis,path,settings,options,manual,self.cancel,lambda v,*_:self.bridge.progress.emit(v,'视频导出'),c.media)
            self.last_export_backend=analysis.stats.get('export_encoder','CPU')+(' · 硬件不可用，已回退' if analysis.stats.get('export_fallback') else '')
            return result
        self.start_job('高质量导出',worker,self.export_done)
    def export_done(self,path):
        self.last_output=str(path);self.folder_button.setEnabled(True);self.status.setText(f'导出完成 · {getattr(self,"last_export_backend", "")} · {path}')
    def export_batch(self):
        if self.busy or not self.clips:return
        for c in self.clips:
            try:exporter.validate(c.info,c.options,c.media)
            except Exception as error:self.message(f'{Path(c.info.path).name}: {error}');return
        folder=QFileDialog.getExistingDirectory(self,'选择批量导出文件夹')
        if not folder:return
        jobs=[(c,*copy.deepcopy((c.settings,c.options,c.manual))) for c in self.clips]
        self.player.stop();self.player.setSource(QUrl())
        def worker():
            outputs=[]
            for number,(c,settings,options,manual) in enumerate(jobs):
                core.check_cancel(self.cancel)
                title=f'批量 {number+1}/{len(jobs)} · {Path(c.info.path).name}'
                progress=lambda value,*_:self.bridge.progress.emit((number+value)/len(jobs),title)
                if options.auto_mask and self.needs_analysis(c):
                    if c.analysis:c.analysis.close()
                    c.analysis,c.wave=self.analyze_clip(c,lambda v,*_:progress(v*.5))
                stem=Path(c.info.path).stem+'_处理';suffix=exporter.suffix_for(options,c.media);path=Path(folder)/(stem+suffix);i=2
                while path.exists():path=Path(folder)/(stem+f'_{i}'+suffix);i+=1
                analysis=c.analysis if options.auto_mask else analysis_engine.manual_analysis(c.info)
                outputs.append(exporter.export(analysis,path,settings,options,manual,self.cancel,lambda v,*_:progress(.5+v*.5),c.media))
            return outputs
        def done(outputs):
            for i,c in enumerate(self.clips):self.bin.item(i).setText(self.clip_text(c))
            self.select_clip(self.current);self.last_output=outputs[-1];self.folder_button.setEnabled(True);self.status.setText(f'批量完成 · 已导出 {len(outputs)} 个视频到 {folder}')
        self.start_job('批量分析与导出',worker,done)
    def snapshot(self):
        c=self.clip
        if not c or self.busy:return
        path,_=QFileDialog.getSaveFileName(self,'保存原尺寸处理帧',str(Path(c.info.path).with_name(Path(c.info.path).stem+f'_帧{self.index+1}.png')),'PNG (*.png)')
        if not path:return
        if not Path(path).suffix:path+='.png'
        if Path(path).exists():self.message('请换一个新文件名，已有文件不会被覆盖。');return
        try:
            options=copy.deepcopy(c.options);known=c.analysis is not None and self.index<len(c.analysis.faces)
            if not known:options.auto_mask=False
            faces=c.analysis.faces[self.index] if known else []
            if options.profile=='native':
                rgb=exporter.native_export.preview(c.info,self.index,faces,c.settings,c.manual,options.auto_mask,max(c.info.width,c.info.height))
            else:
                raw=core.read_frame(c.info,self.index,(c.info.width,c.info.height),colors.decode_filter(c.media,options))
                rgb=exporter.mask_render(raw,faces,c.settings,c.manual,self.index,options)
            if not qimage(rgb).save(path):raise ValueError('PNG 保存失败。')
            self.status.setText(f'已保存原尺寸 8 位 PNG 预览帧 · {path}')
        except Exception as error:self.message(error)
    def extract_audio(self):
        c=self.clip
        if not c or not c.media['audio_codec'] or self.busy:return
        path,_=QFileDialog.getSaveFileName(self,'提取完整音轨',str(Path(c.info.path).with_suffix('.wav')),'WAV (*.wav)')
        if not path:return
        if not Path(path).suffix:path+='.wav'
        self.start_job('提取完整音轨',lambda:exporter.extract_audio(c.info.path,path,self.cancel),self.export_done)
    def project_data(self):
        return {'format':'face-privacy-studio','version':2,'clips':[{'path':c.info.path,'fingerprint':list(core.fingerprint(c.info.path)),'settings':asdict(c.settings),'options':asdict(c.options),'analysis_options':asdict(c.analysis_options),'manual':c.manual} for c in self.clips]}
    def save_project(self):
        if not self.clips:return
        path,_=QFileDialog.getSaveFileName(self,'保存项目',str(self.project_path or Path(self.clip.info.path).with_suffix('.privacy.json')),'工作台项目 (*.privacy.json)')
        if not path:return
        if not path.endswith('.json'):path+='.privacy.json'
        try:
            payload=json.dumps(self.project_data(),ensure_ascii=False,indent=2)
            temp=Path(path).with_name(Path(path).name+'.tmp');temp.write_text(payload,encoding='utf8');temp.replace(path)
            self.project_path=path;self.status.setText('项目已保存 · 重开先预览，点击打码可复用本机识别缓存')
        except Exception as error:self.message(error)
    def open_project(self):
        if self.busy:return
        path,_=QFileDialog.getOpenFileName(self,'打开项目','','工作台项目 (*.json)')
        if path:self.load_project(path)
    def load_project(self,path):
        if self.busy:return
        try:
            data=json.loads(Path(path).read_text(encoding='utf8'))
            if data.get('format')!='face-privacy-studio' or data.get('version') not in (1,2):raise ValueError('不是受支持的工作台项目。')
            pending=[]
            for item in data['clips']:
                source=Path(item['path'])
                if not source.is_file():raise ValueError(f'找不到原视频：{source}')
                if list(core.fingerprint(source))!=item['fingerprint']:raise ValueError(f'原视频已变化，请重新导入：{source.name}')
                c=Clip(core.probe(str(source)),exporter.details(source),core.Settings(**item['settings']),exporter.ExportOptions(**item['options']),item['manual'])
                c.analysis_options=analysis_engine.AnalysisOptions(**item.get('analysis_options',{}))
                c.analysis_options.revision=analysis_engine.AnalysisOptions().revision;analysis_engine.validate(c.analysis_options)
                # Validate project-controlled geometry without trusting JSON shapes.
                if c.settings.region not in ('full','eyes','upper','lower') or c.settings.style not in ('mosaic','blur','solid') or c.settings.missing not in ('keep','full_frame'):raise ValueError('项目遮挡参数无效。')
                if not 1<=c.settings.strength<=5 or not .75<=c.settings.coverage<=2 or not .4<=c.settings.eye_height<=3:raise ValueError('项目遮挡参数超出范围。')
                for mask in c.manual:
                    if not 0<=mask['start']<=mask['end']<c.info.frames:raise ValueError('项目手动框时间范围无效。')
                    for rect in [mask['rect']]+[key['rect'] for key in mask.get('keyframes',[])]:
                        if len(rect)!=4 or not all(0<=n<=1 for n in rect) or rect[0]>=rect[2] or rect[1]>=rect[3]:raise ValueError('项目手动框位置无效。')
                    for key in mask.get('keyframes',[]):
                        if not isinstance(key['frame'],int) or not 0<=key['frame']<c.info.frames:raise ValueError('项目关键帧无效。')
                # Invalid output settings remain reviewable; export validates them.
                if c.options.rotation not in (0,90,180,270) or c.options.profile not in exporter.PROFILES:raise ValueError('项目导出设置无效。')
                pending.append(c)
            self.player.stop();self.player.setSource(QUrl())
            for old in self.clips:
                if old.analysis:old.analysis.close()
            self.bin.blockSignals(True);self.bin.clear();self.clips=pending;self.current=-1
            for c in pending:self.bin.addItem(self.clip_text(c))
            self.bin.blockSignals(False);self.clip_count.setText(f'{len(pending)} 个');self.project_path=path
            self.bin.setCurrentRow(0 if pending else -1)
            self.status.setText('项目已打开 · 原片可预览，点击“开始打码”复用或继续识别缓存')
        except Exception as error:self.message(error)
    def open_output(self):
        if self.last_output:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_output).parent)))
    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and not self.busy:event.acceptProposedAction()
    def dropEvent(self,event):
        if not self.busy:self.import_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
    def closeEvent(self,event):
        if self.busy:self.cancel.set();self.status.setText('正在停止处理，完成后可关闭窗口。');event.ignore();return
        self.seek_timer.stop();self.player.pause();self.renderer.close()
        self.player.stop()
        self.player.setSource(QUrl())
        for c in self.clips:
            if c.analysis:
                try:c.analysis.close()
                except OSError:pass
        event.accept()
def main():
    if sys.stdout is None:sys.stdout=open(os.devnull,'w')
    if sys.stderr is None:sys.stderr=open(os.devnull,'w')
    app=QApplication(sys.argv);app.setApplicationName('视频一键打码工具');app.setStyle('Fusion');app.setStyleSheet(STYLE)
    window=Studio();screen=app.primaryScreen().availableGeometry();window.resize(min(1460,screen.width()-60),min(920,screen.height()-60));window.show()
    paths=[p for p in sys.argv[1:] if Path(p).is_file()]
    if '--verify-color' in sys.argv:
        from color_acceptance import verification_loop
        i=sys.argv.index('--verify-color');return verification_loop(app,window,Path(sys.argv[i+1]))
    if '--verify' in sys.argv:
        from acceptance import verification_loop
        i=sys.argv.index('--verify');return verification_loop(app,window,Path(sys.argv[i+1]),Path(sys.argv[i+2]))
    def start_session():
        if not QSettings('manba-pan','FacePrivacyStudio').value('hideWelcome',False,type=bool):
            ProjectDialog(window,welcome=True).exec()
        if paths:
            window.load_project(paths[0]) if paths[0].endswith('.privacy.json') else window.import_paths(paths)
    QTimer.singleShot(100,start_session)
    result=app.exec()
    # Destroy multimedia objects while QApplication and its GPU context live.
    from shiboken6 import delete
    delete(window);app.processEvents()
    return result

if __name__=='__main__':sys.exit(main())
