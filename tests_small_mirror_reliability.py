"""Regression coverage for missed observers; synthetic data, no camera access."""
import math
import unittest
from unittest.mock import MagicMock
import numpy as np
import cv2

import small_mirror_core as core
from small_mirror_identity import SFace,OwnerTracker,Enrollment
from small_mirror_attention import FacingTimer,face_pose,facing_screen
from tests_small_mirror_identity import Recognizer,feature,face
from tests_small_mirror_v7 import face as pose_face


class RecognitionTests(unittest.TestCase):
    def test_distant_face_can_use_a_distinct_current_owner_but_not_a_stale_box(self):
        tracker=OwnerTracker(Recognizer(),[feature(0)]*3)
        def distant():
            return dict(bbox=(180,20,20,25),confidence=.9,test_feature=None)
        for now in (0,.2):faces=tracker.update(None,[face(),distant()],now)
        self.assertEqual([item['identity'] for item in faces],['owner','other'])
        self.assertEqual(faces[1]['identity_source'],'same-frame')
        # Owner left. A predicted/stale owner box is insufficient evidence.
        item=tracker.update(None,[distant()],.4)[0]
        self.assertEqual(item['identity'],'uncertain')
        self.assertNotIn('identity_source',item)

    def test_overlapping_unknown_face_cannot_use_owner_anchor(self):
        tracker=OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face()],0);tracker.update(None,[face()],.2)
        unknown=face(90);unknown['test_feature']=None
        faces=tracker.update(None,[face(),unknown],.4)
        self.assertNotEqual(faces[1]['identity'],'other')

    def test_brief_other_blur_does_not_restart_attention_but_expires(self):
        tracker=OwnerTracker(Recognizer(),[feature(0)]*3)
        tracker.update(None,[face(owner=False)],0)
        tracker.update(None,[face(owner=False)],.2)
        blurred=face(owner=False);blurred['test_feature']=None
        self.assertEqual(tracker.update(None,[blurred],.71)[0]['identity'],'other')
        self.assertEqual(tracker.update(None,[blurred],1.02)[0]['identity'],'uncertain')
        # Contradicting positive owner evidence immediately revokes permission.
        tracker.update(None,[face(owner=False)],2)
        tracker.update(None,[face(owner=False)],2.2)
        self.assertNotEqual(tracker.update(None,[face()],2.71)[0]['identity'],'other')

    def test_colliding_private_margin_keeps_visible_part_of_face(self):
        frame=np.full((200,400,3),180,np.uint8)
        target=(160,60,60,70)
        crop=core.safe_observer_crop(frame,target,(140,20,240,150),[(105,80,50,50)])
        self.assertIsNotNone(crop);self.assertTrue(np.all(crop==180))
        self.assertEqual(crop.shape[:2],(130,77))
        crop=core.safe_observer_crop(frame,target,(140,20,240,150),[(20,60,50,50)])
        self.assertTrue(np.all(crop==180));self.assertTrue(np.all(frame==180))
        # A neighboring head touches only crop padding. Trim the padding rather
        # than painting a black strip or dropping an otherwise separable face.
        crop=core.safe_observer_crop(frame,target,(140,20,240,150),[(100,80,45,50)])
        self.assertIsNotNone(crop);self.assertTrue(np.all(crop==180))
        self.assertLess(crop.shape[1],100)

    def test_multiple_private_heads_leave_only_current_observer_pixels(self):
        frame=np.full((180,320,3),40,np.uint8);face=(100,40,60,70)
        frame[40:110,100:160]=180
        boxes=[(60,60,40,45),(160,50,30,60)]
        # Paint the entire private margins with a sentinel, then prove none of
        # those pixels appears in the surviving fragment.
        for x,y,w,h in boxes:
            frame[max(0,math.floor(y-.4*h)):math.ceil(y+1.15*h),
                  max(0,math.floor(x-.15*w)):math.ceil(x+1.15*w)]=255
        original=frame.copy()
        crop=core.safe_observer_crop(frame,face,(82,0,178,124),boxes)
        self.assertIsNotNone(crop);self.assertTrue(np.any(crop==180))
        self.assertFalse(np.any(crop==255));np.testing.assert_array_equal(frame,original)

    def test_fully_occluded_target_keeps_redacted_live_frame_without_owner_pixels(self):
        frame=np.full((180,320,3),180,np.uint8)
        crop=core.safe_observer_crop(frame,(100,40,40,40),(88,16,152,88),[(60,0,160,150)])
        self.assertEqual(crop.shape[:2],(72,64));self.assertTrue(np.all(crop==0))
        self.assertTrue(np.all(frame==180))

    def test_zoom_merge_suppresses_duplicate_faces_after_coordinate_mapping(self):
        rows=[[100,80,30,40]+[0]*10+[.8],[101,81,31,39]+[0]*10+[.9],
              [200,80,30,40]+[0]*10+[.85]]
        merged=core.YuNetDetector.merge_rows(rows)
        self.assertEqual(len(merged),2);self.assertEqual(merged[0][-1],.9)
        tiles=core.YuNetDetector.zoom_tiles(1920,1080,[(0,0,1,1),(.7,0,1,.6)])
        self.assertLessEqual(len(tiles),64)
        self.assertTrue(all(0<=l<r<=1920 and 0<=t<b<=1080 and r-l<=512 and b-t<=512 for l,t,r,b in tiles))

    def test_high_sensitivity_face_is_not_discarded_by_a_second_hidden_threshold(self):
        recognizer=SFace.__new__(SFace);recognizer.cv2=cv2
        recognizer.engine=MagicMock()
        recognizer.engine.feature.return_value=feature(0)
        rng=np.random.default_rng(12)
        frame=rng.integers(35,220,(140,180,3),dtype=np.uint8)
        candidate=dict(bbox=(20,20,80,90),confidence=.60,row=[20,20,80,90,40,45,70,45,55,60,45,85,65,85,.60])
        self.assertIsNotNone(recognizer.feature(frame,candidate))
        recognizer.engine.alignCrop.assert_called_once()

    def test_small_or_invalid_face_has_a_useful_reason_and_no_embedding(self):
        recognizer=SFace.__new__(SFace);recognizer.cv2=cv2;recognizer.engine=MagicMock()
        frame=np.zeros((100,100,3),dtype=np.uint8)
        candidate=dict(bbox=(20,20,18,18),confidence=.9,row=[1.]*15)
        self.assertIsNone(recognizer.feature(frame,candidate))
        self.assertIn('太小',candidate['identity_hint'])
        candidate['row']=[float('nan')]*15
        self.assertIsNone(recognizer.feature(frame,candidate))
        recognizer.engine.feature.assert_not_called()

    def test_non_owner_below_reference_boundary_does_not_remain_uncertain_forever(self):
        tracker=OwnerTracker(Recognizer(),[feature(0)]*3)
        vector=.34*feature(0)+math.sqrt(1-.34**2)*feature(1)
        item=face(owner=False);item['test_feature']=vector
        tracker.update(None,[item],0)
        self.assertEqual(tracker.update(None,[item],.2)[0]['identity'],'other')

    def test_ambiguous_identity_can_protect_but_never_projects(self):
        faces=[dict(bbox=(20+i*60,20,40,50),identity=role,detection_accepted=True,
                    accepted=True,region_index=0) for i,role in enumerate(('owner','uncertain','other'))]
        result=core.classify_faces(core.DetectionResult(faces=faces),[(0,0,1,1)],True)
        self.assertEqual([f['protection_candidate'] for f in faces],[False,True,True])
        self.assertEqual([f['accepted'] for f in faces],[False,False,True])
        self.assertEqual(result.face,faces[-1]['bbox'])
        packet=core.FacePacket(frame=np.zeros((200,300,3),np.uint8),faces=faces)
        summary,inside=core.detection_summary(packet,[(0,0,1,1)])
        self.assertEqual(len(inside),3);self.assertIn('待确认 1',summary)

    def test_no_template_or_enrollment_never_gives_projection_permission(self):
        for enrolled,enrolling in ((False,False),(True,True)):
            item=dict(bbox=(20,20,40,50),identity='other',accepted=True,region_index=0)
            result=core.classify_faces(core.DetectionResult(faces=[item]),[(0,0,1,1)],enrolled,enrolling)
            self.assertIsNone(result.face);self.assertFalse(item['protection_candidate'])

    def test_similarly_sized_observers_do_not_keep_resetting_the_facing_timer(self):
        faces=[dict(bbox=(20,20,40,50),track_id=7,identity='other',accepted=True,region_index=0),
               dict(bbox=(100,20,45,50),track_id=8,identity='other',accepted=True,region_index=0)]
        result=core.classify_faces(core.DetectionResult(faces=faces),[(0,0,1,1)],True,preferred_track=7)
        self.assertEqual(result.face,faces[0]['bbox'])
        faces[0]['identity']='owner'
        result=core.classify_faces(result,[(0,0,1,1)],True,preferred_track=7)
        self.assertEqual(result.face,faces[1]['bbox'])


class AttentionTests(unittest.TestCase):
    def test_small_face_landmark_quantization_has_bounded_one_pixel_tolerance(self):
        near=pose_face(.38)
        self.assertFalse(facing_screen(near))
        far=pose_face(.38);far['row'][:14]*=.25
        self.assertTrue(facing_screen(far))
        far=pose_face(.6);far['row'][:14]*=.25
        self.assertFalse(facing_screen(far))

    def test_head_roll_does_not_turn_a_frontal_face_into_a_side_face(self):
        row=pose_face(.10,.6)['row'].copy()
        points=row[4:14].reshape(5,2)
        a=.42;rotation=np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]])
        row[4:14]=(points@rotation.T+100).reshape(-1)
        np.testing.assert_allclose(face_pose(row),(.10,.6),atol=1e-5)

    def test_one_missing_frame_preserves_progress_but_never_displays_a_missing_face(self):
        timer=FacingTimer()
        for i,now in enumerate((0,.17,.34,.51)):
            self.assertFalse(timer.update(now,i,pose_face()))
        self.assertFalse(timer.update(.68,4,None))
        self.assertFalse(timer.update(.85,5,pose_face()))
        self.assertTrue(timer.update(1.02,6,pose_face()))

    def test_definite_turn_or_another_person_resets_progress(self):
        timer=FacingTimer()
        for i,now in enumerate((0,.2,.4,.6,.8)):timer.update(now,i,pose_face())
        self.assertFalse(timer.update(.9,9,pose_face(.7)))
        self.assertFalse(timer.update(1.05,10,pose_face()))
        self.assertFalse(timer.update(1.25,11,pose_face(track=8)))

    def test_repeated_packet_and_long_gap_cannot_acquire(self):
        timer=FacingTimer()
        for now in (0,.4,.8,1.2):self.assertFalse(timer.update(now,1,pose_face()))
        self.assertFalse(timer.update(3,2,pose_face()))


class EnrollmentRecoveryTests(unittest.TestCase):
    def session(self):
        session=Enrollment(Recognizer(),7)
        item=face();item['track_id']=7
        session.update(None,[item],0)
        return session

    def test_short_track_change_can_resume_only_with_matching_face(self):
        session=self.session();item=face();item['track_id']=8
        session.update(None,[item],.4)
        self.assertEqual(session.track_id,8);self.assertEqual(len(session.samples),2)

    def test_nearby_different_person_or_ambiguous_matches_cannot_resume(self):
        for people in ([dict(face(owner=False),track_id=8)],
                       [dict(face(),track_id=8),dict(face(150),track_id=9)]):
            session=self.session();session.update(None,people,.4)
            self.assertEqual(session.track_id,7);self.assertEqual(len(session.samples),1)

    def test_long_absence_requires_explicit_selection(self):
        session=self.session();session.update(None,[dict(face(),track_id=8)],3.)
        self.assertEqual(session.track_id,7);self.assertEqual(len(session.samples),1)


if __name__=='__main__':unittest.main()
