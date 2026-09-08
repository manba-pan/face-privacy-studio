"""Functional verification with a moving face, silent tail, VFR and cancellation."""
from pathlib import Path
import dataclasses
import json
import os
import subprocess
import sys
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core
import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont

SAMPLES=Path(__file__).resolve().parents[1]/'samples'
RESULTS=SAMPLES/'verification'
RESULTS.mkdir(exist_ok=True)


def run_ffmpeg(args):
    p=subprocess.run([core.ffmpeg(),'-hide_banner','-loglevel','error','-y',*args],capture_output=True,creationflags=core.HIDDEN)
    if p.returncode:
        raise RuntimeError(p.stderr.decode('utf8','replace'))


def fixture():
    photo=cv2.cvtColor(cv2.imread(str(SAMPLES/'portrait.jpg')),cv2.COLOR_BGR2RGB)
    photo=cv2.resize(photo,(400,480))
    source=SAMPLES/'测试采访_带声音.mp4'
    p=subprocess.Popen([core.ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','rawvideo',
                        '-pix_fmt','rgb24','-s','640x480','-r','12','-i','pipe:0',
                        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1.5',
                        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',
                        '-c:a','aac',str(source)],stdin=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=core.HIDDEN)
    for i in range(48):
        rgb=np.full((480,640,3),36,np.uint8)
        if i<36:
            x=80+round(55*np.sin(i/10))
            rotated=cv2.warpAffine(photo,cv2.getRotationMatrix2D((200,240),8*np.sin(i/9),1),(400,480),borderValue=(36,36,36))
            rgb[:,x:x+400]=rotated
        p.stdin.write(rgb.tobytes())
    p.stdin.close()
    errors=p.stderr.read()
    p.stderr.close()
    if p.wait():
        raise RuntimeError(errors.decode())
    return source


class Verification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=fixture()
        cls.analysis=core.analyze(core.probe(str(cls.source)))

    @classmethod
    def tearDownClass(cls):
        cls.analysis.close()

    def test_01_real_face_and_missing_segments(self):
        a=self.analysis
        self.assertEqual(len(a.faces),48)
        self.assertGreaterEqual(sum(bool(f) for f in a.faces[:36]),34)
        self.assertTrue(all(not f for f in a.faces[36:]))
        self.assertTrue(a.review)

    def test_02_regions_and_strength(self):
        rgb=core.read_preview(self.analysis,10)
        faces=self.analysis.faces[10]
        masks={}
        tiles=[]
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
        for region,label in [('full','整脸'),('upper','上半脸'),('lower','下半脸'),('eyes','眼睛')]:
            settings=core.Settings(region=region,style='solid',missing='keep')
            output=core.render_frame(rgb,faces,settings)
            masks[region]=np.any(output!=rgb,axis=2)
            self.assertTrue(masks[region].any())
            settings.style='mosaic'
            output=core.render_frame(rgb,faces,settings)
            tile=Image.new('RGB',(640,525),'#17253b')
            tile.paste(Image.fromarray(output),(0,45))
            ImageDraw.Draw(tile).text((18,10),label,font=font,fill='white')
            tiles.append(tile)
        self.assertLess(masks['eyes'].sum(),masks['full'].sum()*.65)
        self.assertLess(masks['upper'].sum(),masks['full'].sum()*.75)
        self.assertLess(masks['lower'].sum(),masks['full'].sum()*.75)
        sheet=Image.new('RGB',(1280,1050))
        for i,tile in enumerate(tiles):
            sheet.paste(tile,((i%2)*640,(i//2)*525))
        sheet.save(RESULTS/'regions.png')
        poly=core.face_polygon(faces[0],640,480,core.Settings())
        for style in ['mosaic','blur']:
            low=core.render_frame(rgb,faces,core.Settings(style=style,strength=1))
            high=core.render_frame(rgb,faces,core.Settings(style=style,strength=5))
            self.assertFalse(np.array_equal(low,high))
        unchanged=core.render_frame(rgb,[],core.Settings())
        self.assertTrue(np.array_equal(unchanged,rgb))
        black=core.render_frame(rgb,[],core.Settings(missing='full_frame'))
        self.assertTrue(np.all(black==16))

    def test_03_manual_region_timing(self):
        rgb=np.full((100,100,3),200,np.uint8)
        settings=core.Settings(style='solid',missing='keep')
        boxes=[{'start':3,'end':5,'rect':[.2,.3,.5,.6]}]
        self.assertTrue(np.array_equal(rgb,core.render_frame(rgb,[],settings,boxes,2)))
        out=core.render_frame(rgb,[],settings,boxes,3)
        self.assertTrue(np.all(out[35:55,25:45]==16))
        self.assertTrue(np.all(out[:20]==200))
        self.assertTrue(np.array_equal(rgb,core.render_frame(rgb,[],settings,boxes,6)))

    def test_04_export_audio_and_all_frames(self):
        dest=RESULTS/'测试成片_保留声音.mp4'
        dest.unlink(missing_ok=True)
        core.export_video(self.analysis,dest,core.Settings())
        with core.Decoder(core.probe(str(dest))) as decoder:
            frames=list(decoder)
        self.assertEqual(len(frames),48,'Short audio must not truncate video')
        self.assertLess(float(frames[-1].std()),2)
        self.assertGreater(float(frames[0].std()),10)
        p=subprocess.run([core.ffmpeg(),'-v','error','-i',str(dest),'-map','0:a:0','-f','s16le','-ac','1','-ar','8000','pipe:1'],capture_output=True,creationflags=core.HIDDEN)
        self.assertEqual(p.returncode,0,p.stderr)
        audio=np.frombuffer(p.stdout,np.int16)
        self.assertGreater(len(audio)/8000,3.9)
        self.assertLess(len(audio)/8000,4.1,'Audio padding must be bounded on both ends')
        self.assertAlmostEqual(core.media_duration(dest),4.,delta=.06)
        self.assertGreater(float(audio[:8000].std()),100)

    def test_05_cancel_and_source_protection(self):
        cancel=threading.Event()
        cancel.set()
        dest=RESULTS/'cancelled.mp4'
        dest.unlink(missing_ok=True)
        with self.assertRaises(core.Cancelled):
            core.export_video(self.analysis,dest,core.Settings(),cancel=cancel)
        self.assertFalse(dest.exists())
        self.assertFalse(list(RESULTS.glob('.redaction-*.mp4')))
        with self.assertRaises(ValueError):
            core.export_video(self.analysis,self.source,core.Settings())
        self.assertTrue(self.source.exists())

    def test_06_vfr_and_silent_source(self):
        vfr=RESULTS/'变帧率_无声.mp4'
        run_ffmpeg(['-i',str(self.source),'-an','-vf',"select='if(lt(n,24),not(mod(n,2)),1)'",'-fps_mode','vfr','-c:v','libx264',str(vfr)])
        a=core.analyze(core.probe(str(vfr)))
        try:
            out=RESULTS/'变帧率_成片.mp4'
            out.unlink(missing_ok=True)
            core.export_video(a,out,core.Settings(region='eyes'))
            output=core.probe(str(out))
            self.assertLess(abs(output.duration-a.info.duration),.15)
            self.assertGreater(output.duration,3.7)
            self.assertAlmostEqual(core.media_duration(out),a.info.duration,delta=.12)
        finally:
            a.close()

    def test_07_phone_rotation(self):
        rotated=RESULTS/'手机旋转.mp4'
        run_ffmpeg(['-display_rotation','90','-i',str(self.source),'-c','copy',str(rotated)])
        info=core.probe(str(rotated))
        self.assertEqual((info.width,info.height),(480,640))
        with core.Decoder(info) as decoder:
            first=next(iter(decoder))
        self.assertEqual(first.shape,(640,480,3))

    def test_08_detected_face_survives_failed_landmarks(self):
        from types import SimpleNamespace
        rgb=core.read_preview(self.analysis,10)
        detector=core.Detector()
        engine=detector.engine
        detector.engine=SimpleNamespace(detect=lambda img:SimpleNamespace(face_landmarks=[]),close=lambda:None)
        try:
            faces=detector.detect(rgb,0)
            self.assertGreaterEqual(len(faces),1)
            self.assertIn('五官定位不稳',detector.last_review_reason)
            out=core.render_frame(rgb,faces,core.Settings(style='solid'))
            self.assertFalse(np.array_equal(out,rgb))
        finally:
            engine.close()
            detector.close()


if __name__=='__main__':
    unittest.main(verbosity=2)
