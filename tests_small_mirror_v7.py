"""V7 behavior checks using synthetic faces and real local MJPG encoding only."""
from pathlib import Path
import tempfile
import shutil
import uuid
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from small_mirror_attention import face_pose, facing_screen
from small_mirror_identity import Enrollment
from small_mirror_effects import EFFECTS, deform
import small_mirror_recording as recording


def face(yaw=0., pitch=.5, track=7, person=0):
    vector=np.zeros(128,dtype=np.float32);vector[person]=1
    return dict(track_id=track, test_feature=vector,
                row=np.array([0,0,100,120,30,30,70,30,50+40*yaw,30+40*pitch,
                              35,70,65,70,.99],dtype=np.float32))


class Recognizer:
    def enrollment_feature(self,frame,item):
        return item.get('test_feature'),'没看清你的脸，再试试'


class AttentionTests(unittest.TestCase):
    def test_front_and_small_offsets_pass_but_sideways_and_vertical_offsets_do_not(self):
        for yaw,pitch in ((0,.5),(.18,.45),(-.18,.7)):
            self.assertTrue(facing_screen(face(yaw,pitch)))
        for yaw,pitch in ((.35,.5),(-.35,.5),(0,.15),(0,.95)):
            self.assertFalse(facing_screen(face(yaw,pitch)))

    def test_degenerate_or_missing_landmarks_never_claim_facing(self):
        for row in (None,[],np.zeros(15),[float('nan')]*15):
            self.assertFalse(facing_screen({'row':row}))
        self.assertFalse(facing_screen(None))

    def test_pose_is_invariant_under_camera_resolution_and_translation(self):
        original=face(.16,.62)['row'];scaled=original.copy()
        scaled[:14]*=2
        scaled[4:14:2]+=137;scaled[5:14:2]+=92
        np.testing.assert_allclose(face_pose(original),face_pose(scaled),rtol=1e-6)


class AutomaticEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.session=Enrollment(Recognizer(),7,automatic=True);self.now=0.

    def add(self,item,count=1):
        for _ in range(count):
            self.session.update(None,[item],self.now);self.now+=.35

    def test_all_four_poses_advance_automatically_and_finish_at_twelve_samples(self):
        for stage,item in enumerate((face(),face(.18),face(-.18),face(0,.62))):
            self.add(item,3)
            self.assertEqual(self.session.stage,stage+1)
        self.assertEqual(len(self.session.samples),12)
        self.assertTrue(self.session.status()['done'])
        self.add(face(),5)
        self.assertEqual(len(self.session.samples),12)

    def test_remaining_front_facing_does_not_silently_complete_the_wizard(self):
        self.add(face(),20)
        self.assertEqual(self.session.stage,1)
        self.assertEqual(len(self.session.samples),3)
        self.assertIn('左',self.session.status()['message'])

    def test_turns_use_individual_front_baseline(self):
        self.add(face(.15,.6),3)
        self.add(face(.20,.6),4)
        self.assertEqual(len(self.session.samples),3)
        self.add(face(.30,.6),3)
        self.assertEqual(self.session.stage,2)
        self.add(face(.02,.6),3)
        self.add(face(.15,.70),3)
        self.assertTrue(self.session.status()['done'])

    def test_lost_track_or_different_face_cannot_add_samples(self):
        self.add(face(),3)
        self.add(face(.18,track=8),3)
        self.assertIn('没跟上',self.session.message)
        self.add(face(.18,person=1),3)
        self.assertIn('不像',self.session.message)
        self.assertEqual(len(self.session.samples),3)

    def test_retry_only_discards_current_partial_pose(self):
        self.add(face(),3);self.add(face(.18),2)
        self.session.retry()
        self.assertEqual(len(self.session.samples),3)
        self.assertEqual(self.session.stage,1)
        self.add(face(.18),3)
        self.assertEqual(self.session.stage,2)

    def test_retry_front_discards_old_baseline_and_quality_failures_do_not_capture(self):
        self.add(face(.15),2);self.session.retry()
        bad=face();bad['test_feature']=None;self.add(bad,3)
        self.assertEqual(self.session.samples,[])
        self.add(face(-.10),3)
        np.testing.assert_allclose(self.session.baseline,(-.10,.5),atol=1e-6)

    def test_sample_vectors_are_copies_not_live_references(self):
        item=face();self.add(item)
        item['test_feature'][:]=0
        self.assertEqual(self.session.samples[0][0],1)


class EffectsTests(unittest.TestCase):
    def test_all_effects_leave_original_recording_pixels_unchanged(self):
        original=np.random.default_rng(7).integers(0,256,(120,160,3),dtype=np.uint8)
        baseline=original.copy()
        for effect in EFFECTS:
            result=deform(original,effect,cv2)
            self.assertEqual(result.shape,original.shape)
            self.assertEqual(result.dtype,original.dtype)
            np.testing.assert_array_equal(original,baseline)
            self.assertFalse(np.shares_memory(result,original))


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.test_root=Path(__file__).resolve().parent/('local-video-test-'+uuid.uuid4().hex)
        self.test_root.mkdir()
        self.directory=self.test_root/'recordings'
        self.recorder=recording.EventRecorder(self.directory,cv2)
        self.red=np.zeros((80,120,3),dtype=np.uint8);self.red[:,:,2]=200
        self.green=np.zeros_like(self.red);self.green[:,:,1]=180

    def tearDown(self):
        self.recorder.close()
        assert self.test_root.resolve().parent==Path(__file__).resolve().parent
        shutil.rmtree(self.test_root)

    def frames(self,path):
        capture=cv2.VideoCapture(str(path));result=[]
        try:
            self.assertTrue(capture.isOpened(),str(path))
            self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS),recording.FPS)
            while True:
                ok,frame=capture.read()
                if not ok:break
                result.append(frame)
        finally:capture.release()
        return result

    def test_no_target_and_close_create_no_recording_directory(self):
        self.recorder.feed(None,0,0,None);self.recorder.finish();self.recorder.close()
        self.assertFalse(self.directory.exists())

    def test_immediate_close_flushes_all_frames_and_decodes_real_mjpg(self):
        original=self.red.copy()
        for i in range(6):self.recorder.feed(self.red,i/6,i,7)
        self.red[:]=0
        self.recorder.close()
        clips=recording.recordings(self.directory)
        self.assertEqual(self.recorder.error,'');self.assertEqual(len(clips),1)
        decoded=self.frames(clips[0]);self.assertEqual(len(decoded),6)
        self.assertEqual(decoded[0].shape,(240,320,3))
        self.assertGreater(decoded[-1][120,160,2],190)
        self.assertLess(decoded[-1][120,160,1],5)
        self.assertFalse(list(self.directory.glob('*.partial.avi')))
        self.assertFalse(self.recorder.thread.is_alive())
        np.testing.assert_array_equal(original[:,:,2],200)

    def test_subject_switch_creates_two_independent_clips(self):
        for i in range(3):self.recorder.feed(self.red,i/6,i,7)
        for i in range(3,6):self.recorder.feed(self.green,i/6,i,8)
        self.recorder.close()
        clips=recording.recordings(self.directory);self.assertEqual(len(clips),2)
        colors=sorted(int(self.frames(clip)[0][120,160].argmax()) for clip in clips)
        self.assertEqual(colors,[1,2])

    def test_missing_target_is_black_then_finishes_instead_of_freezing_a_face(self):
        self.recorder.feed(self.red,0,0,7)
        self.recorder.feed(None,.5,1,None)
        self.recorder.feed(None,2.6,2,None)
        self.assertIsNone(self.recorder.target)
        self.recorder.close()
        decoded=self.frames(recording.recordings(self.directory)[0])
        self.assertGreater(decoded[0][120,160,2],190)
        self.assertLess(decoded[-1].mean(),1)

    def test_finish_for_pause_then_resume_creates_new_clip(self):
        self.recorder.feed(self.red,0,0,7);self.recorder.finish()
        self.recorder.finish()  # Repeated paused ticks are harmless.
        self.recorder.feed(self.green,5,1,7);self.recorder.close()
        self.assertEqual(len(recording.recordings(self.directory)),2)

    def test_duplicate_packets_do_not_extend_recording(self):
        self.recorder.feed(self.red,0,2,7)
        for now in (.2,.4,.6):self.recorder.feed(self.red,now,2,7)
        self.recorder.close()
        self.assertEqual(len(self.frames(recording.recordings(self.directory)[0])),1)

    def test_recording_rolls_over_at_duration_limit(self):
        with patch.object(recording,'MAX_CLIP_SECONDS',1):
            self.recorder.feed(self.red,0,0,7)
            self.recorder.feed(self.green,1.1,1,7)
            self.recorder.close()
        self.assertEqual(len(recording.recordings(self.directory)),2)

    def test_space_limit_preserves_existing_files_and_reports_error(self):
        self.directory.mkdir();existing=self.directory/'older.avi';existing.write_bytes(b'original')
        with patch.object(recording,'SPACE_LIMIT',1):
            self.recorder.feed(self.red,0,0,7);self.recorder.close()
        self.assertEqual(existing.read_bytes(),b'original')
        self.assertIn('1 GB',self.recorder.error)
        self.assertEqual(list(self.directory.iterdir()),[existing])

    def test_list_and_path_guard_exclude_partial_or_unrelated_files(self):
        self.directory.mkdir()
        for name in ('valid.avi','unfinished.partial.avi','notes.txt'):
            (self.directory/name).write_bytes(b'')
        self.assertEqual([item.name for item in recording.recordings(self.directory)],['valid.avi'])
        for path in (self.directory/'unfinished.partial.avi',self.directory/'notes.txt',self.directory/'../other.avi'):
            with self.assertRaises(ValueError):recording.safe_clip(path,self.directory)


if __name__=='__main__':unittest.main(verbosity=2)
