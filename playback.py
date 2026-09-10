"""One pending preview task and one original frame; no growing video queue."""
from __future__ import annotations
import threading
import numpy as np
from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtGui import QImage
import core,exporter
import color_management as colors


class Events(QObject):
    ready=Signal(object)
    failed=Signal(int,str)


class LatestRenderer:
    def __init__(self):
        self.events=Events();self.condition=threading.Condition();self.pending=None;self.stopped=False
        self.reset_source=False;self.cancel=threading.Event()
        self.native_reader=None;self.managed_key=self.managed_rgb=None
        self.thread=threading.Thread(target=self.run,daemon=True,name='video-preview');self.thread.start()

    def submit(self,task):
        with self.condition:self.pending=task;self.condition.notify()

    def discard(self,close_source=False):
        with self.condition:
            self.pending=None;self.reset_source|=close_source;self.condition.notify()

    def close(self):
        with self.condition:self.stopped=True;self.pending=None;self.cancel.set();self.condition.notify()
        self.thread.join(timeout=5)

    def close_source(self):
        # Decoder handles belong to the worker, including during shutdown.
        if self.native_reader:
            self.native_reader.close();self.native_reader=None
        self.managed_key=self.managed_rgb=None

    def run(self):
        try:
            while True:
                with self.condition:
                    while self.pending is None and not self.stopped and not self.reset_source:self.condition.wait()
                    if self.stopped:return
                    reset=self.reset_source;self.reset_source=False
                    task=self.pending;self.pending=None
                if reset:self.close_source()
                if task is None:continue
                try:self.render_task(task)
                except core.Cancelled:
                    if self.stopped:return
                except Exception as error:
                    self.close_source();self.events.failed.emit(task[0],str(error))
        finally:self.close_source()

    def render_task(self,task):
        epoch,index,source,info,analysis,settings,options,manual,compare,limit,media=task
        still=source is None
        known=analysis is not None and index<len(analysis.faces)
        faces=analysis.faces[index] if known else []
        native=not compare and exporter.native_preview_compatible(media,options)
        if native:
            if self.native_reader is None or self.native_reader.info.path!=info.path:
                self.close_source();self.native_reader=exporter.native_export.PreviewReader(info)
            output=self.native_reader.preview(index,faces,settings,manual,options.auto_mask and known,limit,self.cancel)
            # Cached RGB must never contain an existing mask. The reader keeps
            # the original native frame for rerendering with changed settings.
            rgb=None
        else:
            if self.native_reader:self.close_source()
            managed=not compare and (options.input_color!='auto' or options.input_range!='auto' or options.output_color!='preserve')
            if managed:
                size=core.fit_size(info.width,info.height,limit);color_filter=colors.decode_filter(media,options)
                key=(info.path,index,size,color_filter)
                if key!=self.managed_key:
                    self.managed_rgb=core.read_frame(info,index,size,color_filter);self.managed_key=key
                rgb=self.managed_rgb
            elif source is None:rgb=core.read_frame(info,index,core.fit_size(info.width,info.height,limit))
            elif isinstance(source,np.ndarray):rgb=source
            else:
                im=source if isinstance(source,QImage) else source.toImage()
                if im.isNull():return
                if max(im.width(),im.height())>limit:
                    im=im.scaled(limit,limit,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                im=im.convertToFormat(QImage.Format.Format_RGB888)
                rgb=np.frombuffer(im.constBits(),np.uint8,count=im.sizeInBytes()).reshape(im.height(),im.bytesPerLine())[:,:im.width()*3].reshape(im.height(),im.width(),3).copy()
            if compare:output=exporter.rotate(rgb,options.rotation)
            else:
                if not known:options.auto_mask=False
                output=exporter.mask_render(rgb,faces,settings,manual,index,options)
            # Do not feed color-converted RGB back to an unmanaged preview.
            if managed:rgb=None
        core.check_cancel(self.cancel)
        output=np.ascontiguousarray(output)
        image=QImage(output.data,output.shape[1],output.shape[0],output.strides[0],QImage.Format.Format_RGB888).copy()
        self.events.ready.emit({'epoch':epoch,'index':index,'raw':rgb,'image':image,'known':known,
                                'faces':len(faces),'still':still})
