"""Bounded-memory, crash-resumable face geometry chunks. Never stores video."""
from __future__ import annotations
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import time
import threading
from functools import wraps
from functools import lru_cache
import numpy as np

SCHEMA=2
CHUNK=180


def root():
    folder=Path(os.environ.get('FACEPRIVACY_CACHE_DIR') or
                Path(os.environ.get('LOCALAPPDATA',Path.home()/'.cache'))/'FacePrivacyStudio'/'analysis-v2')
    folder.mkdir(parents=True,exist_ok=True);return folder


def cache_path(info,options):
    import core
    source=Path(info.path);stat=source.stat()
    digest=hashlib.sha256()
    with source.open('rb') as stream:
        digest.update(stream.read(65536));stream.seek(max(0,stat.st_size-65536));digest.update(stream.read(65536))
    payload={'schema':SCHEMA,'source':str(source.resolve()),'size':stat.st_size,'mtime':stat.st_mtime_ns,
             'sample_hash':digest.hexdigest(),'fps':info.fps_text,'size_px':[info.width,info.height],'options':asdict(options),
             'models':[model_hash(str(p),p.stat().st_mtime_ns) for p in (core.MODEL,core.FACE_MODEL)]}
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return root()/(key+'.sqlite')


@lru_cache(maxsize=8)
def model_hash(path,mtime):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def locked(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.lock:return method(self,*args,**kwargs)
    return call


class FaceStore(Sequence):
    """Serialized connection and bounded chunks; safe for UI/preview/export."""
    def __init__(self,path,write=False):
        self.path=Path(path);self.write=write;self.chunks=OrderedDict();self.lock=threading.RLock()
        self.db=sqlite3.connect(str(path),timeout=10,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS chunks (start INTEGER PRIMARY KEY, count INTEGER, data BLOB)')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS review (start INTEGER, end INTEGER, reason TEXT)')
        self.db.commit();self.refresh()

    @locked
    def refresh(self):
        self.state={k:json.loads(v) for k,v in self.db.execute('SELECT key,value FROM state')}
        self.count=int(self.state.get('frames',0));self.complete=bool(self.state.get('complete',False))
        return self

    def __len__(self):return self.count

    @locked
    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self)))]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        start=index//CHUNK*CHUNK
        if start not in self.chunks:
            row=self.db.execute('SELECT data FROM chunks WHERE start=?',(start,)).fetchone()
            if row is None:raise ValueError('识别缓存不完整，请清除当前缓存后重试。')
            with np.load(io.BytesIO(row[0]),allow_pickle=False) as data:
                counts=data['counts'];points=data['points']
            if counts.ndim!=1 or len(counts)>CHUNK or points.shape!=(len(counts),6,40,2) or np.any(counts>6) or not np.isfinite(points).all():
                raise ValueError('识别缓存格式无效。')
            self.chunks[start]=(counts,points)
            while len(self.chunks)>6:self.chunks.popitem(last=False)
        self.chunks.move_to_end(start);counts,points=self.chunks[start];offset=index-start
        return [points[offset,i].copy() for i in range(int(counts[offset]))]

    @locked
    def append(self,frames,reasons,stats):
        if not self.write:raise RuntimeError('Read-only face store')
        if not frames:return
        if self.count%CHUNK:raise ValueError('Cannot append after a final partial chunk')
        if len(frames)>CHUNK:raise ValueError('Chunk too large')
        values=np.zeros((len(frames),6,40,2),np.float32);counts=np.zeros(len(frames),np.uint8)
        for i,faces in enumerate(frames):
            counts[i]=min(6,len(faces))
            for j,face in enumerate(faces[:6]):values[i,j]=face
        buf=io.BytesIO();np.savez_compressed(buf,counts=counts,points=values)
        start=self.count
        with self.db:
            self.db.execute('INSERT INTO chunks VALUES (?,?,?)',(start,len(frames),buf.getvalue()))
            runs=[]
            for offset,reason in enumerate(reasons):
                if not reason:continue
                frame=start+offset
                if runs and runs[-1][1]==frame-1 and runs[-1][2]==reason:runs[-1][1]=frame
                else:runs.append([frame,frame,reason])
            self.db.executemany('INSERT INTO review VALUES (?,?,?)',runs)
            self.set_state('frames',start+len(frames))
            self.set_state('face_frames',int(self.state.get('face_frames',0))+int(np.count_nonzero(counts)))
            self.set_state('stats',stats)
        self.refresh()

    def set_state(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))

    @locked
    def finish(self,stats):
        with self.db:self.set_state('complete',True);self.set_state('stats',stats)
        self.refresh()

    def discard_partial_tail(self):
        if self.complete or self.count%CHUNK==0:return
        start=self.count//CHUNK*CHUNK
        removed=sum(bool(self[i]) for i in range(start,self.count))
        with self.db:
            self.db.execute('DELETE FROM chunks WHERE start>=?',(start,))
            self.db.execute('DELETE FROM review WHERE start>=?',(start,))
            self.set_state('frames',start)
            self.set_state('face_frames',int(self.state.get('face_frames',0))-removed)
        self.chunks.clear();self.refresh()

    @locked
    def review(self):
        runs=[]
        for start,end,reason in self.db.execute('SELECT start,end,reason FROM review ORDER BY start'):
            if runs and runs[-1]['end']+1==start and runs[-1]['reason']==reason:runs[-1]['end']=end
            else:runs.append({'start':start,'end':end,'reason':reason})
        return runs

    @locked
    def close(self):
        self.chunks.clear()
        if self.db is not None:self.db.close();self.db=None


def clear_for(path):
    """Explicit single-video cache clear, restricted to this cache root."""
    path=Path(path).resolve();folder=root().resolve()
    if path.parent!=folder or path.suffix!='.sqlite':raise ValueError('缓存路径无效')
    for suffix in ('','-wal','-shm'):
        target=Path(str(path)+suffix)
        if target.parent==folder:target.unlink(missing_ok=True)
