"""Export profiles, original audio copy, trimming and precise frame-rate output."""
from __future__ import annotations
import copy
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
from dataclasses import dataclass,asdict
from fractions import Fraction
import re

import cv2
import numpy as np
import core

PROFILES={
    'quality':('高质量 · H.264','mp4','CRF 14，兼顾清晰度与兼容性。'),
    'compact':('均衡体积 · H.264','mp4','CRF 18，适合日常分享。'),
    'prores':('后期中间片 · ProRes HQ','mov','10 位 4:2:2 编码，适合继续剪辑，文件较大。'),
    'lossless':('无损编码 · FFV1','mkv','无损保存处理后的 RGB 画面，文件很大。')}


@dataclass
class ExportOptions:
    profile:str='quality'
    audio:str='copy'
    resolution:int=0
    fps:float=0
    start_frame:int=0
    end_frame:int=-1
    rotation:int=0
    volume:float=1.0
    auto_mask:bool=True


def details(path):
    r=subprocess.run([core.ffmpeg(),'-hide_banner','-i',str(path)],capture_output=True,creationflags=core.HIDDEN)
    text=r.stderr.decode('utf8','replace')
    video=next((line for line in text.splitlines() if 'Video:' in line),'')
    audio=next((line for line in text.splitlines() if 'Audio:' in line),'')
    match=re.search(r'Audio:\s*([\w]+)',audio)
    return {'audio_codec':match.group(1) if match else '',
            'hdr':any(tag in video for tag in ['smpte2084','arib-std-b67','bt2020']),
            'ten_bit':bool(re.search(r'(?:yuv\w*|gbr\w*)p(?:10|12|16)',video)),
            'video_description':video.strip(),'audio_description':audio.strip()}


def suffix_for(options,media):
    if options.profile not in PROFILES:
        raise ValueError('未知的导出预设。')
    suffix=PROFILES[options.profile][1]
    # PCM in camera MP4 files is not broadly supported in standard MP4 muxers.
    # Choose MOV explicitly rather than silently compressing the original audio.
    if suffix=='mp4' and options.audio=='copy' and media['audio_codec'] not in ('','aac','mp3','alac','ac3','eac3'):
        suffix='mov'
    return '.'+suffix


def geometry(info,options):
    w,h=info.width,info.height
    if options.resolution:
        factor=min(1.,options.resolution/min(w,h))
        w,h=max(2,round(w*factor/2)*2),max(2,round(h*factor/2)*2)
    return w,h


def validate(info,options,media):
    if media['hdr']:
        raise ValueError('当前颜色处理面向 SDR。HDR / HLG / PQ 素材请先做受控的 SDR 转换；本版不会把它悄悄当作 SDR 导出。')
    if options.profile not in PROFILES:
        raise ValueError('未知导出预设。')
    if options.rotation not in (0,90,180,270):
        raise ValueError('旋转角度无效。')
    if options.audio not in ('copy','aac','mute'):
        raise ValueError('音频设置无效。')
    if options.resolution not in (0,720,1080,2160):
        raise ValueError('输出尺寸设置无效。')
    if not 0<=options.volume<=4:
        raise ValueError('音量应在 0～400% 之间。')
    if options.audio=='copy' and options.volume!=1:
        raise ValueError('原音频直拷不能同时调整音量，请选择高质量 AAC。')
    if options.fps and (options.fps>info.fps+.01 or options.fps<1):
        raise ValueError('输出帧率需在 1 到源帧率之间。保留原帧率不会插入重复帧。')
    end=info.frames if options.end_frame<0 else options.end_frame
    if not 0<=options.start_frame<end<=info.frames:
        raise ValueError('导出区间无效。')
    if media['ten_bit'] and options.profile in ('quality','compact'):
        raise ValueError('此素材为高于 8 位的 SDR。请选择 ProRes HQ 或 FFV1，避免降为 8 位。')
    return end


def preview_with_audio(analysis,cancel=None):
    """Attach bounded audio to the already normalized, low-resolution preview."""
    path=Path(analysis.preview_path)
    new=path.with_name(path.stem+'-sound.mp4')
    duration=f'{analysis.info.duration:.9f}'
    try:
        with tempfile.TemporaryFile() as errors:
            p=subprocess.Popen([core.ffmpeg(),'-hide_banner','-loglevel','error','-nostdin','-y',
                '-i',str(path),'-i',analysis.info.path,'-map','0:v:0','-map','1:a:0?',
                '-c:v','copy','-c:a','aac','-b:a','128k',
                '-af',f'apad=whole_dur={duration},atrim=duration={duration}',
                '-t',duration,'-movflags','+faststart',str(new)],
                stdout=subprocess.DEVNULL,stderr=errors,creationflags=core.HIDDEN)
            try:
                while p.poll() is None:
                    core.check_cancel(cancel)
                    try:p.wait(timeout=.2)
                    except subprocess.TimeoutExpired:pass
                if p.returncode:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode('utf8','replace')[-1000:])
            finally:
                if p.poll() is None:
                    p.kill();p.wait()
        path.unlink(missing_ok=True)
        analysis.preview_path=str(new)
    except Exception:
        new.unlink(missing_ok=True)
        raise


def waveform(path,points=640):
    p=subprocess.run([core.ffmpeg(),'-v','error','-i',str(path),'-map','0:a:0?',
        '-vn','-ac','1','-ar','2000','-f','f32le','pipe:1'],capture_output=True,creationflags=core.HIDDEN)
    if p.returncode or not p.stdout:
        return []
    values=np.frombuffer(p.stdout,np.float32)
    return [float(np.max(np.abs(chunk))) if len(chunk) else 0 for chunk in np.array_split(values,points)]


def rotate(frame,angle):
    return np.ascontiguousarray(np.rot90(frame,-angle//90)) if angle else frame


def mask_render(frame,faces,settings,manual,index,options):
    if not options.auto_mask:
        settings=copy.copy(settings)
        settings.missing='keep'
        faces=[]
    return rotate(core.render_frame(frame,faces,settings,manual,index),options.rotation)


def export(analysis,destination,settings,options,manual=None,cancel=None,progress=None,media=None):
    info=analysis.info
    media=media or details(info.path)
    end=validate(info,options,media)
    source,dest=Path(info.path).resolve(),Path(destination).resolve()
    if dest==source or dest.exists():
        raise ValueError('请使用一个尚不存在的新文件名，已有文件不会被覆盖。')
    if dest.suffix.lower()!=suffix_for(options,media):
        raise ValueError('目标文件扩展名与当前预设不一致。')
    if analysis.fingerprint!=core.fingerprint(source):
        raise ValueError('源视频已变化，请重新分析。')
    w,h=geometry(info,options)
    ow,oh=(h,w) if options.rotation in (90,270) else (w,h)
    duration=(end-options.start_frame)/info.fps
    rate=options.fps or info.fps
    rate_text=str(Fraction(rate).limit_denominator(1001000))
    # Keep precision through RGB decoding and mask processing for high-bit SDR.
    high_depth=media['ten_bit'] or options.profile=='prores'
    pixel='rgb48le' if high_depth else 'rgb24'
    dtype=np.dtype('<u2') if high_depth else np.dtype('u1')
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.studio-',suffix=dest.suffix,dir=dest.parent)
    os.close(fd)
    temp=Path(name)
    errors=tempfile.TemporaryFile()
    dec_errors=tempfile.TemporaryFile()
    encoder=decoder=None
    complete=False
    try:
        profile=options.profile
        filters=['pad=ceil(iw/2)*2:ceil(ih/2)*2','setsar=1']
        if options.fps and abs(options.fps-info.fps)>.001:
            filters.append(f'fps={rate_text}')
        if profile=='lossless':
            video=['-c:v','ffv1','-level','3','-coder','1','-context','1',
                   '-pix_fmt','gbrp16le' if high_depth else 'bgr0']
        else:
            # Explicit matrix/range and tags prevent unlabelled RGB->YUV output.
            filters.append('scale=out_color_matrix=bt709:out_range=tv')
            video=(['-c:v','prores_ks','-profile:v','3','-pix_fmt','yuv422p10le']
                if profile=='prores' else
                ['-c:v','libx264','-preset','medium' if profile=='quality' else 'fast',
                 '-crf','14' if profile=='quality' else '18','-pix_fmt','yuv420p'])
            video+=['-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv']
        command=[core.ffmpeg(),'-hide_banner','-loglevel','error','-nostdin','-y',
            '-f','rawvideo','-pixel_format',pixel,'-video_size',f'{ow}x{oh}',
            '-framerate',info.fps_text,'-i','pipe:0',
            '-ss',f'{options.start_frame/info.fps:.9f}','-i',info.path,
            '-map','0:v:0','-vf',','.join(filters),*video]
        if options.audio!='mute' and media['audio_codec']:
            command+=['-map','1:a:0']
            if options.audio=='copy':
                if media['audio_codec'].startswith('pcm_') and (options.start_frame or end<info.frames):
                    # PCM has no compression. Re-serialize unchanged samples to
                    # trim inside an audio packet without an extra packet tail.
                    command+=['-c:a',media['audio_codec'],'-af',f'atrim=duration={duration:.9f},asetpts=PTS-STARTPTS']
                else:
                    command+=['-c:a','copy']
            else:
                command+=['-c:a','aac','-b:a','320k','-af',
                    f'volume={options.volume},apad=whole_dur={duration:.9f},atrim=duration={duration:.9f}']
        else:
            command+=['-an']
        command+=['-t',f'{duration:.9f}','-map_metadata','-1']
        if dest.suffix.lower() in ('.mp4','.mov'):
            command+=['-movflags','+faststart']
        encoder=subprocess.Popen(command+[str(temp)],stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,stderr=errors,creationflags=core.HIDDEN)
        decoder=subprocess.Popen([core.ffmpeg(),'-hide_banner','-loglevel','error','-nostdin',
            '-i',info.path,'-map','0:v:0','-an','-sn','-dn','-vf',
            f'fps={info.fps_text},trim=start_frame={options.start_frame}:end_frame={end},setpts=PTS-STARTPTS,scale={w}:{h},setsar=1',
            '-fps_mode','passthrough','-pix_fmt',pixel,'-f','rawvideo','pipe:1'],stdout=subprocess.PIPE,stderr=dec_errors,creationflags=core.HIDDEN)
        nbytes=w*h*3*dtype.itemsize
        for index in range(options.start_frame,end):
            core.check_cancel(cancel)
            raw=decoder.stdout.read(nbytes)
            if len(raw)!=nbytes:
                raise RuntimeError('源视频未完整解码，已停止导出。')
            rgb=np.frombuffer(raw,dtype).reshape(h,w,3)
            processed=mask_render(rgb,analysis.faces[index],settings,manual,index,options)
            try:encoder.stdin.write(processed.tobytes())
            except BrokenPipeError:
                errors.seek(0)
                raise RuntimeError('编码失败：'+errors.read().decode('utf8','replace')[-1500:])
            if progress and (index-options.start_frame)%5==0:
                progress((index-options.start_frame+1)/(end-options.start_frame),index,0)
        encoder.stdin.close()
        for process in (decoder,encoder):
            while process.poll() is None:
                core.check_cancel(cancel)
                try:process.wait(timeout=.2)
                except subprocess.TimeoutExpired:pass
            if process.returncode:
                errors.seek(0);dec_errors.seek(0)
                raise RuntimeError('导出失败：'+(errors.read()+dec_errors.read()).decode('utf8','replace')[-1500:])
        core.check_cancel(cancel)
        actual=core.media_duration(temp)
        if abs(actual-duration)>max(.15,2/rate):
            raise RuntimeError(f'音视频时长校验失败：预计 {duration:.3f}s，实际 {actual:.3f}s。')
        result=core.probe(str(temp))
        if abs(result.fps-rate)>.05 or (result.width,result.height)!=(ow+ow%2,oh+oh%2):
            raise RuntimeError('成片分辨率或帧率校验失败，未保存输出。')
        if analysis.fingerprint!=core.fingerprint(source) or dest.exists():
            raise RuntimeError('源视频或目标位置发生变化，已停止保存。')
        temp.rename(dest)
        complete=True
        return str(dest)
    finally:
        for process in (decoder,encoder):
            if process:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                for pipe in (process.stdin,process.stdout):
                    if pipe and not pipe.closed:
                        try:pipe.close()
                        except OSError:pass
        errors.close();dec_errors.close()
        if not complete:temp.unlink(missing_ok=True)


def extract_audio(source,dest,cancel=None):
    target=Path(dest)
    if target.exists():raise ValueError('目标已存在，请换一个文件名。')
    p=subprocess.Popen([core.ffmpeg(),'-v','error','-nostdin','-i',str(source),'-map','0:a:0',
        '-vn','-c:a','pcm_s24le',str(target)],stderr=subprocess.PIPE,creationflags=core.HIDDEN)
    try:
        while p.poll() is None:
            core.check_cancel(cancel)
            try:p.wait(timeout=.2)
            except subprocess.TimeoutExpired:pass
        err=p.stderr.read()
        if p.returncode:raise ValueError('无法提取音轨：'+err.decode('utf8','replace')[-800:])
        return str(target)
    except Exception:
        if p.poll() is None:p.kill();p.wait()
        target.unlink(missing_ok=True)
        raise
    finally:p.stderr.close()
