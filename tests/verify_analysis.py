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

    def test_no_ghost_mask_after_face_moves_or_disappears(self):
        gray=np.zeros((200,300),np.uint8)
        row=np.array([30,30,40,60,42,50,58,50,50,65,43,78,57,78,.9],np.float32)
        previous=engine.Detector.fallback_geometry(row,300,200)
        current=previous+np.array([.4,0],np.float32)
        tracker=engine.Tracker();tracker.update(gray,[previous],[],5,True)
        # Reproduce a real face at a new location with an old prediction on
        # the background: the detector found one person, not two.
        faces,_=tracker.update(gray,[current],[(previous,1)],5,True)
        self.assertEqual(len(faces),1);self.assertTrue(np.array_equal(faces[0],current))
        faces,_=tracker.update(gray,[],[(current,1)],5,True)
        self.assertEqual(faces,[],'A lost face must not persist over hands/body')

    def test_extra_model_candidates_need_confirmation(self):
        rgb=np.zeros((200,300,3),np.uint8)
        primary=np.array([110,-5,50,65,122,12,146,12,134,26,125,40,145,40,.68],np.float32)
        hand=np.array([30,100,40,55,40,116,56,116,48,125,41,140,55,140,.84],np.float32)
        detector=object.__new__(engine.Detector)
        detector.options=engine.AnalysisOptions(profile='precise')
        detector.primary_rows=[primary];detector.locate=lambda _:[primary,hand]
        detector.landmarks=lambda *_:None
        detector.confirm_fallback=lambda _rgb,row:row is primary
        faces=detector.detect(rgb,0)
        self.assertEqual(len(faces),1,'Unconfirmed extra-model hand candidate was masked')
        self.assertLess(engine.bounds(faces[0])[1],0,'Partial face at frame edge was discarded')
        rendered=core.render_frame(np.full_like(rgb,200),faces,core.Settings(style='solid'))
        self.assertTrue(np.all(rendered[100:155,30:70]==200))

    def test_verified_mesh_and_single_coverage_margin(self):
        row=np.array([100,50,60,80,115,75,145,75,130,95,118,110,142,110,.9],np.float32)
        mesh=engine.Detector.fallback_geometry(row,300,200)
        detector=object.__new__(engine.Detector);detector.options=engine.AnalysisOptions(profile='precise')
        oversized=row.copy();oversized[:4]=[70,30,120,150]
        detector.primary_rows=[oversized];detector.locate=lambda _:[oversized];detector.landmarks=lambda *_:mesh
        rgb=np.full((200,300,3),200,np.uint8);faces=detector.detect(rgb,0)
        result=core.render_frame(rgb,faces,core.Settings(style='solid'))
        self.assertTrue(np.any(result[70:110,110:150]!=200),'Actual face was not covered')
        self.assertTrue(np.all(result[142:160,115:145]==200),'Neck below confirmed chin was masked by an extra enlarged box')

    def test_nearby_real_face_cannot_validate_a_false_box(self):
        from types import SimpleNamespace as Obj
        row=np.array([100,50,60,80,115,75,145,75,130,95,118,110,142,110,.88],np.float32)
        detector=object.__new__(engine.Detector);detector.primary_rows=[row,row.copy()]
        detector.mp=Obj(Image=lambda **_:None,ImageFormat=Obj(SRGB=0))
        detector.crop=lambda *_:(np.zeros((320,320,3),np.uint8),0,0,320)
        hit=Obj(categories=[Obj(score=.99)],bounding_box=Obj(origin_x=220,origin_y=50,width=60,height=80))
        detector.verifier=Obj(detect=lambda _:Obj(detections=[hit]))
        rgb=np.zeros((320,320,3),np.uint8)
        self.assertFalse(detector.confirm_fallback(rgb,row),'Nearby face validated unrelated hand/shoulder candidate')
        hit.bounding_box.origin_x=100
        self.assertTrue(detector.confirm_fallback(rgb,row),'Aligned independent detector confirmation was ignored')

    def test_partial_face_at_top_edge_is_not_filtered_with_background(self):
        from types import SimpleNamespace as Obj
        row=np.array([100,0.4,54,67,111,14,132,15,120,27,110,44,129,45,.888],np.float32)
        detector=object.__new__(engine.Detector);detector.primary_rows=[row,row.copy()]
        detector.mp=Obj(Image=lambda **_:None,ImageFormat=Obj(SRGB=0))
        detector.verifier=Obj(detect=lambda _:Obj(detections=[]))
        rgb=np.zeros((480,852,3),np.uint8)
        self.assertTrue(detector.confirm_fallback(rgb,row),'Confirmed partial edge face disappeared')
        background=row.copy();background[5]=6
        detector.primary_rows=[background,background.copy()]
        self.assertFalse(detector.confirm_fallback(rgb,background),'Edge proximity alone confirmed a background box')

if __name__=='__main__':unittest.main(verbosity=2)
