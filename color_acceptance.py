"""Opt-in frozen-app acceptance using generated media, never personal footage."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog
import av
import numpy as np
import core, color_management as colors, native_export


def verification_loop(app, window, output):
    output.mkdir(parents=True,exist_ok=True)
    source=output/'generated-601-10bit.mkv'
    # This mode may only create a fresh acceptance run.
    if source.exists(): raise ValueError('Acceptance output must be a new directory')
    command=[core.ffmpeg(),'-v','error','-nostdin','-f','lavfi','-i','testsrc2=size=320x240:rate=12:duration=1',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',
        '-vf','setparams=color_primaries=smpte170m:color_trc=smpte170m:colorspace=smpte170m:range=tv',
        '-c:v','ffv1','-pix_fmt','yuv420p10le','-c:a','pcm_s16le',str(source)]
    subprocess.run(command,check=True,creationflags=core.HIDDEN)
    interframe=output/'generated-interframe.mp4';rotated=output/'generated-rotated.mp4'
    subprocess.run([core.ffmpeg(),'-v','error','-nostdin','-f','lavfi','-i','testsrc2=size=320x240:rate=30:duration=2',
        '-c:v','libx264','-g','120','-sc_threshold','0','-bf','3',str(interframe)],check=True,creationflags=core.HIDDEN)
    subprocess.run([core.ffmpeg(),'-v','error','-nostdin','-display_rotation:v:0','90','-i',str(interframe),
        '-c','copy',str(rotated)],check=True,creationflags=core.HIDDEN)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    state={'step':0,'started':time.monotonic()};errors=[]
    report={'passed':False,'checks':[]}
    original_dialog=QFileDialog.getSaveFileName
    window.message=lambda message:errors.append(str(message))
    window.renderer.events.failed.connect(lambda _,message:errors.append(message))
    timer=QTimer(window);timer.setInterval(100)
    def select(widget,value):widget.setCurrentIndex(widget.findData(value))
    def start_export(name):
        target=output/name
        QFileDialog.getSaveFileName=lambda *_,**__:(str(target),'')
        window.export_current()
        QFileDialog.getSaveFileName=original_dialog
    def finish(error=None):
        timer.stop()
        report['passed']=error is None
        if error:report['error']=str(error)
        (output/'color-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        window.close();app.exit(0 if report['passed'] else 1)
    def tick():
        try:
            if errors:raise RuntimeError(errors)
            if time.monotonic()-state['started']>90:raise TimeoutError('Color acceptance timed out')
            if window.busy:return
            step=state['step']
            if step==0:
                state['step']=1;window.import_paths([str(source)])
            elif step==1:
                c=window.clip
                assert c and c.analysis is None and c.options.profile=='native'
                assert not window.input_color.isEnabled() and c.media['bit_depth']==10
                window.auto.setChecked(False)
                c.manual=[{'start':0,'end':c.info.frames-1,'rect':[.2,.2,.55,.55]}]
                window.refresh_frame();window.tabs.setCurrentIndex(window.tabs.count()-1)
                state['step']=10
            elif step==10:
                if window.viewer.image is None:return
                window.grab().save(str(output/'native-controls.png'))
                report['checks'].append('Import keeps original bytes; 10-bit source defaults to native with locked color controls')
                state['step']=2;start_export('native-mask.mkv')
            elif step==2:
                dest=output/'native-mask.mkv'
                assert dest.exists() and '逐帧校验通过' in window.last_export_backend
                mask=native_export.mask_for(320,240,[],window.clip.settings,window.clip.manual,0,False)
                with av.open(str(source)) as src, av.open(str(dest)) as dst:
                    for a,b in zip(src.decode(video=0),dst.decode(video=0)):
                        for x,y in zip(a.planes,b.planes):
                            p=native_export.plane_array(x,10)[1];q=native_export.plane_array(y,10)[1]
                            outside=native_export.plane_mask(mask,x.width,x.height)==0
                            assert np.array_equal(p[outside],q[outside])
                report['checks'].append('Frozen FFV1: all frames and original audio verified; pixels outside mask identical')
                select(window.profile,'lossless');assert window.input_color.isEnabled()
                select(window.input_color,'smpte170m');select(window.input_range,'tv');select(window.output_color,'bt709')
                project=output/'settings.privacy.json'
                project.write_text(json.dumps(window.project_data(),ensure_ascii=False),encoding='utf8')
                window.load_project(str(project))
                assert window.clip.options.output_color=='bt709' and window.clip.options.input_color=='smpte170m'
                assert window.clip.options.input_range=='tv' and window.clip.analysis is None
                window.tabs.setCurrentIndex(window.tabs.count()-1);state['step']=20
            elif step==20:
                if window.viewer.image is None:return
                window.grab().save(str(output/'color-controls.png'))
                report['checks'].append('Color controls and manual masks survive project save/reopen')
                state['step']=3;start_export('converted-709.mkv')
            elif step==3:
                tags=colors.probe(output/'converted-709.mkv')
                assert tags['color']=={'color_primaries':1,'color_trc':1,'colorspace':0,'color_range':2}
                assert tags['bit_depth']==16
                assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
                report['checks'].append('Frozen Rec.601 to Rec.709 export: tagged 16-bit RGB FFV1, source SHA-256 unchanged')
                state['step']=4;window.import_paths([str(interframe)])
            elif step==4:
                window.bin.setCurrentRow(len(window.clips)-1)
                assert window.clip.options.profile=='native'
                window.auto.setChecked(False)
                window.clip.manual=[{'start':0,'end':0,'rect':[.2,.2,.6,.6]}]
                state['step']=5;start_export('interframe-native.mkv')
            elif step==5:
                count=0
                with av.open(str(interframe)) as src,av.open(str(output/'interframe-native.mkv')) as dst:
                    originals=iter(src.decode(video=0))
                    for index,b in enumerate(dst.decode(video=0)):
                        a=next(originals,None);assert a is not None
                        for x,y in zip(a.planes,b.planes):
                            p=native_export.plane_array(x,8)[1];q=native_export.plane_array(y,8)[1]
                            if index>0:assert np.array_equal(p,q),'Unexpected pixels after the one-frame mask'
                        count+=1
                    assert count==60 and next(originals,None) is None
                report['checks'].append('H.264 P/B frames: one-frame mask leaves all 59 later frames unchanged')
                state['step']=6;window.import_paths([str(rotated)])
            elif step==6:
                window.bin.setCurrentRow(len(window.clips)-1)
                assert window.clip.options.profile=='lossless'
                assert (window.clip.info.width,window.clip.info.height)==(240,320)
                window.auto.setChecked(False)
                window.clip.manual=[{'start':0,'end':59,'rect':[.1,.1,.4,.4]}]
                window.viewer.image=None;window.refresh_frame();window.request_still();state['step']=7
            elif step==7:
                if window.viewer.image is None:return
                assert (window.viewer.image.width(),window.viewer.image.height())==(240,320)
                window.grab().save(str(output/'rotated-preview.png'))
                report['checks'].append('Rotated source defaults to RGB lossless and keeps portrait mask preview')
                finish()
        except Exception as error:finish(error)
    timer.timeout.connect(tick);timer.start()
    result=app.exec()
    QFileDialog.getSaveFileName=original_dialog
    from shiboken6 import delete
    delete(window);app.processEvents()
    return result
