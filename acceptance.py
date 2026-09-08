"""Opt-in, repeatable packaged-app verification; never runs on normal launch."""
from pathlib import Path
import hashlib,json,subprocess,time,traceback
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QFileDialog,QLabel
from PySide6.QtMultimedia import QMediaPlayer
import core,exporter

def verify_author_ui(window,out):
    """Check the actual bundled codes and their rendered/save-original paths."""
    import cv2,numpy as np
    from project_info import ProjectDialog,SupportDialog,QRDialog
    expected={'alipay':'5df5ecbd5cab2dc2dc96344beff0385beef5cf9f18f18c72cdeb4f09085d9c31',
              'wechat':'e9ce92a5c1d53829b2b2ef8b6e7e28170e992d2e79114f0a8693c7a51893a5c6'}
    detector=cv2.QRCodeDetector()
    def decode(path):
        return detector.detectAndDecode(cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR))[0]
    assert window.windowTitle().startswith('视频一键打码工具')
    welcome=ProjectDialog(window,welcome=True)
    welcome.show();QApplication.processEvents()
    copy='\n'.join(label.text() for label in welcome.findChildren(QLabel))
    assert '免费使用，如有商业化等请联系作者\n模型可能漏脸，支持人工复查' in copy
    welcome.grab().save(str(out/'welcome.png'));welcome.close()
    support=SupportDialog(window);support.show();QApplication.processEvents()
    assert len(support.codes)==2
    support.grab().save(str(out/'support.png'))
    checks=[]
    for (name,path,_),label in zip(support.codes,support.qr_labels):
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest==expected[path.stem],name+' resource changed'
        payload=decode(path);assert payload
        rendered=out/(path.stem+'-rendered.png');label.grab().save(str(rendered))
        assert decode(rendered)==payload,name+' thumbnail does not decode to original'
        zoom=QRDialog(support,name,path);zoom.show();QApplication.processEvents()
        full=out/(path.stem+'-full.png')
        zoom.findChild(QLabel,'fullPaymentCode').grab().save(str(full))
        assert decode(full)==payload,name+' enlarged code does not decode to original'
        saved=out/(path.stem+'-saved'+path.suffix)
        original_dialog=QFileDialog.getSaveFileName
        try:
            QFileDialog.getSaveFileName=lambda *_,**__:(str(saved),'')
            zoom.save_code(name,path)
        finally:QFileDialog.getSaveFileName=original_dialog
        assert saved.read_bytes()==path.read_bytes()
        zoom.close()
        checks.append({'platform':name,'sha256':digest,'rendered_qr_matches_original':True,'saved_bytes_identical':True})
    support.close()
    return checks


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
                state['step']=-1
                report['author_ui']=verify_author_ui(window,out)
                state['step']=1;window.import_paths([str(source)])
            elif state['step']==1:
                c=window.clip;assert c.analysis
                assert window.clip.settings.missing=='keep' and window.missing.currentData()=='keep'
                window.region.setCurrentIndex(window.region.findData('eyes'))
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
