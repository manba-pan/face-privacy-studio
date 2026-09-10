"""Preview correctness, bounded decoder reuse and rotated source compatibility."""
from pathlib import Path
import sys, os, tempfile, unittest, subprocess, threading, time, copy
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import av
import numpy as np
from PySide6.QtWidgets import QApplication
import core, exporter, native_export, playback, studio


def ff(args):
    result=subprocess.run([core.ffmpeg(),'-v','error','-nostdin',*map(str,args)],capture_output=True,creationflags=core.HIDDEN)
    if result.returncode:raise RuntimeError(result.stderr.decode('utf8','replace'))
    return result.stdout


class PreviewChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.tmp=tempfile.TemporaryDirectory(prefix='preview-check-');cls.root=Path(cls.tmp.name)
        cls.source=cls.root/'interframe.mp4';cls.rotated=cls.root/'rotated.mp4'
        ff(['-f','lavfi','-i','testsrc2=size=320x240:rate=30:duration=4',
            '-c:v','libx264','-g','120','-sc_threshold','0','-bf','3',cls.source])
        ff(['-display_rotation:v:0','90','-i',cls.source,'-c','copy',cls.rotated])
        cls.info=core.probe(str(cls.source));cls.media=exporter.details(cls.source)
        with av.open(str(cls.source)) as inp:
            cls.original=[f.to_ndarray(format='rgb24') for f in inp.decode(video=0)]

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def task(self,index=0,path=None,options=None,manual=None):
        info=self.info if path is None else core.probe(str(path))
        media=self.media if path is None else exporter.details(path)
        return (0,index,None,info,None,core.Settings(style='solid'),
                options or exporter.ExportOptions(profile='native',auto_mask=False),manual or [],False,320,media)

    def wait_for(self,predicate):
        deadline=time.monotonic()+15
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertTrue(predicate(),'Preview worker timed out')

    def test_01_persistent_decoder_exact_seek_and_unmodified_cached_frame(self):
        with patch.object(native_export.av,'open',wraps=av.open) as opened:
            reader=native_export.PreviewReader(self.info)
            try:
                for index in [0,1,2,15,21,119,118,1,0]:
                    manual=[{'start':0,'end':119,'rect':[.1,.1,.7,.7]}]
                    masked=reader.preview(index,[],core.Settings(style='solid'),manual,False,320)
                    raw=reader.preview(index,[],core.Settings(),[],False,320)
                    self.assertTrue(np.any(masked!=raw))
                    np.testing.assert_array_equal(raw,self.original[index],err_msg=f'frame {index}')
                self.assertEqual(opened.call_count,1)
                cancel=threading.Event();cancel.set()
                with self.assertRaises(core.Cancelled):reader.read(2,cancel)
            finally:reader.close()

    def test_02_native_worker_reuses_decoder_and_closes_without_next_task(self):
        renderer=playback.LatestRenderer();results=[];errors=[]
        renderer.events.ready.connect(results.append);renderer.events.failed.connect(lambda *e:errors.append(e))
        with patch.object(native_export.av,'open',wraps=av.open) as opened,patch.object(core,'read_frame',side_effect=AssertionError('Redundant RGB decoding')):
            try:
                for count,index in enumerate([0,1,2,10],1):
                    renderer.submit(self.task(index));self.wait_for(lambda:len(results)>=count or bool(errors))
                    self.assertFalse(errors)
                self.assertFalse(errors)
                self.assertEqual(opened.call_count,1)
                self.assertTrue(all(r['raw'] is None for r in results))
                renderer.discard(close_source=True)
                self.wait_for(lambda:renderer.native_reader is None)
            finally:renderer.close()
        self.assertFalse(renderer.thread.is_alive())

    def test_03_complete_source_compatibility_and_rotated_preview(self):
        media=exporter.details(self.rotated);info=core.probe(str(self.rotated))
        self.assertEqual((info.width,info.height),(240,320));self.assertTrue(media['rotation'])
        self.assertEqual(exporter.default_profile(media),'lossless')
        options=exporter.ExportOptions(profile='native',auto_mask=False)
        self.assertFalse(exporter.native_preview_compatible(media,options))
        with self.assertRaises(ValueError):exporter.validate(info,options,media)
        exporter.validate(info,exporter.ExportOptions(profile=exporter.default_profile(media)),media)
        for update in [{'sar':'2'},{'interlaced':True},{'hdr':True},{'alpha':True}]:
            self.assertEqual(exporter.default_profile(dict(self.media,**update)),'lossless')
        results=[];errors=[];renderer=playback.LatestRenderer()
        renderer.events.ready.connect(results.append);renderer.events.failed.connect(lambda *e:errors.append(e))
        try:
            # Even an old project containing an invalid native profile must
            # preview in the displayed orientation, with correct mask geometry.
            manual=[{'start':0,'end':119,'rect':[.1,.1,.4,.4]}]
            renderer.submit(self.task(path=self.rotated,options=options,manual=manual))
            self.wait_for(lambda:bool(results or errors));self.assertFalse(errors)
            im=results[0]['image'];self.assertEqual((im.width(),im.height()),(240,320))
            self.assertEqual(im.pixelColor(45,60).getRgb(),(16,16,16,255))
            self.assertIsNone(renderer.native_reader)
        finally:renderer.close()

    def test_04_color_cache_invalidates_and_never_reuses_converted_raw(self):
        results=[];renderer=playback.LatestRenderer();errors=[]
        renderer.events.ready.connect(results.append);renderer.events.failed.connect(lambda *e:errors.append(e))
        options=exporter.ExportOptions(profile='lossless',auto_mask=False,input_color='smpte170m',output_color='bt709')
        try:
            with patch.object(core,'read_frame',wraps=core.read_frame) as decode:
                for number,(index,color) in enumerate([(0,'bt709'),(0,'bt709'),(1,'bt709'),(1,'smpte170m')],1):
                    options=copy.copy(options);options.output_color=color
                    renderer.submit(self.task(index,options=options));self.wait_for(lambda:len(results)>=number or bool(errors))
                    self.assertFalse(errors)
                self.assertEqual(decode.call_count,3)
                self.assertTrue(all(r['raw'] is None for r in results))
                renderer.submit(self.task(options=exporter.ExportOptions(profile='lossless',auto_mask=False)))
                self.wait_for(lambda:len(results)==5 or bool(errors));self.assertFalse(errors)
                np.testing.assert_array_equal(results[-1]['raw'],core.read_frame(self.info,0,(320,240)))
        finally:renderer.close()

    def test_05_real_ui_import_and_project_roundtrip_choose_valid_profile(self):
        window=studio.Studio();errors=[];window.message=lambda text:errors.append(str(text))
        try:
            window.import_paths([str(self.rotated)])
            self.wait_for(lambda:not window.busy)
            self.assertFalse(errors);self.assertIsNone(window.clip.analysis)
            self.assertEqual(window.clip.options.profile,'lossless')
            self.assertTrue(window.rotation.isEnabled())
            data=window.project_data();self.assertEqual(data['clips'][0]['options']['profile'],'lossless')
            path=self.root/'rotated.privacy.json'
            import json
            path.write_text(json.dumps(data),encoding='utf8')
            window.load_project(str(path));self.assertFalse(errors)
            self.assertEqual(window.clip.options.profile,'lossless')
        finally:
            window.close();self.app.processEvents()
            from shiboken6 import delete
            delete(window);self.app.processEvents()


if __name__=='__main__':unittest.main(verbosity=2)
