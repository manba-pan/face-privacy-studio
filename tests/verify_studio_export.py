"""Output contracts: high fps/size, RGB lossless frames, original PCM and timing."""
from pathlib import Path
import sys,subprocess,unittest,time,json,threading,os
import faulthandler
faulthandler.dump_traceback_later(45,repeat=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core,exporter
import numpy as np

OUT=Path(__file__).resolve().parents[1]/'samples'/'verification'/'studio'
OUT.mkdir(exist_ok=True)
REPORT=[]

def ff(args):
    p=subprocess.run([core.ffmpeg(),'-v','error','-nostdin','-y',*map(str,args)],capture_output=True,creationflags=core.HIDDEN)
    if p.returncode:raise RuntimeError(p.stderr.decode('utf8','replace'))
    return p.stdout

def analysis(path):
    info=core.probe(str(path))
    return core.Analysis(info,[[] for _ in range(info.frames)],[],core.fingerprint(path))

def fresh(name):
    path=OUT/name
    path.unlink(missing_ok=True)
    return path

class ExportChecks(unittest.TestCase):
    def test_01_4k60_and_1080p120(self):
        for size,fps in [('3840x2160',60),('1920x1080',120)]:
            src=OUT/f'pattern_{fps}.mp4'
            ff(['-f','lavfi','-i',f'testsrc2=size={size}:rate={fps}:duration=0.25','-c:v','libx264','-preset','ultrafast','-crf','18',src])
            a=analysis(src);options=exporter.ExportOptions(audio='mute',auto_mask=False)
            start=time.monotonic();dest=fresh(f'quality_{fps}.mp4')
            exporter.export(a,dest,core.Settings(),options,manual=[{'start':0,'end':a.info.frames-1,'rect':[.25,.25,.4,.4]}])
            result=core.probe(str(dest));self.assertEqual((result.width,result.height),(a.info.width,a.info.height))
            self.assertEqual(result.frames,a.info.frames);self.assertAlmostEqual(result.fps,fps,places=2)
            self.assertAlmostEqual(core.media_duration(dest),.25,delta=.02)
            REPORT.append({'case':size+f' {fps}fps','frames':result.frames,'output_seconds':result.duration,'export_seconds':round(time.monotonic()-start,2)})
    def test_02_rgb_lossless_trim_rotation_keyframes(self):
        src=OUT/'rgb_source.mkv'
        ff(['-f','lavfi','-i','testsrc=size=320x240:rate=12:duration=1','-c:v','ffv1','-pix_fmt','bgr0',src])
        a=analysis(src);options=exporter.ExportOptions(profile='lossless',audio='mute',auto_mask=False,start_frame=2,end_frame=9,rotation=90)
        masks=[{'start':2,'end':8,'rect':[.1,.1,.3,.3],'keyframes':[{'frame':2,'rect':[.1,.1,.3,.3]},{'frame':8,'rect':[.6,.6,.9,.9]}]}]
        dest=fresh('lossless_rotation.mkv');settings=core.Settings(style='solid',missing='keep')
        exporter.export(a,dest,settings,options,masks)
        with core.Decoder(a.info) as dec:original=list(dec)
        with core.Decoder(core.probe(str(dest))) as dec:result=list(dec)
        self.assertEqual(len(result),7)
        for i,rgb in enumerate(result):
            expected=exporter.mask_render(original[i+2],[],settings,masks,i+2,options)
            np.testing.assert_array_equal(expected,rgb)
        np.testing.assert_allclose(core.manual_rect(masks[0],5),[.35,.35,.6,.6])
        REPORT.append({'case':'FFV1 RGB + trim + 90° + animated manual mask','frames_identical':7})
    def test_03_original_sony_pcm_copy(self):
        if not os.environ.get('FACEPRIVACY_ACCEPTANCE_VIDEO'):self.skipTest('Optional camera acceptance fixture not configured')
        src=Path(os.environ['FACEPRIVACY_ACCEPTANCE_VIDEO'])
        if not src.exists():self.skipTest('Optional camera acceptance fixture unavailable')
        a=analysis(src);options=exporter.ExportOptions(auto_mask=False)
        media=exporter.details(src);self.assertEqual(media['audio_codec'],'pcm_s16be');self.assertEqual(exporter.suffix_for(options,media),'.mov')
        dest=fresh('sony_audio_copy.mov');start=time.monotonic()
        exporter.export(a,dest,core.Settings(),options,media=media)
        raw=lambda path:ff(['-i',path,'-map','0:a:0','-f','s16le','-c:a','pcm_s16le','pipe:1'])
        original,output=raw(src),raw(dest)
        self.assertEqual(original,output)
        self.assertAlmostEqual(core.media_duration(dest),5.76,delta=.02)
        REPORT.append({'case':'Sony 1080p50 original PCM copy','audio_bytes_identical':len(output),'seconds':round(time.monotonic()-start,2)})
        options.start_frame=50;options.end_frame=150
        dest=fresh('sony_trim_copy.mov');exporter.export(a,dest,core.Settings(),options,media=media)
        output=raw(dest);self.assertAlmostEqual(len(output)/(48000*4),2,delta=.03)
        # PCM sample-aligned trim must be the corresponding source interval.
        self.assertEqual(output,original[48000*4:48000*4*3])
    def test_04_10bit_sdr_and_prores(self):
        src=OUT/'10bit_sdr.mov'
        ff(['-f','lavfi','-i','testsrc2=size=320x240:rate=60:duration=0.1','-c:v','prores_ks','-profile:v','3','-pix_fmt','yuv422p10le',src])
        a=analysis(src);media=exporter.details(src);self.assertTrue(media['ten_bit'])
        with self.assertRaisesRegex(ValueError,'8 位'):exporter.validate(a.info,exporter.ExportOptions(),media)
        for profile in ('prores','lossless'):
            options=exporter.ExportOptions(profile=profile,audio='mute',auto_mask=False)
            dest=fresh('10bit_'+profile+exporter.suffix_for(options,media))
            exporter.export(a,dest,core.Settings(),options,[{'start':0,'end':5,'rect':[.2,.2,.5,.5]}],media=media)
            self.assertTrue(exporter.details(dest)['ten_bit']);self.assertEqual(core.probe(str(dest)).frames,6)
        REPORT.append({'case':'10-bit SDR ProRes/FFV1','high_bit_depth_retained':True})
    def test_05_cancel_and_invalid_settings(self):
        src=OUT/'pattern_120.mp4';a=analysis(src);options=exporter.ExportOptions(audio='mute',auto_mask=False)
        cancel=threading.Event();cancel.set();dest=fresh('cancel.mp4')
        with self.assertRaises(core.Cancelled):exporter.export(a,dest,core.Settings(),options,cancel=cancel)
        self.assertFalse(dest.exists())
        options.fps=240
        with self.assertRaisesRegex(ValueError,'帧率'):exporter.validate(a.info,options,exporter.details(src))
        options.fps=0
        with self.assertRaisesRegex(ValueError,'HDR'):exporter.validate(a.info,options,{'hdr':True})
    def test_06_aac_volume_and_fps_reduction(self):
        src=Path(__file__).resolve().parents[1]/'samples'/'测试采访_带声音.mp4'
        a=analysis(src);options=exporter.ExportOptions(audio='aac',volume=.5,auto_mask=False,start_frame=6,end_frame=30,fps=6)
        dest=fresh('aac_trim.mp4');exporter.export(a,dest,core.Settings(),options)
        self.assertAlmostEqual(core.media_duration(dest),2,delta=.02);self.assertEqual(core.probe(str(dest)).fps,6)
        pcm=ff(['-i',dest,'-map','0:a:0','-f','f32le','-ac','1','-ar','48000','pipe:1'])
        values=np.frombuffer(pcm,np.float32)
        self.assertAlmostEqual(len(values)/48000,2,delta=.03)
        self.assertLess(np.max(np.abs(values)),.09)
        self.assertLess(np.max(np.abs(values[round(1.2*48000):])),.002)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ExportChecks)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (OUT/'export_report.json').write_text(json.dumps({'passed':result.wasSuccessful(),'checks':REPORT},ensure_ascii=False,indent=2),encoding='utf8')
    sys.exit(not result.wasSuccessful())
