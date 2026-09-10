"""Real codec round-trips: native sample preservation, masks, tags and UI state."""
from pathlib import Path
import sys, subprocess, tempfile, unittest, os, threading, copy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import av
import numpy as np
from unittest.mock import patch
import core, exporter, native_export, color_management as colors


def ff(args):
    result = subprocess.run([core.ffmpeg(), '-v','error','-nostdin','-y',*map(str,args)], capture_output=True, creationflags=core.HIDDEN)
    if result.returncode: raise RuntimeError(result.stderr.decode('utf8','replace'))
    return result.stdout


def planes(path):
    with av.open(str(path)) as inp:
        return [[native_export.plane_array(p, max(c.bits for c in f.format.components))[1].copy() for p in f.planes]
                for f in inp.decode(video=0)]


def analysis(path):
    info = core.probe(str(path))
    return core.Analysis(info,[[] for _ in range(info.frames)],[],core.fingerprint(path))


class ColorChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='color-check-')
        self.root = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def fixture(self, fmt='yuv420p', space='bt709', value_range='tv', audio=False, odd=False):
        src=self.root/f'{fmt}-{space}-{value_range}.mkv'
        args=['-f','lavfi','-i',('testsrc=size=161x121' if odd else 'testsrc2=size=160x120')+':rate=12:duration=0.5']
        if audio:
            args += ['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.5',
                     '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=0.5',
                     '-map','0:v','-map','1:a','-map','2:a','-c:a','pcm_s16le']
        args += ['-vf',f'setparams=color_primaries={space}:color_trc={space}:colorspace={space}:range={value_range}',
                 '-c:v','ffv1','-pix_fmt',fmt,'-color_primaries',space,'-color_trc',space,
                 '-colorspace',space,'-color_range',value_range,src]
        ff(args)
        return src
    def test_01_native_roundtrip(self):
        for fmt in ('yuv420p','yuv422p10le','yuv444p16le','gbrp10le'):
            with self.subTest(fmt=fmt):
                src=self.fixture(fmt)
                a=analysis(src);dest=self.root/f'out-{fmt}.mkv'
                exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'))
                before,after=planes(src),planes(dest)
                self.assertEqual(len(before),len(after))
                for b,f in zip(before,after):
                    for x,y in zip(b,f):np.testing.assert_array_equal(x,y)
                self.assertEqual(colors.probe(src)['color'],colors.probe(dest)['color'])
    def test_02_mask_outside_unchanged(self):
        for style in ('solid','blur','mosaic'):
            src=self.fixture('yuv420p10le');a=analysis(src)
            manual=[{'start':0,'end':5,'rect':[.2,.2,.55,.55]}]
            options=exporter.ExportOptions(profile='native',auto_mask=False,audio='mute')
            settings=core.Settings(style=style)
            dest=self.root/f'mask-{style}.mkv'
            exporter.export(a,dest,settings,options,manual)
            mask=native_export.mask_for(a.info.width,a.info.height,[],settings,manual,0,False)
            changed=0
            for before,after in zip(planes(src),planes(dest)):
                for b,f in zip(before,after):
                    selected=native_export.plane_mask(mask,b.shape[1],b.shape[0])!=0
                    np.testing.assert_array_equal(b[~selected],f[~selected])
                    changed+=np.count_nonzero(b[selected]!=f[selected])
            self.assertGreater(changed,0)
    def test_03_all_audio_copied(self):
        src=self.fixture(audio=True);a=analysis(src);dest=self.root/'audio.mkv'
        exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False))
        self.assertEqual(colors.probe(dest)['audio_tracks'],2)
        for track in range(2):
            def samples(p):return ff(['-i',p,'-map',f'0:a:{track}','-f','s16le','pipe:1'])
            self.assertEqual(samples(src),samples(dest))
    def test_04_conversion_is_real(self):
        src=self.fixture('yuv444p10le','smpte170m');a=analysis(src)
        options=exporter.ExportOptions(profile='lossless',auto_mask=False,audio='mute',output_color='bt709')
        dest=self.root/'converted.mkv'
        exporter.export(a,dest,core.Settings(),options)
        # Independent explicit reference; does not call the application's filter builder.
        reference='scale=in_range=tv:out_range=tv:out_color_matrix=smpte170m,format=yuv444p16le,colorspace=iall=smpte170m:irange=tv:all=bt709:range=tv:format=yuv444p12,scale=in_color_matrix=bt709:in_range=tv:out_range=pc'
        expected=ff(['-i',src,'-vf',reference,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1'])
        actual=ff(['-i',dest,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1'])
        self.assertTrue(expected == actual, 'Converted samples differ from reference')
        unchanged=ff(['-i',src,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1'])
        self.assertTrue(unchanged != actual, 'Conversion must change actual samples')
        self.assertEqual(colors.probe(dest)['color']['color_primaries'],1)
    def test_05_source_tags_and_full_range(self):
        for value_range in ('pc','tv'):
            src=self.fixture('yuv444p','smpte170m',value_range);a=analysis(src)
            dest=self.root/f'preserved-{value_range}.mp4'
            exporter.export(a,dest,core.Settings(),exporter.ExportOptions(auto_mask=False,audio='mute'))
            tags=colors.probe(dest)['color']
            self.assertEqual(tags['color_primaries'],6)
            self.assertEqual(tags['color_trc'],6)
            self.assertEqual(tags['colorspace'],6)
            self.assertEqual(tags['color_range'],2 if value_range=='pc' else 1)
    def test_06_guardrails_and_cancel(self):
        src=self.fixture();a=analysis(src);media=colors.probe(src)
        for kwargs in ({'fps':6},{'rotation':90},{'output_color':'bt709'},{'input_range':'pc'},{'start_frame':1},{'audio':'aac'}):
            with self.assertRaises(ValueError):exporter.validate(a.info,exporter.ExportOptions(profile='native',**kwargs),media)
        hdr=copy.deepcopy(media);hdr['hdr']=True
        with self.assertRaisesRegex(ValueError,'HDR'):exporter.validate(a.info,exporter.ExportOptions(),hdr)
        cancel=threading.Event();cancel.set();dest=self.root/'cancel.mkv'
        with self.assertRaises(core.Cancelled):exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False),cancel=cancel)
        self.assertFalse(dest.exists());self.assertFalse(list(self.root.glob('.native-*')))
    def test_07_vfr_no_dropped_frames(self):
        src=self.fixture();vfr=self.root/'vfr.mkv'
        ff(['-i',src,'-vf',"setpts='if(lt(N,3),N/(12*TB),(N+2)/(12*TB))'",'-fps_mode','vfr','-c:v','ffv1',vfr])
        a=analysis(vfr);dest=self.root/'vfr-out.mkv'
        exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'))
        def pts(p):
            with av.open(str(p)) as inp:return [float(f.pts*f.time_base) for f in inp.decode(video=0)]
        np.testing.assert_allclose(pts(vfr),pts(dest),atol=.001)
        self.assertEqual(len(planes(vfr)),len(planes(dest)))
        with self.assertRaisesRegex(ValueError,'时间线'):
            exporter.export(a,self.root/'bad-vfr.mkv',core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'),[{'start':0,'end':5,'rect':[.1,.1,.5,.5]}])
        self.assertFalse((self.root/'bad-vfr.mkv').exists())
        self.assertFalse(list(self.root.glob('.native-*')))
    def test_08_preview_matches_native(self):
        src=self.fixture('yuv420p10le');a=analysis(src);settings=core.Settings(style='mosaic')
        manual=[{'start':0,'end':5,'rect':[.15,.1,.55,.6]}]
        dest=self.root/'preview.mkv'
        exporter.export(a,dest,settings,exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'),manual)
        preview=native_export.preview(a.info,0,[],settings,manual,False,160)
        with av.open(str(dest)) as inp: expected=next(inp.decode(video=0)).to_ndarray(format='rgb24')
        np.testing.assert_array_equal(preview,expected)

    def test_09_odd_dimensions_and_unknown_tags(self):
        src=self.fixture('yuv444p16le',odd=True);a=analysis(src)
        dest=self.root/'odd.mkv'
        exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'))
        self.assertEqual((core.probe(str(dest)).width,core.probe(str(dest)).height),(161,121))
        unknown=self.root/'unknown.mkv'
        ff(['-f','lavfi','-i','testsrc2=size=160x120:rate=12:duration=0.5','-c:v','ffv1',unknown])
        a=analysis(unknown);media=colors.probe(unknown)
        self.assertEqual(media['color']['color_primaries'],2)
        self.assertIn('Rec.709',colors.policy_note(media,exporter.ExportOptions()))
        dest=self.root/'unknown-out.mkv'
        exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'))
        self.assertEqual(colors.probe(dest)['color'],media['color'])

    def test_10_aac_copy_and_cancel_during_work(self):
        src=self.fixture(audio=True);aac=self.root/'aac.mp4'
        ff(['-i',src,'-map','0','-c:v','libx264','-c:a','aac',aac])
        a=analysis(aac);dest=self.root/'aac-native.mkv'
        exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False))
        self.assertEqual(colors.probe(dest)['audio_tracks'],2)
        self.assertIn('AAC',colors.policy_note(colors.probe(aac),exporter.ExportOptions(profile='native')))
        # AAC packets survive, but MP4 edit-list priming is not a sample-exact
        # Matroska contract. Keep this known boundary visible to the user.
        raw=lambda p:ff(['-i',p,'-map','0:a:0','-f','s16le','pipe:1'])
        original,result=raw(aac),raw(dest)
        self.assertTrue(result[-len(original):] == original)
        cancel=threading.Event();dest=self.root/'cancel-mid.mkv'
        with self.assertRaises(core.Cancelled):
            exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False),cancel=cancel,progress=lambda *_:cancel.set())
        self.assertFalse(dest.exists());self.assertFalse(list(self.root.glob('.native-*')))

    def interframe_fixture(self, codec='libx264', fmt='yuv420p'):
        src=self.root/f'interframe-{codec}.mp4'
        args=['-f','lavfi','-i','testsrc2=size=320x240:rate=30:duration=2',
              '-c:v',codec,'-pix_fmt',fmt,'-g','120','-bf','3']
        if codec=='libx265':args+=['-x265-params','log-level=error:pools=2:frame-threads=1:scenecut=0']
        else:args+=['-sc_threshold','0']
        ff([*args,src])
        return src

    def test_11_interframe_masks_preserve_other_frames_and_outside_pixels(self):
        for codec,fmt in [('libx264','yuv420p'),('libx265','yuv420p10le')]:
            with self.subTest(codec=codec):
                src=self.interframe_fixture(codec,fmt);a=analysis(src)
                settings=core.Settings(style='solid')
                manual=[{'start':0,'end':0,'rect':[.2,.2,.6,.6]},
                        {'start':25,'end':30,'rect':[.65,.1,.9,.4]}]
                dest=self.root/f'interframe-{codec}-out.mkv'
                exporter.export(a,dest,settings,exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'),manual)
                # Independently decode the original, not the frames the exporter
                # modified. Both codecs use references across masked frames.
                before,after=planes(src),planes(dest)
                self.assertEqual(len(before),len(after))
                changed=False
                for index,(b,f) in enumerate(zip(before,after)):
                    mask=native_export.mask_for(a.info.width,a.info.height,[],settings,manual,index,False)
                    for x,y in zip(b,f):
                        outside=native_export.plane_mask(mask,x.shape[1],x.shape[0])==0
                        np.testing.assert_array_equal(x[outside],y[outside],err_msg=f'{codec} frame {index}')
                        changed|=bool(np.any(x!=y))
                self.assertTrue(changed)
                self.assertEqual(a.stats['native_source_verified_frames'],len(before))

    def test_12_independent_verification_rejects_unmasked_corruption(self):
        src=self.interframe_fixture();a=analysis(src);dest=self.root/'corrupted.mkv'
        render=native_export.render
        def corrupt(frame,mask,settings):
            frame=render(frame,mask,settings);frame.make_writable()
            # Simulate an accidental write outside any requested effect. The
            # export's expected hash includes this write and cannot catch it.
            values=native_export.plane_array(frame.planes[0],8)[1]
            values[0,0]^=1
            return frame
        with patch.object(native_export,'render',corrupt):
            with self.assertRaisesRegex(RuntimeError,'未遮挡区域'):
                exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'))
        self.assertFalse(dest.exists());self.assertFalse(list(self.root.glob('.native-*')))

    def test_13_cancel_during_independent_source_verification(self):
        src=self.fixture();a=analysis(src);dest=self.root/'cancel-verify.mkv';cancel=threading.Event()
        def progress(value,*_):
            if value>.8:cancel.set()
        with self.assertRaises(core.Cancelled):
            exporter.export(a,dest,core.Settings(),exporter.ExportOptions(profile='native',auto_mask=False,audio='mute'),cancel=cancel,progress=progress)
        self.assertFalse(dest.exists());self.assertFalse(list(self.root.glob('.native-*')))


if __name__=='__main__':unittest.main(verbosity=2)
