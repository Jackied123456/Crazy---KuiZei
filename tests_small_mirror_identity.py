"""Owner enrollment, identity confusion, encryption and resizing regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

import small_mirror_identity as identity
import small_mirror_core as core
import small_mirror as app

HERE=Path(__file__).resolve().parent

def feature(index):
    result=np.zeros(128,dtype=np.float32);result[index]=1
    return result

class Recognizer:
    def feature(self,frame,item):return item.get('test_feature')
    def enrollment_feature(self,frame,item):return self.feature(frame,item),''

def face(x=20,owner=True):
    return dict(bbox=(x,20,80,90),confidence=.99,
                test_feature=feature(0 if owner else 1))

class IdentityTests(unittest.TestCase):
    def test_rejects_invalid_vectors(self):
        for value in ([],[0]*128,[float('nan')]*128,[1]*127):
            with self.assertRaises(ValueError):identity.unit_feature(value)

    def test_two_frames_verify_owner_and_other_independently(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        for now in (0,.2):
            faces=tracker.update(None,[face(),face(140,False)],now)
        self.assertEqual([item['identity'] for item in faces],['owner','other'])
        self.assertEqual(len(tracker.owner_boxes(.2)),1)

    def test_owner_motion_keeps_same_track(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        ids=[]
        for now,x in ((0,20),(.2,24),(.4,30),(.6,36)):
            item=tracker.update(None,[face(x)],now)[0];ids.append(item['track_id'])
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(tracker.owner_boxes(.6),[(36,20,80,90)])

    def test_low_quality_owner_bridge_expires(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face()],0);tracker.update(None,[face()],.2)
        missing=face();missing['test_feature']=None
        self.assertEqual(tracker.update(None,[missing],.71)[0]['identity'],'owner')
        missing=face();missing['test_feature']=None
        self.assertEqual(tracker.update(None,[missing],1.02)[0]['identity'],'uncertain')

    def test_other_replaces_owner_at_same_position_never_inherits_owner_on_recheck(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face()],0);tracker.update(None,[face()],.2)
        item=tracker.update(None,[face(20,False)],.71)[0]
        self.assertNotEqual(item['identity'],'owner')
        item=tracker.update(None,[face(20,False)],.9)[0]
        self.assertEqual(item['identity'],'other')

    def test_crossing_forces_verification_even_inside_cache_interval(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face(),face(140,False)],0)
        tracker.update(None,[face(),face(140,False)],.2)
        faces=tracker.update(None,[face(20,False),face(60,True)],.3)
        self.assertFalse(any(item['identity']=='owner' and np.array_equal(item['test_feature'],feature(1)) for item in faces))

    def test_long_departure_loses_owner_id_and_requires_new_verification(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face()],0)
        old=tracker.update(None,[face()],.2)[0]['track_id']
        tracker.update(None,[],1.1)
        new=tracker.update(None,[face()],1.2)[0]
        self.assertNotEqual(old,new['track_id'])
        self.assertEqual(new['identity'],'uncertain')
        self.assertEqual(tracker.update(None,[face()],1.4)[0]['identity'],'owner')

    def test_simultaneous_owner_matches_are_ambiguous(self):
        tracker=identity.OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face(),face(140)],0)
        faces=tracker.update(None,[face(),face(140)],.2)
        self.assertEqual([item['identity'] for item in faces],['uncertain','uncertain'])
        self.assertEqual(tracker.owner_boxes(.2),[])

    def test_no_template_never_claims_owner(self):
        tracker=identity.OwnerTracker(Recognizer())
        self.assertEqual(tracker.update(None,[face()],0)[0]['identity'],'unregistered')

    def test_enrollment_requires_explicit_capture_each_angle(self):
        enrollment=identity.Enrollment(Recognizer(),7)
        item=face();item['track_id']=7
        now=0
        for stage in range(4):
            if stage:enrollment.capture()
            for _ in range(3):
                enrollment.update(None,[item],now);now+=.35
            self.assertEqual(enrollment.stage,stage+1)
            enrollment.update(None,[item],now)
            self.assertEqual(len(enrollment.samples),3*(stage+1))
        self.assertTrue(enrollment.status()['done'])

    def test_enrollment_rejects_different_person_and_lost_track(self):
        enrollment=identity.Enrollment(Recognizer(),7)
        item=face();item['track_id']=7
        enrollment.update(None,[item],0)
        item=face(owner=False);item['track_id']=7
        enrollment.update(None,[item],.4)
        self.assertEqual(len(enrollment.samples),1)
        self.assertIn('不像',enrollment.message)
        enrollment.update(None,[],.8)
        self.assertIn('没跟上',enrollment.message)

    def test_real_dpapi_store_roundtrip_and_delete_contains_no_plain_features(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='identity-') as directory:
            store=identity.OwnerStore(Path(directory)/'owner.bin')
            store.save([feature(0)]*3)
            encrypted=store.path.read_bytes()
            self.assertTrue(encrypted.startswith(store.MAGIC))
            self.assertNotIn(b'"samples"',encrypted)
            self.assertNotIn(identity.MODEL.encode(),encrypted)
            self.assertTrue(np.allclose(store.load()[0],feature(0)))
            store.delete();self.assertEqual(store.load(),[])

    def test_encryption_failure_does_not_replace_previous_template(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='identity-') as directory:
            store=identity.OwnerStore(Path(directory)/'owner.bin')
            store.save([feature(0)]*3);previous=store.path.read_bytes()
            with patch.object(identity,'dpapi',side_effect=OSError('injected failure')):
                with self.assertRaises(OSError):store.save([feature(1)]*3)
            self.assertEqual(previous,store.path.read_bytes())

    def test_invalid_template_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='identity-') as directory:
            store=identity.OwnerStore(Path(directory)/'owner.bin');store.path.write_bytes(b'bad')
            with self.assertRaises(ValueError):store.load()
            self.assertEqual(store.path.read_bytes(),b'bad')

    def test_owner_redaction_is_local_and_does_not_filter_neighbor_face(self):
        frame=np.full((200,400,3),200,dtype=np.uint8)
        owner=(30,60,80,80);other=(180,60,80,80)
        result=core.redact_owner(frame,(0,0,400,200),[owner])
        self.assertTrue(np.all(result[60:140,30:110]==0))
        self.assertTrue(np.all(result[60:140,180:260]==200))
        self.assertEqual(core.matching_region(other,400,200,[(0,0,1,1)]),0)
        self.assertTrue(np.all(frame==200))

class ResizeTests(unittest.TestCase):
    def test_four_corners_keep_opposite_corner_anchored(self):
        rect=(100,100,360,280);bounds=(0,0,1920,1080)
        self.assertEqual(app.resized_rect(rect,'se',100,50,bounds),(100,100,460,330))
        self.assertEqual(app.resized_rect(rect,'nw',-100,-50,bounds),(0,50,460,330))
        self.assertEqual(app.resized_rect(rect,'ne',100,-50,bounds),(100,50,460,330))
        self.assertEqual(app.resized_rect(rect,'sw',-100,50,bounds),(0,100,460,330))

    def test_resize_respects_minimum_and_desktop_bounds(self):
        self.assertEqual(app.resized_rect((100,100,360,280),'se',-500,-500,(0,0,1920,1080)),(100,100,140,100))
        self.assertEqual(app.resized_rect((100,100,360,280),'se',9999,9999,(0,0,1000,800)),(100,100,900,700))
        self.assertEqual(app.resized_rect((-1800,100,360,280),'',-9999,0,(-1920,0,3840,1080)),(-1920,100,360,280))

if __name__=='__main__':unittest.main(verbosity=2)
