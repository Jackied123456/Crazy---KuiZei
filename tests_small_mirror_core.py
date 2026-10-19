"""Bounded local core checks; never opens a camera or uses the network."""

import argparse
import math
import os
import unittest
from unittest import mock

import small_mirror_core as core


class GeometryTests(unittest.TestCase):
    def test_exclusions_override_detection_areas_and_choose_observer(self):
        rows = [[40, 30, 40, 40] + [0]*10 + [.99],
                [140, 30, 20, 20] + [0]*10 + [.85]]
        excluded = [(0, 0, .5, 1)]
        result = core._interpret_faces(rows, 200, 100, [(0, 0, 1, 1)], exclusions=excluded)
        self.assertEqual(result.face, (140, 30, 20, 20))
        self.assertTrue(result.faces[0]['excluded'])
        self.assertFalse(result.faces[0]['accepted'])
        self.assertTrue(result.faces[1]['accepted'])
        self.assertIsNone(core._interpret_faces(rows[:1], 200, 100, [(0, 0, 1, 1)],
                                               exclusions=excluded).face)

    def test_exclusions_include_center_or_substantial_overlap_and_multiple_zones(self):
        face = (40, 40, 20, 20)
        self.assertTrue(core.excluded_face(face, 100, 100, [(.4,.4,.6,.6)]))
        self.assertTrue(core.excluded_face(face, 100, 100, [(0,0,.45,1)]))
        self.assertFalse(core.excluded_face(face, 100, 100, [(0,0,.41,1)]))
        self.assertTrue(core.excluded_face(face, 100, 100, [(0,0,.1,.1),(.4,.4,.6,.6)]))

    def test_head_crop_redacts_owner_pixels_without_modifying_original(self):
        import numpy as np
        frame = np.full((100,100,3), 255, dtype=np.uint8)
        crop = core.redact_exclusions(frame, (20,20,80,80), [(0,0,.3,1),(.7,.7,1,1)])
        self.assertEqual(crop.shape, (60,60,3))
        self.assertTrue(np.all(crop[:, :10] == 0))
        self.assertTrue(np.all(crop[50:,50:] == 0))
        self.assertTrue(np.all(crop[10:40,10:40] == 255))
        self.assertTrue(np.all(frame == 255))

    def test_normalize_legacy_multiple_and_invalid(self):
        self.assertEqual(core.normalize_rois((.2, .1, .8, .9)), [(.2, .1, .8, .9)])
        self.assertEqual(core.normalize_rois([(.8, .9, .2, .1), (.2, .1, .8, .9)]), [(.2, .1, .8, .9)])
        self.assertEqual(core.normalize_rois([(-1, 0, .3, 2), (0, 0, 0, 1), (math.nan, 0, 1, 1)]),
                         [(0.0, 0.0, .3, 1.0)])
        self.assertEqual(core.normalize_rois(None), [])

    def test_multiregion_owner_exclusion_and_overlap_single_match(self):
        rois = [(0, 0, .3, 1), (.7, 0, 1, 1)]
        self.assertIsNone(core.matching_region((40, 30, 20, 20), 100, 100, rois))
        self.assertEqual(core.matching_region((4, 20, 20, 20), 100, 100, rois), 0)
        self.assertEqual(core.matching_region((74, 20, 20, 20), 100, 100, rois), 1)
        self.assertEqual(core.matching_region((20, 20, 20, 20), 100, 100,
                                             [(0, 0, .6, 1), (.1, 0, .7, 1)]), 0)

    def test_center_and_majority_required(self):
        self.assertIsNone(core.matching_region((5, 5, 80, 80), 100, 100, [(.4, .4, .6, .6)]))
        self.assertIsNone(core.matching_region((10, 20, 20, 20), 100, 100, [(0, 0, .2, 1)]))
        self.assertEqual(core.matching_region((15, 20, 20, 20), 100, 100, [(0, 0, .3, 1)]), 0)

    def test_head_expansion_bounded_to_matched_roi(self):
        roi = (.2, .1, .8, .9)
        self.assertEqual(core.observer_crop((40, 40, 20, 20), 100, 100, roi), (34, 28, 66, 64))
        near_border = core.observer_crop((22, 12, 20, 20), 100, 100, roi)
        self.assertEqual(near_border, (20, 10, 48, 36))
        self.assertIsNone(core.observer_crop((2, 2, 10, 10), 100, 100, roi))
        eye = core.observer_crop((40, 40, 20, 20), 100, 100, roi, eye_mode=True)
        self.assertTrue(20 <= eye[0] < eye[2] <= 80 and 10 <= eye[1] < eye[3] <= 90)

    def test_confidence_size_and_scale(self):
        rows = [[20, 20, 20, 20] + [0] * 10 + [.60]]
        high = core._interpret_faces(rows, 200, 200, [(0, 0, 1, 1)], "高", 2, 2)
        standard = core._interpret_faces(rows, 200, 200, [(0, 0, 1, 1)], "标准", 2, 2)
        self.assertEqual(high.face, (40, 40, 40, 40))
        self.assertIsNone(standard.face)
        self.assertFalse(standard.faces[0]["accepted"])
        rows = [[10, 10, 11, 11] + [0] * 10 + [.99]]
        self.assertIsNone(core._interpret_faces(rows, 100, 100, [(0, 0, 1, 1)]).face)
        rows[0][2:4] = [12, 12]
        self.assertIsNotNone(core._interpret_faces(rows, 100, 100, [(0, 0, 1, 1)]).face)

    def test_configure_and_stop_do_not_open_camera(self):
        worker = core.CameraWorker(0)
        worker.configure([(.1, .1, .4, .9), (.6, .1, .9, .9)], resolution="1920x1080")
        self.assertEqual(worker.resolution, "1920×1080")
        self.assertEqual(len(worker.rois), 2)
        worker.stop()
        self.assertTrue(worker.stop_event.is_set())
        self.assertIsNone(worker.latest().frame)


def real_model_checks(sample_path=None):
    core.load_dependencies()
    cv2 = core.cv2
    import numpy as np
    with mock.patch.object(cv2, "VideoCapture", side_effect=AssertionError("Camera access forbidden in tests")):
        detector = core.YuNetDetector()
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        assert detector.detect(blank, [(0, 0, 1, 1)]).face is None
        if sample_path is None:
            print("REAL MODEL: blank-frame inference passed; no local sample provided.")
            return
        image = cv2.imread(os.fspath(sample_path))
        assert image is not None, "Cannot read the supplied local test sample"
        height, width = image.shape[:2]
        detected = detector.detect(image, [(0, 0, 1, 1)])
        assert detected.face is not None, "YuNet did not detect the official local face sample"
        x, y, w, h = detected.face
        assert 0 <= x < x + w <= width and 0 <= y < y + h <= height
        # The exact same complete image is evaluated; only ROI eligibility changes.
        excluded = detector.detect(image, [(0, 0, .1, .1)])
        assert excluded.face is None
        overlapping = detector.detect(image, [(0, 0, 1, 1), (.1, .1, .95, .95)])
        assert len([face for face in overlapping.faces if face["accepted"]]) == len(
            [face for face in detected.faces if face["accepted"]])
        own = detector.detect(image, [(0, 0, 1, 1)], exclusions=[(0, 0, 1, 1)])
        assert own.face is None and all(not face['accepted'] for face in own.faces)
        # Embed the known portrait at smaller sizes in a wide, uncropped frame.
        successes = []
        for sample_width in (192, 128):
            scaled = cv2.resize(image, (sample_width, round(height * sample_width / width)))
            canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
            sh, sw = scaled.shape[:2]
            canvas[40:40 + sh, 900:900 + sw] = scaled
            small = detector.detect(canvas, [(.65, 0, 1, .6)])
            successes.append((sample_width, small.face is not None,
                              round(max((face["confidence"] for face in small.faces), default=0), 3)))
        assert any(item[1] for item in successes), "No smaller embedded sample was detected"
        print(f"REAL MODEL PASS: official sample, ROI exclusion, overlap count, small-face cases {successes}; no camera.")


def fake_capture_check():
    """Check actual-size retention and property requests without a camera driver."""
    import numpy as np
    core.load_dependencies()
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    worker = core.CameraWorker(0)
    worker.configure([(.6, 0, 1, 1)], resolution="1920×1080")
    snapshots = []

    class FakeCapture:
        def __init__(self):
            self.properties = []
            self.reads = 0
            self.released = False

        def isOpened(self):
            return True

        def set(self, prop, value):
            self.properties.append((prop, value))
            return prop != core.cv2.CAP_PROP_ZOOM

        def get(self, prop):
            return 0.0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, frame
            snapshots.append(worker.latest())
            worker.stop()
            return False, None

        def release(self):
            self.released = True

    class FakeDetector:
        def detect(self, original, rois, sensitivity, exclusions):
            assert original is frame and original.shape == (1080, 1920, 3)
            assert rois == [(.6, 0.0, 1.0, 1.0)]
            return core.DetectionResult()

    fake = FakeCapture()
    with mock.patch.object(core.cv2, "VideoCapture", return_value=fake), mock.patch.object(
            core, "YuNetDetector", return_value=FakeDetector()):
        worker.run()
    assert fake.released and len(snapshots) == 1
    packet = snapshots[0]
    assert packet.ready and packet.frame is frame
    assert "实际 1920×1080" in packet.capture_info and "软件无裁切" in packet.capture_info
    assert "变焦 0 请求未接受/未支持" in packet.capture_info
    assert (core.cv2.CAP_PROP_FRAME_WIDTH, 1920) in fake.properties
    assert (core.cv2.CAP_PROP_FRAME_HEIGHT, 1080) in fake.properties
    assert (core.cv2.CAP_PROP_FPS, 15) in fake.properties
    assert (core.cv2.CAP_PROP_ZOOM, 0) in fake.properties
    print("FAKE CAPTURE PASS: original 1920×1080 frame retained, requests reported and driver released; no camera.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-path")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GeometryTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    real_model_checks(args.sample_path)
    fake_capture_check()
