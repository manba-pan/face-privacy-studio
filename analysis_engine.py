"""Selectable detectors, short optical-flow compensation and chunked analysis."""
from __future__ import annotations
from dataclasses import dataclass
import math
import os
import time
import zipfile
import cv2
import numpy as np
import core
from acceleration import YuNet,nms
from analysis_cache import FaceStore,cache_path,CHUNK

PROFILES={
    'fast':('快速','轻量找脸 + 短时跟踪；适合先粗看，可能增加漏检。'),
    'balanced':('均衡','逐帧多尺度找脸 + 五官定位，失去检测时停止延续遮挡。'),
    'precise':('细致','多尺度复检；额外候选须通过五官验证，遮挡贴合面部。')}


@dataclass
class AnalysisOptions:
    profile:str='balanced'
    device:str='auto'
    decode:str='auto'
    tracking:bool=True
    revision:int=10


def validate(options):
    if options.profile not in PROFILES:raise ValueError('未知识别模式')
    if options.device not in ('cpu','auto') and not (options.device.startswith('gpu:') and options.device[4:].isdigit() and int(options.device[4:])<16):raise ValueError('识别设备无效')
    if options.decode not in ('cpu','auto'):raise ValueError('解码设备无效')


class Detector(core.Detector):
    def __init__(self,options):
        validate(options);self.options=options;self.engine=None;self.secondary=None;self.verifier=None
        self.yunet=YuNet(core.FACE_MODEL,options.device,.65)
        self.last_review_reason=None
        from vision_runtime import mediapipe
        mp=mediapipe();self.mp=mp
        with zipfile.ZipFile(core.MODEL) as task:model=task.read('face_detector.tflite')
        self.verifier=mp.tasks.vision.FaceDetector.create_from_options(mp.tasks.vision.FaceDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=model),min_detection_confidence=.55))
        if options.profile!='fast':
            self.engine=mp.tasks.vision.FaceLandmarker.create_from_options(mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_buffer=core.MODEL.read_bytes()),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,num_faces=1,
                min_face_detection_confidence=.2,min_face_presence_confidence=.3))
            if options.profile=='precise':
                self.secondary=mp.tasks.vision.FaceDetector.create_from_options(mp.tasks.vision.FaceDetectorOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_buffer=model),min_detection_confidence=.65))

    def locate(self,rgb):
        h,w=rgb.shape[:2];bgr=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR);rows=[];primary=[]
        limits={'fast':[640],'balanced':[960,480],'precise':[1280,640,320]}[self.options.profile]
        sizes=list(dict.fromkeys(core.fit_size(w,h,n) for n in limits))
        for scale_index,(dw,dh) in enumerate(sizes):
            view=cv2.resize(bgr,(dw,dh),interpolation=cv2.INTER_AREA) if (dw,dh)!=(w,h) else bgr
            for row in self.yunet.detect(view):
                row=row.copy();row[:14:2]*=w/dw;row[1:14:2]*=h/dh
                if min(row[2:4])>=8:
                    rows.append(row)
                    if self.options.profile!='precise' or scale_index<2:primary.append(row)
        if self.options.profile=='precise':
            dw,dh=core.fit_size(w,h,640);mirror=cv2.flip(cv2.resize(bgr,(dw,dh)),1)
            for row in self.yunet.detect(mirror):
                row=row.copy();row[0]=dw-row[0]-row[2];row[4:14:2]=dw-row[4:14:2]
                # Mirroring exchanges anatomical left/right landmark labels.
                row[4:8]=row[[6,7,4,5]];row[10:14]=row[[12,13,10,11]]
                row[:14:2]*=w/dw;row[1:14:2]*=h/dh
                if min(row[2:4])>=8:rows.append(row)
            view=cv2.resize(rgb,(dw,dh));detected=self.secondary.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=np.ascontiguousarray(view)))
            for face in detected.detections:
                box=face.bounding_box;points=face.keypoints
                if len(points)<4:continue
                row=np.zeros(15,np.float32)
                row[:4]=[box.origin_x*w/dw,box.origin_y*h/dh,box.width*w/dw,box.height*h/dh]
                row[4:10]=[points[0].x*w,points[0].y*h,points[1].x*w,points[1].y*h,points[2].x*w,points[2].y*h]
                row[10:14]=[points[3].x*w-row[2]*.12,points[3].y*h,points[3].x*w+row[2]*.12,points[3].y*h]
                row[14]=face.categories[0].score
                if min(row[2:4])>=8:rows.append(row)
        self.primary_rows=primary;self.last_rows=nms(rows,.35)
        return self.last_rows

    @staticmethod
    def fallback_geometry(row,w,h):
        # Coverage is applied once by the shared renderer. Do not add the old
        # implicit 20% enlargement on top of the user's coverage setting.
        x,y,bw,bh=row[:4];theta=np.linspace(-np.pi/2,3*np.pi/2,36,endpoint=False)
        contour=np.column_stack((x+bw/2+np.cos(theta)*bw*.5,y+bh/2+np.sin(theta)*bh*.5))
        return (np.vstack((contour,row[4:6],row[4:6],row[6:8],row[6:8]))/[w,h]).astype(np.float32)

    @staticmethod
    def crop(rgb,row):
        h,w=rgb.shape[:2];x,y,bw,bh=row[:4]
        side=max(32,int(math.ceil(max(bw,bh)*1.8)))
        x0,y0=round(x+bw/2-side/2),round(y+bh/2-side/2);x1,y1=x0+side,y0+side
        if x1<=0 or y1<=0 or x0>=w or y0>=h:return None
        crop=rgb[max(0,y0):min(h,y1),max(0,x0):min(w,x1)]
        crop=cv2.copyMakeBorder(crop,max(0,-y0),max(0,y1-h),max(0,-x0),max(0,x1-w),cv2.BORDER_REPLICATE)
        crop=cv2.resize(crop,(320,320))
        return np.ascontiguousarray(crop),x0,y0,side

    def landmarks(self,rgb,row):
        h,w=rgb.shape[:2];view=self.crop(rgb,row)
        if view is None:return None
        crop,x0,y0,side=view
        result=self.engine.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=np.ascontiguousarray(crop)))
        if not result.face_landmarks:return None
        pts=result.face_landmarks[0]
        face=np.array([[(pts[i].x*side+x0)/w,(pts[i].y*side+y0)/h] for i in core.OVAL+[33,133,362,263]],np.float32)
        lo=(face[:36]*[w,h]).min(axis=0);hi=(face[:36]*[w,h]).max(axis=0)
        if not np.isfinite(face).all() or core.Detector.overlap([*lo,*(hi-lo)],row)<.35:return None
        # A crop may contain a different nearby face. Never transfer its mesh
        # to a spurious detector box on a hand or shoulder.
        center=(lo+hi)/2
        if np.any(np.abs(center-(row[:2]+row[2:4]/2))>row[2:4]*.4):return None
        if np.any((hi-lo)>row[2:4]*1.5):return None
        return face

    @staticmethod
    def plausible_points(row):
        points=row[4:14].reshape(5,2);x,y,w,h=row[:4]
        if not np.isfinite(row).all() or w<=0 or h<=0:return False
        eye_level=(points[:2,1].mean()-y)/h
        mouth_level=(points[3:,1].mean()-y)/h
        separation=np.linalg.norm(points[0]-points[1])/w
        return (.12<=eye_level<=.60 and .43<=mouth_level<=1.03 and
                .15<=mouth_level-eye_level<=.65 and .08<=separation<=.85 and
                np.all(points>=[x-w*.2,y-h*.2]) and np.all(points<=[x+w*1.2,y+h*1.2]))

    def confirm_fallback(self,rgb,row):
        agreement=sum(core.Detector.overlap(row,p)>.5 for p in self.primary_rows)
        plausible=self.plausible_points(row);h,w=rgb.shape[:2];x,y,bw,bh=row[:4]
        clipped=x<0 or y<0 or x+bw>w or y+bh>h
        near_edge=min(x/w,y/h,(w-x-bw)/w,(h-y-bh)/h)<.015
        visible_eyes=np.all((row[[5,7]]-y)/bh>=.18)
        if plausible and (row[-1]>=.90 or ((clipped or (near_edge and visible_eyes)) and row[-1]>=.80 and agreement>=2)):return True
        threshold=.55 if plausible and (near_edge or (row[-1]>=.80 and agreement>=2)) else .65
        view=self.crop(rgb,row)
        if view is None:return False
        crop,x0,y0,side=view
        result=self.verifier.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=crop))
        for hit in result.detections:
            b=hit.bounding_box;box=[b.origin_x*side/320+x0,b.origin_y*side/320+y0,b.width*side/320,b.height*side/320]
            if hit.categories[0].score>=threshold and core.Detector.overlap(box,row)>=.45:return True
        return False

    def detect(self,rgb,timestamp_ms):
        h,w=rgb.shape[:2];rows=self.locate(rgb);faces=[];self.last_review_reason=None
        for row in rows:
            face=self.landmarks(rgb,row) if self.options.profile!='fast' else None
            primary=any(core.Detector.overlap(row,other)>.35 for other in self.primary_rows)
            if face is None:
                if not primary or not self.confirm_fallback(rgb,row):
                    self.last_review_reason='疑似人脸未通过复核，请人工检查'
                    continue
                face=self.fallback_geometry(row,w,h)
                if self.options.profile!='fast':self.last_review_reason='已按检测框遮挡，五官定位不稳，请回看'
            # A valid face mesh takes precedence over an oversized coarse box.
            # The renderer's single coverage control supplies the outer margin.
            if not any(core.Detector.overlap(np.asarray(bounds(face))*[w,h,w,h],
                    np.asarray(bounds(other))*[w,h,w,h])>.5 for other in faces):faces.append(face)
        if len(faces)>6:self.last_review_reason='画面人脸超过处理上限'
        faces=faces[:6]
        return faces

    def close(self):
        if self.verifier:self.verifier.close()
        if self.secondary:self.secondary.close()
        if self.engine:self.engine.close()
        self.yunet.sessions.clear()


def bounds(face):
    lo=np.min(face[:36],axis=0);hi=np.max(face[:36],axis=0)
    return [*lo,*(hi-lo)]


def overlaps(a,b):
    # core.Detector.overlap uses pixel boxes; these geometries are normalized.
    aa=np.asarray(bounds(a))*1000;bb=np.asarray(bounds(b))*1000
    return core.Detector.overlap(aa,bb)>.2


class Tracker:
    """Forward/backward optical flow with scene-cut and motion rejection."""
    def __init__(self):self.gray=None;self.faces=[];self.ages=[];self.cut=False

    def predict(self,rgb):
        gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY);h,w=gray.shape
        self.cut=False;predictions=[]
        if self.gray is not None and self.gray.shape==gray.shape:
            before=cv2.resize(self.gray,(64,36));after=cv2.resize(gray,(64,36))
            self.cut=float(np.mean(cv2.absdiff(before,after)))>18
            if not self.cut:
                for face,age in zip(self.faces,self.ages):
                    mask=np.zeros_like(gray);outline=face[:36]*[w,h]
                    outline=outline.mean(axis=0)+(outline-outline.mean(axis=0))*.8
                    cv2.fillConvexPoly(mask,np.round(outline).astype(np.int32),255)
                    points=cv2.goodFeaturesToTrack(self.gray,60,.015,5,mask=mask)
                    if points is None or len(points)<7:continue
                    moved,ok,_=cv2.calcOpticalFlowPyrLK(self.gray,gray,points,None,winSize=(21,21),maxLevel=3)
                    if moved is None:continue
                    back,back_ok,_=cv2.calcOpticalFlowPyrLK(gray,self.gray,moved,None,winSize=(21,21),maxLevel=3)
                    if back is None:continue
                    valid=(ok.ravel()>0)&(back_ok.ravel()>0)&(np.linalg.norm(points-back,axis=2).ravel()<1.6)
                    if np.count_nonzero(valid)<7:continue
                    matrix,inliers=cv2.estimateAffinePartial2D(points[valid],moved[valid],method=cv2.RANSAC,ransacReprojThreshold=2.5)
                    if matrix is None or np.count_nonzero(inliers)<6:continue
                    scale=float(np.hypot(matrix[0,0],matrix[1,0]))
                    if not .8<scale<1.25 or np.linalg.norm(matrix[:,2])>max(w,h)*.16:continue
                    transformed=(face*[w,h])@matrix[:,:2].T+matrix[:,2]
                    normalized=(transformed/[w,h]).astype(np.float32)
                    center=normalized.mean(axis=0)
                    if np.all(center>-.05) and np.all(center<1.05):predictions.append((normalized,age+1))
        return gray,predictions

    def update(self,gray,detected,predictions,max_age,full_detection):
        faces=list(detected);ages=[0]*len(faces);compensated=False
        # Tracking is only for intermediate fast-preview analysis frames.
        # A full detector pass is authoritative; never append stale people to it
        # or carry a lost face onto the next shot's hands, neck or background.
        allowance=0 if full_detection else 6-len(faces)
        for face,age in predictions:
            if allowance<=0:break
            if age>max_age:continue
            if any(overlaps(face,other) for other in faces):continue
            faces.append(face);ages.append(age);compensated|=full_detection;allowance-=1
        self.gray=gray;self.faces=faces[:6];self.ages=ages[:6]
        return self.faces,compensated


def open_analysis(info,path):
    store=FaceStore(path)
    return core.Analysis(info,store,store.review(),core.fingerprint(info.path),
                         completed=store.complete,stats=dict(store.state.get('stats',{}),face_frames=store.state.get('face_frames',0)))


class EmptyFaces:
    def __init__(self,count):self.count=count
    def __len__(self):return self.count
    def __getitem__(self,index):
        if not 0<=index<self.count:raise IndexError(index)
        return []


def manual_analysis(info):
    return core.Analysis(info,EmptyFaces(info.frames),[],core.fingerprint(info.path))


def validate_timeline(info,count):
    if abs(count/info.fps-info.duration)>max(.5,3/info.fps):
        raise ValueError('识别帧数与视频时长不符，请清除当前缓存后重试；未标记为识别完成。')


def analyze(info,options,cancel=None,progress=None,partial=None):
    validate(options);path=cache_path(info,options);store=FaceStore(path,write=True)
    cv2.setNumThreads(min(4,os.cpu_count() or 1))
    detector=None;started=time.perf_counter();fingerprint=core.fingerprint(info.path)
    try:
        store.discard_partial_tail()
        if store.complete:
            validate_timeline(info,len(store))
            if partial:partial(str(path))
            return {'path':str(path),'frames':len(store),'cache_hit':True,'stats':store.state.get('stats',{})}
        resume=len(store);size=core.fit_size(info.width,info.height,{'fast':640,'balanced':960,'precise':1280}[options.profile])
        stats={'profile':options.profile,'resumed_frames':resume,'detect_seconds':0.,'decode_seconds':0.,'write_seconds':0.,'tracked_frames':0,'decoded_frames':0}
        detector=Detector(options);tracker=Tracker();chunk=[];reasons=[];previous=0
        if partial:partial(str(path))
        with core.Decoder(info,size,hardware=options.decode,start_frame=resume,cancel=cancel) as decoder:
            stats['decoder']=decoder.backend;iterator=iter(decoder);index=resume
            while True:
                core.check_cancel(cancel);begin=time.perf_counter()
                try:rgb=next(iterator)
                except StopIteration:break
                stats['decode_seconds']+=time.perf_counter()-begin;begin=time.perf_counter()
                stride=max(1,min(3,round(info.fps*.08))) if options.profile=='fast' and options.tracking else 1
                gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
                cut=tracker.gray is not None and float(np.mean(cv2.absdiff(cv2.resize(tracker.gray,(64,36)),cv2.resize(gray,(64,36)))))>18
                full=(index-resume)%stride==0 or not tracker.faces or cut
                found=detector.detect(rgb,round(index*1000/info.fps)) if full else []
                missing=len(found)<len(tracker.faces)
                if options.tracking and not cut and not full:gray,predictions=tracker.predict(rgb)
                else:predictions=[]
                gap=round(info.fps*({'fast':.12,'balanced':.18,'precise':.30}[options.profile]))
                faces,compensated=tracker.update(gray,found,predictions,max(1,gap),full)
                reason=detector.last_review_reason if full else None
                if not full and faces:stats['tracked_frames']+=1
                if not faces:reason=reason or '未检测到人脸'
                elif len(faces)<previous:reason=reason or '检测到的人脸数量减少'
                previous=len(faces);chunk.append(faces);reasons.append(reason)
                stats['detect_seconds']+=time.perf_counter()-begin;stats['decoded_frames']+=1;index+=1
                stats['inference']=' + '.join(sorted(detector.yunet.effective));stats['notes']=list(dict.fromkeys(detector.yunet.notes))
                if len(chunk)==CHUNK:
                    begin=time.perf_counter();store.append(chunk,reasons,stats);stats['write_seconds']+=time.perf_counter()-begin
                    chunk=[];reasons=[]
                    if partial:partial(str(path))
                if progress and index%5==0:progress(min(.999,index/max(1,info.frames)),index,len(faces))
            if chunk:store.append(chunk,reasons,stats)
        if not len(store):raise ValueError('视频没有可读取的画面')
        validate_timeline(info,len(store))
        if fingerprint!=core.fingerprint(info.path):raise ValueError('分析期间源视频发生变化，请重新导入。')
        stats['elapsed_seconds']=time.perf_counter()-started;stats['frames_per_second']=stats['decoded_frames']/max(.001,stats['elapsed_seconds'])
        stats['model_benchmark']=detector.yunet.timings;store.finish(stats)
        if partial:partial(str(path))
        return {'path':str(path),'frames':len(store),'cache_hit':False,'stats':stats}
    finally:
        if detector:detector.close()
        store.close()
