"""Local video redaction. Detection, preview and export share the same geometry."""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, asdict, field
from fractions import Fraction
from typing import Callable

import cv2
import imageio_ffmpeg
import numpy as np

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
MODEL = ROOT / 'models' / 'face_landmarker.task'
FACE_MODEL = ROOT / 'models' / 'face_detection_yunet_2026may.onnx'
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
OVAL = [10,338,297,332,284,251,389,356,454,323,361,288,397,365,379,378,
        400,377,152,148,176,149,150,136,172,58,132,93,234,127,162,21,54,103,67,109]


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise Cancelled('已取消')


def ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def media_duration(path):
    """Read container duration, not just the video frame count."""
    result=subprocess.run([ffmpeg(),'-hide_banner','-i',str(path)],
        capture_output=True,creationflags=HIDDEN)
    match=re.search(r'^\s*Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',
                    result.stderr.decode('utf8','replace'),re.MULTILINE)
    if not match:
        raise RuntimeError('无法验证成片总时长，未保存输出。')
    h,m,s=map(float,match.groups())
    return h*3600+m*60+s


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    fps_text: str
    frames: int
    duration: float


@dataclass
class Settings:
    region: str = 'full'
    style: str = 'mosaic'
    strength: int = 4
    coverage: float = 1.15
    eye_height: float = 1.0
    missing: str = 'keep'


def probe(path: str) -> VideoInfo:
    cap = cv2.VideoCapture(path)
    try:
        ok, first = cap.read()
        if not ok:
            raise ValueError('无法读取视频。请尝试 MP4、MOV 或 MKV 文件。')
        h, w = first.shape[:2]
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    if not math.isfinite(fps) or not 0.5 <= fps <= 240 or frames < 1:
        raise ValueError('无法取得有效的视频时长或帧率。请先转成普通 MP4。')
    rate = Fraction(fps).limit_denominator(1001000)
    return VideoInfo(str(Path(path).resolve()), w, h, fps, str(rate), frames, frames/fps)


def fit_size(w, h, limit=960):
    scale = min(1.0, limit/max(w,h))
    return max(2, round(w*scale/2)*2), max(2, round(h*scale/2)*2)


class Decoder:
    """FFmpeg normalizes VFR to a CFR timeline shared by analysis and export."""
    def __init__(self, info, size=None, hardware='cpu', start_frame=0, cancel=None):
        self.info = info
        self.w, self.h = size or (info.width, info.height)
        self.errors = tempfile.TemporaryFile()
        from acceleration import decoder_args
        acceleration,self.backend=decoder_args(ffmpeg(),info.path,hardware)
        filters=f'fps={info.fps_text}'
        if start_frame:filters+=f',trim=start_frame={start_frame},setpts=PTS-STARTPTS'
        filters+=f',scale={self.w}:{self.h},setsar=1'
        self.proc = subprocess.Popen([
            ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            *acceleration,'-i', info.path, '-map', '0:v:0', '-an', '-sn', '-dn',
            '-vf', filters,
            '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=self.errors, creationflags=HIDDEN)
        self.cancel=cancel;self.watch_stop=threading.Event();self.watcher=None
        if cancel is not None:
            def watch():
                while not self.watch_stop.wait(.1):
                    if cancel.is_set():
                        try:self.proc.kill()
                        except OSError:pass
                        return
            self.watcher=threading.Thread(target=watch,daemon=True);self.watcher.start()

    def __iter__(self):
        n = self.w*self.h*3
        while True:
            data = self.proc.stdout.read(n)
            check_cancel(self.cancel)
            if not data:
                rc = self.proc.wait()
                if rc:
                    self.errors.seek(0)
                    raise RuntimeError('视频解码失败：'+self.errors.read().decode('utf8', 'replace')[-1200:])
                return
            if len(data) != n:
                raise RuntimeError('视频帧不完整，未导出成片。')
            yield np.frombuffer(data, np.uint8).reshape(self.h,self.w,3).copy()

    def close(self):
        self.watch_stop.set()
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.errors.close()
        if self.watcher:self.watcher.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def read_frame(info, index, size=None, color_filter=None):
    w, h = size or fit_size(info.width,info.height,1100)
    # Seek on the normalized timeline: keep the fps filter before frame selection.
    # Accurate input seeking avoids decoding an entire long interview per click.
    result = subprocess.run([
        ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-ss', f'{max(0,index)/info.fps:.9f}', '-i', info.path,
        '-map', '0:v:0', '-frames:v', '1', '-an', '-sn',
        '-vf', f'scale={w}:{h},setsar=1'+(','+color_filter if color_filter else ''), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=HIDDEN)
    if result.returncode or len(result.stdout) != w*h*3:
        raise RuntimeError('无法预览这个位置的视频画面。')
    return np.frombuffer(result.stdout,np.uint8).reshape(h,w,3).copy()


class Detector:
    def __init__(self):
        import mediapipe as mp
        self.mp = mp
        # The landmark task's built-in detector is designed for close-up faces.
        # Detect at two image scales first, then run landmarks on each face crop.
        # Load both models from bytes so Chinese installation paths work too.
        self.locator = cv2.FaceDetectorYN.create(
            'onnx',np.frombuffer(FACE_MODEL.read_bytes(),np.uint8),np.empty(0,np.uint8),
            (320,320),0.65,0.3,5000)
        self.engine = mp.tasks.vision.FaceLandmarker.create_from_options(
            mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_buffer=MODEL.read_bytes()),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_faces=1,
                min_face_detection_confidence=0.2,
                min_face_presence_confidence=0.3,
                min_tracking_confidence=0.4))
        self.last_review_reason=None

    @staticmethod
    def overlap(a,b):
        ax,ay,aw,ah=a[:4]
        bx,by,bw,bh=b[:4]
        intersection=max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
        return intersection/max(1.,aw*ah+bw*bh-intersection)

    def locate(self,rgb):
        h,w=rgb.shape[:2]
        bgr=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
        candidates=[]
        sizes=list(dict.fromkeys([fit_size(w,h,960),fit_size(w,h,480)]))
        for dw,dh in sizes:
            view=cv2.resize(bgr,(dw,dh)) if (dw,dh)!=(w,h) else bgr
            self.locator.setInputSize((dw,dh))
            _,rows=self.locator.detect(view)
            for row in rows if rows is not None else []:
                row=row.copy()
                row[:14:2]*=w/dw
                row[1:14:2]*=h/dh
                if min(row[2:4])>=8:
                    candidates.append(row)
        selected=[]
        for row in sorted(candidates,key=lambda r:float(r[-1]),reverse=True):
            if not any(self.overlap(row,other)>.3 for other in selected):
                selected.append(row)
        return selected

    @staticmethod
    def fallback_geometry(row,w,h):
        # Coarse geometry stays available even if the landmark stage fails.
        # A failed landmark stage must never discard a detected face.
        x,y,bw,bh=row[:4]
        theta=np.linspace(-np.pi/2,3*np.pi/2,36,endpoint=False)
        contour=np.column_stack([x+bw/2+np.cos(theta)*bw*.60,
                                 y+bh/2+np.sin(theta)*bh*.60])
        eye1,eye2=row[4:6],row[6:8]
        points=np.vstack([contour,eye1,eye1,eye2,eye2])
        return (points/[w,h]).astype(np.float32)

    def detect(self, rgb, timestamp_ms):
        h,w=rgb.shape[:2]
        rows=self.locate(rgb)
        faces = []
        self.last_review_reason='画面人脸超过处理上限' if len(rows)>6 else None
        for row in rows[:6]:
            x,y,bw,bh=row[:4]
            side=max(32,int(math.ceil(max(bw,bh)*1.8)))
            x0,y0=round(x+bw/2-side/2),round(y+bh/2-side/2)
            x1,y1=x0+side,y0+side
            if x1<=0 or y1<=0 or x0>=w or y0>=h:
                continue
            crop=rgb[max(0,y0):min(h,y1),max(0,x0):min(w,x1)]
            crop=cv2.copyMakeBorder(crop,max(0,-y0),max(0,y1-h),max(0,-x0),max(0,x1-w),cv2.BORDER_REPLICATE)
            crop=cv2.resize(crop,(320,320))
            result=self.engine.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                                                     data=np.ascontiguousarray(crop)))
            arr=None
            if result.face_landmarks:
                pts=result.face_landmarks[0]
                arr=np.array([[(pts[i].x*side+x0)/w,(pts[i].y*side+y0)/h]
                              for i in OVAL+[33,133,362,263]],dtype=np.float32)
                bounds=arr[:36]*[w,h]
                lo,hi=bounds.min(axis=0),bounds.max(axis=0)
                if self.overlap([*lo,*(hi-lo)],row)<.25:
                    arr=None
            if arr is None:
                arr=self.fallback_geometry(row,w,h)
                self.last_review_reason='人脸已遮挡，五官定位不稳，请回看'
            faces.append(arr)
        return faces

    def close(self):
        self.engine.close()


@dataclass
class Analysis:
    info: VideoInfo
    faces: list
    review: list
    fingerprint: tuple
    preview_path: str = ''
    completed: bool = True
    stats: dict = field(default_factory=dict)

    def close(self):
        if hasattr(self.faces,'close'):self.faces.close()
        if self.preview_path:
            Path(self.preview_path).unlink(missing_ok=True)


def read_preview(analysis, index):
    if not analysis.preview_path:return read_frame(analysis.info,index)
    cap=cv2.VideoCapture(analysis.preview_path)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES,index)
        ok,bgr=cap.read()
        if not ok:
            raise RuntimeError('预览缓存读取失败，请重新分析。')
        return cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def fingerprint(path):
    stat = Path(path).stat()
    return (stat.st_size, stat.st_mtime_ns)


def analyze(info: VideoInfo, cancel=None, progress: Callable|None=None):
    before = fingerprint(info.path)
    all_faces = []
    review = []
    detector = Detector()
    prev_count = 0
    try:
        size=fit_size(info.width,info.height)
        fd,preview_path=tempfile.mkstemp(prefix='face-privacy-preview-',suffix='.mp4')
        os.close(fd)
        writer=cv2.VideoWriter(preview_path,cv2.VideoWriter_fourcc(*'mp4v'),info.fps,size)
        if not writer.isOpened():
            raise RuntimeError('无法创建预览缓存，请检查临时目录空间。')
        with Decoder(info,size) as decoder:
            for i, rgb in enumerate(decoder):
                check_cancel(cancel)
                faces = detector.detect(rgb,round(i*1000/info.fps))
                writer.write(cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
                all_faces.append(faces)
                if detector.last_review_reason:
                    reason=detector.last_review_reason
                elif not faces:
                    reason = '未检测到人脸'
                elif len(faces) < prev_count:
                    reason = '检测到的人脸数量减少'
                else:
                    reason = None
                if reason:
                    if review and review[-1]['reason']==reason and review[-1]['end']==i-1:
                        review[-1]['end'] = i
                    else:
                        review.append({'start':i,'end':i,'reason':reason})
                prev_count = len(faces)
                if progress and (i%5==0):
                    progress(min(.99,(i+1)/max(info.frames,1)),i,len(faces))
    finally:
        detector.close()
        if 'writer' in locals():
            writer.release()
        if sys.exc_info()[0] and 'preview_path' in locals():
            Path(preview_path).unlink(missing_ok=True)
    if not all_faces:
        Path(preview_path).unlink(missing_ok=True)
        raise ValueError('视频里没有可读取的画面。')
    if before != fingerprint(info.path):
        Path(preview_path).unlink(missing_ok=True)
        raise ValueError('分析期间源视频发生变化，请重新导入。')
    info.frames = len(all_faces)
    info.duration = info.frames/info.fps
    return Analysis(info,all_faces,review,before,preview_path)


def clip_half(poly, cut, keep_top):
    out = []
    for a,b in zip(poly,np.roll(poly,-1,axis=0)):
        ai = a[1]<=cut if keep_top else a[1]>=cut
        bi = b[1]<=cut if keep_top else b[1]>=cut
        if ai:
            out.append(a)
        if ai != bi:
            t = (cut-a[1])/(b[1]-a[1])
            out.append(a+t*(b-a))
    return np.array(out,dtype=np.float32)


def face_polygon(face, w, h, settings):
    xy = np.asarray(face,dtype=np.float32)*np.array([w,h],np.float32)
    contour = xy[:36]
    eye1, eye2 = xy[36:38].mean(axis=0),xy[38:40].mean(axis=0)
    if eye1[0] > eye2[0]:
        eye1,eye2 = eye2,eye1
    direction = eye2-eye1
    length = max(float(np.linalg.norm(direction)),1.)
    u = direction/length
    v = np.array([-u[1],u[0]])
    basis = np.array([u,v])
    center = (eye1+eye2)/2
    local = (contour-center)@basis.T
    low, high = local.min(axis=0),local.max(axis=0)
    middle = (low+high)/2
    local = (local-middle)*settings.coverage+middle
    if settings.region == 'eyes':
        half_w = max(length*.9,(high[0]-low[0])*.51)*settings.coverage
        half_h = max(length*.24,(high[1]-low[1])*.095)*settings.eye_height*settings.coverage
        local = np.array([[-half_w,-half_h],[half_w,-half_h],[half_w,half_h],[-half_w,half_h]])
    elif settings.region in ('upper','lower'):
        local = clip_half(local,middle[1],settings.region=='upper')
    return local@basis+center


def effect_polygon(rgb, polygon, settings):
    h,w = rgb.shape[:2]
    pts = np.round(polygon).astype(np.int32)
    if len(pts)<3:
        return
    x0,y0 = np.maximum(pts.min(axis=0),0)
    x1,y1 = np.minimum(pts.max(axis=0)+1,[w,h])
    if x1<=x0 or y1<=y0:
        return
    roi = rgb[y0:y1,x0:x1]
    mask = np.zeros(roi.shape[:2],np.uint8)
    cv2.fillPoly(mask,[pts-[x0,y0]],255)
    strength = max(1,min(5,int(settings.strength)))
    short = max(1,min(roi.shape[:2]))
    if settings.style == 'solid':
        filtered = np.full_like(roi,16*(257 if rgb.dtype==np.uint16 else 1))
    elif settings.style == 'blur':
        sigma = max(2.,short*[.045,.075,.12,.19,.30][strength-1])
        # Downsample large ROIs for predictable cost and consistent strong blur.
        factor = min(1.,200/max(roi.shape[:2]))
        small = cv2.resize(roi,(max(1,round(roi.shape[1]*factor)),max(1,round(roi.shape[0]*factor))))
        small = cv2.GaussianBlur(small,(0,0),sigma*factor)
        filtered = cv2.resize(small,(roi.shape[1],roi.shape[0]),interpolation=cv2.INTER_LINEAR)
    else:
        block = max(3,round(short*[.045,.08,.13,.21,.34][strength-1]))
        small = cv2.resize(roi,(max(1,roi.shape[1]//block),max(1,roi.shape[0]//block)),interpolation=cv2.INTER_AREA)
        filtered = cv2.resize(small,(roi.shape[1],roi.shape[0]),interpolation=cv2.INTER_NEAREST)
    roi[mask!=0] = filtered[mask!=0]


def render_frame(rgb, faces, settings, manual=None, index=0):
    output = rgb.copy()
    h,w = output.shape[:2]
    if not faces and settings.missing=='full_frame':
        # Deterministic opaque fallback; a large, weak mosaic can expose detail.
        output[:]=16*(257 if rgb.dtype==np.uint16 else 1)
    else:
        for face in faces:
            effect_polygon(output,face_polygon(face,w,h,settings),settings)
    for box in manual or []:
        if box['start']<=index<=box['end']:
            x0,y0,x1,y1 = np.array(manual_rect(box,index))*[w,h,w,h]
            effect_polygon(output,np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]]),settings)
    return output


def manual_rect(box,index):
    """Interpolate user-added keyframes; legacy fixed rectangles still work."""
    keys=sorted(box.get('keyframes',[]),key=lambda key:key['frame'])
    if not keys:
        return box['rect']
    if index<=keys[0]['frame']:
        return keys[0]['rect']
    for left,right in zip(keys,keys[1:]):
        if index<=right['frame']:
            t=(index-left['frame'])/max(1,right['frame']-left['frame'])
            return (np.asarray(left['rect'])*(1-t)+np.asarray(right['rect'])*t).tolist()
    return keys[-1]['rect']


def export_video(analysis, destination, settings, manual=None, cancel=None, progress=None):
    info = analysis.info
    source, dest = Path(info.path).resolve(),Path(destination).resolve()
    if source==dest or (dest.exists() and os.path.samefile(source,dest)):
        raise ValueError('请另存为新文件，不能覆盖源视频。')
    if dest.exists():
        raise ValueError('目标文件已存在，请换一个文件名。')
    if analysis.fingerprint != fingerprint(source):
        raise ValueError('源视频已变化，请重新分析。')
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_name = tempfile.mkstemp(prefix='.redaction-',suffix='.mp4',dir=dest.parent)
    os.close(fd)
    temp = Path(temp_name)
    errors = tempfile.TemporaryFile()
    encoder = None
    success = False
    count=0
    duration_text=f'{len(analysis.faces)/info.fps:.9f}'
    try:
        encoder = subprocess.Popen([
            ffmpeg(),'-hide_banner','-loglevel','error','-nostdin','-y',
            '-f','rawvideo','-pix_fmt','rgb24','-s',f'{info.width}x{info.height}',
            '-r',info.fps_text,'-i','pipe:0','-i',info.path,
            '-map','0:v:0','-map','1:a:0?',
            '-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1',
            '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
            # Bound both the padding filter and muxer explicitly. Infinite apad
            # with a piped video input can generate hours of trailing silence
            # before the muxer observes EOF, despite -shortest.
            '-c:a','aac','-b:a','192k','-af',
            f'apad=whole_dur={duration_text},atrim=duration={duration_text}',
            '-t',duration_text,
            '-map_metadata','-1','-movflags','+faststart',str(temp)],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors,creationflags=HIDDEN)
        with Decoder(info) as decoder:
            for i,rgb in enumerate(decoder):
                check_cancel(cancel)
                if i>=len(analysis.faces):
                    raise RuntimeError('视频帧数与分析结果不一致，请重新分析。')
                output = render_frame(rgb,analysis.faces[i],settings,manual,i)
                try:
                    encoder.stdin.write(output.tobytes())
                except BrokenPipeError:
                    errors.seek(0)
                    raise RuntimeError('视频编码失败：'+errors.read().decode('utf8','replace')[-1200:])
                count+=1
                if progress and i%5==0:
                    progress((i+1)/len(analysis.faces),i,0)
        if count != len(analysis.faces):
            raise RuntimeError('源视频没有完整解码，已停止导出。')
        encoder.stdin.close()
        while encoder.poll() is None:
            check_cancel(cancel)
            try:
                encoder.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                pass
        if encoder.returncode:
            errors.seek(0)
            raise RuntimeError('导出失败：'+errors.read().decode('utf8','replace')[-1200:])
        check_cancel(cancel)
        actual_duration=media_duration(temp)
        if abs(actual_duration-len(analysis.faces)/info.fps)>max(.12,2/info.fps):
            raise RuntimeError('成片音视频总时长与源画面不一致，已停止保存。')
        if analysis.fingerprint != fingerprint(source):
            raise ValueError('导出期间源视频发生变化，已停止保存。')
        if dest.exists():
            raise ValueError('目标文件已被其他程序创建，请换一个文件名。')
        # Windows rename refuses to replace an existing destination.
        temp.rename(dest)
        success = True
        return str(dest)
    finally:
        if encoder:
            if encoder.poll() is None:
                encoder.terminate()
                try:
                    encoder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    encoder.kill()
                    encoder.wait()
            if encoder.stdin and not encoder.stdin.closed:
                try:
                    encoder.stdin.close()
                except OSError:
                    pass
        errors.close()
        if not success:
            temp.unlink(missing_ok=True)


def timecode(seconds):
    seconds=max(0,float(seconds))
    m,s=divmod(seconds,60)
    h,m=divmod(int(m),60)
    return f'{h:02d}:{m:02d}:{s:05.2f}'


def parse_time(text):
    parts = str(text).strip().split(':')
    if not 1<=len(parts)<=3:
        raise ValueError('时间请填写秒数，或 00:01:23.50。')
    value = 0.
    for part in parts:
        num=float(part)
        if not math.isfinite(num) or num<0:
            raise ValueError('时间必须是非负数。')
        value=value*60+num
    return value
