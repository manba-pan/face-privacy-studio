"""Optional Windows GPU inference and verified hardware codec selection.

No network, driver installation or GPU requirement. DirectML uses a separate
sequential session for each fixed input shape, as required by ONNX Runtime.
"""
from __future__ import annotations
from collections import OrderedDict
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time
import cv2
import numpy as np


def adapters():
    """DXGI indices match DirectML device_id; WMI display indices do not."""
    if os.name != 'nt':return []
    class GUID(C.Structure):
        _fields_=[('a',W.DWORD),('b',W.WORD),('c',W.WORD),('d',C.c_ubyte*8)]
    class LUID(C.Structure):
        _fields_=[('lo',W.DWORD),('hi',W.LONG)]
    class DESC(C.Structure):
        _fields_=[('name',W.WCHAR*128),('vendor',W.UINT),('device',W.UINT),
                  ('subsystem',W.UINT),('revision',W.UINT),('video',C.c_size_t),
                  ('system',C.c_size_t),('shared',C.c_size_t),('luid',LUID),('flags',W.UINT)]
    def method(ptr,index,result,*args):
        table=C.cast(ptr,C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(result,C.c_void_p,*args)(table[index])
    factory=C.c_void_p();items=[]
    try:
        iid=GUID(0x770aae78,0xf26f,0x4dba,(C.c_ubyte*8)(0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87))
        create=C.WinDLL('dxgi').CreateDXGIFactory1
        create.argtypes=[C.POINTER(GUID),C.POINTER(C.c_void_p)];create.restype=W.LONG
        if create(C.byref(iid),C.byref(factory))<0:return []
        for index in range(16):
            adapter=C.c_void_p()
            if method(factory,12,W.LONG,W.UINT,C.POINTER(C.c_void_p))(factory,index,C.byref(adapter))<0:break
            try:
                desc=DESC()
                if method(adapter,10,W.LONG,C.POINTER(DESC))(adapter,C.byref(desc))>=0 and not desc.flags&2:
                    items.append({'id':index,'name':desc.name,'memory':int(desc.video),'vendor':int(desc.vendor)})
            finally:method(adapter,2,W.ULONG)(adapter)
    except (OSError,ValueError):return []
    finally:
        if factory.value:method(factory,2,W.ULONG)(factory)
    return items


def best_adapter():
    devices=adapters()
    return max(devices,key=lambda d:d['memory']) if devices else None


def nms(rows,threshold=.3):
    if len(rows)==0:return np.empty((0,15),np.float32)
    rows=np.asarray(rows,dtype=np.float32);order=np.argsort(rows[:,-1])[::-1];kept=[]
    while len(order):
        i=int(order[0]);kept.append(i);other=order[1:]
        if not len(other):break
        lo=np.maximum(rows[i,:2],rows[other,:2]);hi=np.minimum(rows[i,:2]+rows[i,2:4],rows[other,:2]+rows[other,2:4])
        overlap=np.prod(np.maximum(0,hi-lo),axis=1)
        union=np.prod(rows[i,2:4])+np.prod(rows[other,2:4],axis=1)-overlap
        order=other[overlap/np.maximum(union,1)<=threshold]
    return rows[kept]


class YuNet:
    """Same public YuNet model; independent vectorized ONNX output decoding."""
    def __init__(self,model,device='auto',threshold=.65):
        self.model=Path(model).read_bytes();self.request=device;self.threshold=threshold
        self.sessions=OrderedDict();self.notes=[];self.timings={};self.effective=set()
        self.cv=cv2.FaceDetectorYN.create('onnx',np.frombuffer(self.model,np.uint8),np.empty(0,np.uint8),(320,320),threshold,.3,5000)
        try:
            import onnxruntime as ort
            self.ort=ort
        except ImportError:self.ort=None
        self.adapter=best_adapter() if device=='auto' else None
        self.device_id=self.adapter['id'] if self.adapter else 0
        if device.startswith('gpu:'):self.device_id=int(device.split(':',1)[1])

    def session(self,h,w,provider):
        options=self.ort.SessionOptions();options.enable_mem_pattern=False
        options.execution_mode=self.ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads=2;options.inter_op_num_threads=1
        options.add_free_dimension_override_by_name('height',h)
        options.add_free_dimension_override_by_name('width',w)
        providers=[('DmlExecutionProvider',{'device_id':self.device_id}),'CPUExecutionProvider'] if provider=='GPU' else ['CPUExecutionProvider']
        s=self.ort.InferenceSession(self.model,sess_options=options,providers=providers)
        if provider=='GPU' and 'DmlExecutionProvider' not in s.get_providers():raise RuntimeError('DirectML provider unavailable')
        s.disable_fallback()
        return s

    def choose(self,blob):
        h,w=blob.shape[2:];key=(h,w)
        if key in self.sessions:
            self.sessions.move_to_end(key);return self.sessions[key]
        if self.ort is None:
            self.notes.append('未安装 ONNX Runtime，使用 CPU');result=(None,'CPU / OpenCV')
        else:
            cpu=self.session(h,w,'CPU');result=(cpu,'CPU / ONNX')
            if self.request!='cpu' and 'DmlExecutionProvider' in self.ort.get_available_providers():
                try:
                    gpu=self.session(h,w,'GPU')
                    timings={}
                    for name,session in [('CPU',cpu),('GPU',gpu)]:
                        session.run(None,{'input':blob})
                        begin=time.perf_counter()
                        for _ in range(3):session.run(None,{'input':blob})
                        timings[name]=(time.perf_counter()-begin)/3
                    self.timings[f'{w}x{h}']=timings
                    if self.request.startswith('gpu:') or timings['GPU']<timings['CPU']*.95:result=(gpu,'GPU / DirectML')
                except Exception as error:self.notes.append('GPU 初始化失败，已切换 CPU：'+str(error).splitlines()[0][:150])
        self.sessions[key]=result
        while len(self.sessions)>5:self.sessions.popitem(last=False)
        self.effective.add(result[1]);return result

    def detect(self,bgr):
        h,w=bgr.shape[:2];ph=(h+31)//32*32;pw=(w+31)//32*32
        pad=cv2.copyMakeBorder(bgr,0,ph-h,0,pw-w,cv2.BORDER_CONSTANT,value=0)
        blob=np.ascontiguousarray(pad.transpose(2,0,1)[None],dtype=np.float32)
        session,label=self.choose(blob)
        if session is None:
            self.cv.setInputSize((w,h));_,rows=self.cv.detect(bgr)
            return rows if rows is not None else np.empty((0,15),np.float32)
        try:raw=session.run(None,{'input':blob})
        except Exception as error:
            if label.startswith('CPU'):raise
            self.notes.append('GPU 运行失败，本次分析改用 CPU：'+str(error).splitlines()[0][:150])
            self.request='cpu';self.sessions.clear();session,label=self.choose(blob);raw=session.run(None,{'input':blob})
        output=dict(zip([o.name for o in session.get_outputs()],raw));rows=[]
        for stride in (8,16,32):
            scores=np.sqrt(np.clip(output[f'cls_{stride}'].reshape(-1),0,1)*np.clip(output[f'obj_{stride}'].reshape(-1),0,1))
            take=np.flatnonzero(scores>=self.threshold)
            if not len(take):continue
            grid=np.column_stack((take%(pw//stride),take//(pw//stride))).astype(np.float32)
            box=output[f'bbox_{stride}'].reshape(-1,4)[take]
            size=np.exp(np.clip(box[:,2:4],-16,16))*stride
            xy=(box[:,:2]+grid)*stride-size/2
            points=(output[f'kps_{stride}'].reshape(-1,5,2)[take]+grid[:,None,:])*stride
            rows.append(np.column_stack((xy,size,points.reshape(-1,10),scores[take])))
        return nms(np.concatenate(rows) if rows else [])


ENCODER_ARGS={
    'cpu':['-c:v','libx264','-preset','medium','-crf','14'],
    'amf':['-c:v','h264_amf','-quality','quality','-rc','cqp','-qp_i','16','-qp_p','18','-qp_b','20'],
    'nvenc':['-c:v','h264_nvenc','-preset','p6','-rc','vbr','-cq','18','-b:v','0'],
    'qsv':['-c:v','h264_qsv','-preset','slow','-global_quality','18'],
}
ENCODER_LABELS={'cpu':'CPU · 画质优先','amf':'AMD AMF','nvenc':'NVIDIA NVENC','qsv':'Intel Quick Sync'}


def test_encoder(ffmpeg,key):
    command=[ffmpeg,'-hide_banner','-v','error','-nostdin','-f','lavfi','-i','color=c=gray:s=128x128:r=30',
             '-frames:v','3',*ENCODER_ARGS[key],'-pix_fmt','yuv420p','-f','null','-']
    try:
        result=subprocess.run(command,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return {'available':result.returncode==0,'reason':result.stderr.decode('utf8','replace')[-500:]}
    except (OSError,subprocess.TimeoutExpired) as error:return {'available':False,'reason':str(error)[:200]}


def decoder_args(ffmpeg,source,preference='auto'):
    """Validate this source's hardware decoding before streaming any frames."""
    if preference=='cpu' or os.name!='nt':return [],'CPU'
    args=['-hwaccel','d3d11va']
    command=[ffmpeg,'-hide_banner','-v','verbose','-nostdin',*args,'-i',str(source),'-map','0:v:0','-an',
             '-frames:v','3','-vf','scale=64:64','-pix_fmt','rgb24','-f','rawvideo','-']
    try:
        p=subprocess.run(command,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        log=p.stderr.decode('utf8','replace').lower()
        active=any(token in log for token in ('format d3d11 chosen','using d3d11va','d3d11va surface','pix_fmt: d3d11'))
        if p.returncode==0 and len(p.stdout)==64*64*3*3 and active:return args,'GPU / D3D11VA'
    except (OSError,subprocess.TimeoutExpired):pass
    return [],'CPU（硬件解码不可用）'
