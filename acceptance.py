"""Opt-in, repeatable packaged-app verification; never runs on normal launch."""
from pathlib import Path
import hashlib,json,subprocess,time,traceback
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QFileDialog
from PySide6.QtMultimedia import QMediaPlayer
import core,exporter

def run_verification(window,source,out):
    out.mkdir(parents=True,exist_ok=True)
    for font in ['msyh.ttc','seguisym.ttf']:
        path=Path('C:/Windows/Fonts')/font
        if path.is_file():QFontDatabase.addApplicationFont(str(path))
    report={'source_name':source.name,'passed':False,'checks':[]}
    state={'step':0,'started':time.monotonic()};errors=[]
    window.message=lambda message:errors.append(str(message))
    timer=QTimer(window);timer.setInterval(120)
    def finish(error=None):
        timer.stop()
        if error:report['error']=str(error)
        else:report['passed']=True
        (out/'packaged-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        window.close();QApplication.instance().exit(0 if report['passed'] else 1)
    def tick():
        try:
            if errors:raise RuntimeError(errors)
            if time.monotonic()-state['started']>180:raise TimeoutError('Packaged acceptance timed out')
            if window.busy:return
            if state['step']==0:
                state['step']=1;window.import_paths([str(source)])
            elif state['step']==1:
                c=window.clip;assert c.analysis
                window.missing.setCurrentIndex(1);window.region.setCurrentIndex(window.region.findData('eyes'))
                window.grab().save(str(out/'packaged-studio.png'))
                report['checks'].append({'analysis_frames':c.info.frames,'face_frames':sum(bool(f) for f in c.analysis.faces)})
                state['frame']=window.index;state['play_started']=time.monotonic();window.toggle_play();state['step']=2
            elif state['step']==2:
                if time.monotonic()-state['play_started']<.7:return
                if window.index<=state['frame'] and time.monotonic()-state['play_started']<8:return
                report['player']={'position':window.player.position(),'index':window.index,'initial_index':state['frame'],
                    'status':str(window.player.mediaStatus()),'state':str(window.player.playbackState()),
                    'duration':window.player.duration(),'seekable':window.player.isSeekable(),'error':window.player.errorString()}
                assert window.player.error()==QMediaPlayer.Error.NoError,window.player.errorString()
                assert window.index>state['frame'],'Preview did not advance'
                if window.clip.media['audio_codec']:assert window.player.hasAudio()
                report['checks'].append({'preview_playback':True,'has_audio':window.player.hasAudio()})
                window.player.pause();state['step']=3
                suffix=exporter.suffix_for(window.clip.options,window.clip.media)
                target=out/('packaged_export'+suffix);state['output']=target
                QFileDialog.getSaveFileName=lambda *_,**__:(str(target),'')
                window.export_current()
            elif state['step']==3:
                target=state['output'];assert target.is_file(),'Export did not save a file'
                result=core.probe(str(target));source_info=window.clip.info
                assert (result.width,result.height,result.frames)==(source_info.width,source_info.height,source_info.frames)
                assert abs(result.fps-source_info.fps)<.01
                assert abs(core.media_duration(target)-source_info.duration)<.1
                if window.clip.media['audio_codec'].startswith('pcm_'):
                    def pcm(path):
                        p=subprocess.run([core.ffmpeg(),'-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-c:a','pcm_s16le','pipe:1'],capture_output=True,creationflags=core.HIDDEN)
                        assert p.returncode==0;return p.stdout
                    assert pcm(source)==pcm(target),'PCM samples changed'
                    report['checks'].append({'pcm_samples_identical':True})
                report['checks'].append({'output_size':[result.width,result.height],'fps':result.fps,'frames':result.frames,'duration':core.media_duration(target)})
                window.tabs.setCurrentIndex(window.tabs.count()-1);window.grab().save(str(out/'packaged-export.png'));finish()
        except Exception:finish(traceback.format_exc())
    timer.timeout.connect(tick);timer.start()
