"""One pending preview frame: slow rendering never queues an entire video."""
from __future__ import annotations
import threading
import cv2
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
        self.thread=threading.Thread(target=self.run,daemon=True,name='video-preview');self.thread.start()

    def submit(self,task):
        with self.condition:self.pending=task;self.condition.notify()

    def discard(self):
        with self.condition:self.pending=None

    def close(self):
        with self.condition:self.stopped=True;self.pending=None;self.condition.notify()
        self.thread.join(timeout=5)

    def run(self):
        while True:
            with self.condition:
                while self.pending is None and not self.stopped:self.condition.wait()
                if self.stopped:return
                task=self.pending;self.pending=None
            try:
                epoch,index,source,info,analysis,settings,options,manual,compare,limit,media=task
                managed=not compare and (options.input_color!='auto' or options.input_range!='auto' or options.output_color!='preserve')
                if managed:
                    source=core.read_frame(info,index,core.fit_size(info.width,info.height,limit),colors.decode_filter(media,options))
                if source is None:rgb=core.read_frame(info,index,core.fit_size(info.width,info.height,limit))
                elif isinstance(source,np.ndarray):rgb=source
                else:
                    im=source if isinstance(source,QImage) else source.toImage()
                    if im.isNull():continue
                    if max(im.width(),im.height())>limit:
                        im=im.scaled(limit,limit,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                    im=im.convertToFormat(QImage.Format.Format_RGB888)
                    rgb=np.frombuffer(im.constBits(),np.uint8,count=im.sizeInBytes()).reshape(im.height(),im.bytesPerLine())[:,:im.width()*3].reshape(im.height(),im.width(),3).copy()
                known=analysis is not None and index<len(analysis.faces)
                faces=analysis.faces[index] if known else []
                if compare:output=exporter.rotate(rgb,options.rotation)
                elif options.profile=='native' and exporter.native_export.supported(media['pixel_format']):
                    output=exporter.native_export.preview(info,index,faces,settings,manual,options.auto_mask and known,limit)
                else:
                    if not known:options.auto_mask=False
                    output=exporter.mask_render(rgb,faces,settings,manual,index,options)
                output=np.ascontiguousarray(output)
                image=QImage(output.data,output.shape[1],output.shape[0],output.strides[0],QImage.Format.Format_RGB888).copy()
                self.events.ready.emit({'epoch':epoch,'index':index,'raw':rgb,'image':image,'known':known,
                                        'faces':len(faces),'still':source is None})
            except Exception as error:self.events.failed.emit(task[0],str(error))
