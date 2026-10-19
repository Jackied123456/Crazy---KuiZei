"""Regression coverage for missed observers; synthetic data, no camera access."""
import math
import unittest
from unittest.mock import MagicMock
import numpy as np
import cv2

import small_mirror_core as core
from small_mirror_identity import SFace,OwnerTracker,Enrollment
from small_mirror_attention import FacingTimer,face_pose
from tests_small_mirror_identity import Recognizer,feature,face
from tests_small_mirror_v7 import face as pose_face


class RecognitionTests(unittest.TestCase):
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
