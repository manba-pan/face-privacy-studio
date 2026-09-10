"""Capture only our own Qt widget; exercise the actual preview and project state."""
from pathlib import Path
import sys,time,json,os
import faulthandler
faulthandler.dump_traceback_later(30,repeat=True)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtCore import QUrl
import studio,core

out=Path(__file__).resolve().parents[1]/'samples'/'verification'/'studio';out.mkdir(exist_ok=True)
app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(studio.STYLE)
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
QFontDatabase.addApplicationFont('C:/Windows/Fonts/seguisym.ttf')
window=studio.Studio();window.resize(1460,920);window.show()
errors=[];window.message=lambda text:errors.append(str(text))
def events(ms=100):
    deadline=time.monotonic()+ms/1000
    while time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
def wait_job():
    deadline=time.monotonic()+120
    while window.busy and time.monotonic()<deadline:events(100)
    assert not window.busy,'worker timeout'
    assert not errors,errors

events();window.grab().save(str(out/'studio_empty.png'))
source=os.environ.get('FACEPRIVACY_ACCEPTANCE_VIDEO',str(Path(__file__).resolve().parents[1]/'samples'/'测试采访_带声音.mp4'))
window.import_paths([source]);wait_job();events(200)
assert window.clip.analysis is None,'Import must not trigger analysis'
window.audio.setMuted(True);window.toggle_play();events(800)
assert window.index>0,'Original video did not play before analysis'
assert window.monitor.currentWidget()==window.native_video
window.player.pause();window.analyze_current();wait_job();events(250)
assert window.clip.analysis
frame=next(i for i,faces in enumerate(window.clip.analysis.faces) if faces and i>window.clip.info.frames*.3)
assert window.clip.settings.missing=='keep' and window.missing.currentData()=='keep'
window.seek(frame);events(350)
assert len(window.clip.analysis.faces[frame])>=1
window.grab().save(str(out/'studio_face.png'))
window.region.setCurrentIndex(window.region.findData('eyes'));window.strength.setValue(5);events()
window.grab().save(str(out/'studio_eyes.png'))
window.toggle_play();events(650)
assert window.player.error()==QMediaPlayer.Error.NoError,window.player.errorString()
assert window.index>frame,'Video sink did not advance preview'
assert window.player.hasAudio(),'Audio track not loaded'
window.player.pause()
# Trimming and rotation require the RGB profile; native locks these controls.
window.profile.setCurrentIndex(window.profile.findData('lossless'));events()
start=round(window.clip.info.frames*.25);end=round(window.clip.info.frames*.75);key=round(window.clip.info.frames*.6)
window.seek(start);window.mark_in();window.seek(end-1);window.mark_out()
assert window.clip.options.start_frame==start and window.clip.options.end_frame==end
window.new_box();window.draw_rectangle([.1,.1,.2,.2]);window.seek(key);window.reposition_box();window.draw_rectangle([.5,.5,.6,.6])
assert len(window.clip.manual[0]['keyframes'])==2
window.seek(key)
rect=core.manual_rect(window.clip.manual[0],key)
assert window.clip.manual[0]['keyframes'][0]['frame']==key
window.rotation.setCurrentIndex(1);events();window.grab().save(str(out/'studio_manual.png'))
project=out/'ui_test.privacy.json';project.write_text(json.dumps(window.project_data(),ensure_ascii=False),encoding='utf8')
window.load_project(str(project));wait_job();events()
assert window.clip.analysis is None,'Opening a project must not launch analysis'
window.analyze_current();wait_job();events(250)
assert window.clip.options.rotation==90 and len(window.clip.manual[0]['keyframes'])==2
assert window.clip.options.start_frame==start and window.clip.options.end_frame==end
window.tabs.setCurrentIndex(window.tabs.count()-1);window.grab().save(str(out/'studio_export.png'))
assert not errors,errors
window.close();events()
from shiboken6 import delete
delete(window);events()
faulthandler.cancel_dump_traceback_later()
print('UI passed: analysis, face/eye preview, audible media track, playback frames, range, manual keys, project roundtrip, own-widget screenshots.')
