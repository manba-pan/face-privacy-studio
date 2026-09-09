"""Regression tests for resumed timelines and bounded, durable analysis caches."""
from pathlib import Path
import sys,os,tempfile,subprocess,threading,unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core,analysis_engine as engine
from analysis_cache import FaceStore,CHUNK,cache_path

class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='face-privacy-test-')
        cls.root=Path(cls.temp.name);cls.source=cls.root/'timeline.mp4'
        subprocess.run([core.ffmpeg(),'-v','error','-f','lavfi','-i','testsrc2=size=160x96:rate=30',
            '-frames:v','420','-c:v','libx264','-pix_fmt','yuv420p',str(cls.source)],check=True,creationflags=core.HIDDEN)
        cls.info=core.probe(str(cls.source))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_resume_matches_full_decoding(self):
        for hardware in ('cpu','auto'):
            with self.subTest(hardware=hardware):
                with core.Decoder(self.info,(80,48),hardware=hardware) as decoder:full=list(decoder)
                with core.Decoder(self.info,(80,48),hardware=hardware,start_frame=180) as decoder:tail=list(decoder)
                self.assertEqual(len(full),420);self.assertEqual(len(tail),240)
                self.assertTrue(all(np.array_equal(a,b) for a,b in zip(full[180:],tail)))
    def test_cancel_resume_cache_and_invalidation(self):
        class Detector:
            def __init__(self,*_):
                from types import SimpleNamespace
                self.yunet=SimpleNamespace(effective={'test'},notes=[],timings={});self.last_review_reason=None
            def detect(self,*_):return []
            def close(self):pass
        with patch.dict(os.environ,{'FACEPRIVACY_CACHE_DIR':str(self.root/'cache')}),patch.object(engine,'Detector',Detector):
            options=engine.AnalysisOptions(profile='fast',device='cpu',decode='cpu');cancel=threading.Event()
            def progress(_,index,*args):
                if index>=190:cancel.set()
            with self.assertRaises(core.Cancelled):engine.analyze(self.info,options,cancel,progress)
            path=cache_path(self.info,options);store=FaceStore(path)
            self.assertEqual(len(store),180);self.assertFalse(store.complete);store.close()
            result=engine.analyze(self.info,options)
            self.assertEqual(result['frames'],420);self.assertEqual(result['stats']['resumed_frames'],180)
            with patch.object(engine,'Detector',side_effect=AssertionError('Complete cache must skip model')):
                self.assertTrue(engine.analyze(self.info,options)['cache_hit'])
            options.profile='precise';self.assertNotEqual(path,cache_path(self.info,options))
            with self.assertRaises(ValueError):engine.validate_timeline(self.info,600)
    def test_bounded_cache_reads_and_partial_recovery(self):
        path=self.root/'bounded.sqlite';store=FaceStore(path,write=True)
        for block in range(9):
            face=np.full((40,2),block/10,np.float32)
            store.append([[face]]*CHUNK,[None]*CHUNK,{})
        store.append([[]]*7,['unseen']*7,{})
        store.close();store=FaceStore(path,write=True);store.discard_partial_tail()
        self.assertEqual(len(store),9*CHUNK)
        for i in range(9):self.assertTrue(np.allclose(store[i*CHUNK][0],i/10))
        self.assertLessEqual(len(store.chunks),6);self.assertEqual(store.review(),[]);store.close()

if __name__=='__main__':unittest.main(verbosity=2)
