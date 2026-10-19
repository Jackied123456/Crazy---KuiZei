"""Native state-transition and responsiveness checks; synthetic pixels only."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import numpy as np
import small_mirror as mirror


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mirror.core.load_dependencies()
        mirror.core.enable_dpi_awareness()
        (Path(__file__).parent/'build').mkdir(exist_ok=True)

    def setUp(self):
        self.profile=tempfile.TemporaryDirectory(dir=Path(__file__).parent/'build',prefix='ui-check-')
        self.root=tk.Tk()
        self.app=mirror.PrivacyApp(self.root,Path(self.profile.name),enable_shell=False)
        if self.app.tick_after is not None:
            self.root.after_cancel(self.app.tick_after);self.app.tick_after=None
        self.root.update_idletasks();self.root.update()
        self.errors=[]
        self.root.report_callback_exception=lambda kind,value,tb:self.errors.append(str(value))

    def tearDown(self):
        self.app.worker=None
        self.app.close()
        self.profile.cleanup()
        self.assertEqual(self.errors,[])

    def texts(self):
        return [self.app.preview.itemcget(item,'text') for item in self.app.preview.find_all()
                if self.app.preview.type(item)=='text']

    def test_starting_and_error_replace_hidden_preview_without_leaking_items(self):
        app=self.app
        self.assertIn('画面已隐藏',self.texts())
        app.preview_enabled.set(True);app.toggle_preview()
        app.worker=SimpleNamespace();app.paused=False
        for _ in range(4):
            app._preview_render_key=None
            app.render_preview(mirror.core.FacePacket(capture_info='正在连接摄像头'))
            self.assertEqual(self.texts()[0],'正在连接摄像头')
            self.assertEqual(len(self.texts()),2)
            self.assertNotIn('画面已隐藏',self.texts())
        app.render_preview(mirror.core.FacePacket(error='摄像头被占用，请关闭其他应用后重试'))
        self.assertEqual(len(self.texts()),2)
        self.assertNotIn('正在连接摄像头',self.texts())
        app.paused=True
        app.render_preview(mirror.core.FacePacket())
        self.assertEqual(self.texts()[0],'正在关闭相机…')

    def test_wrapped_state_text_never_overlaps(self):
        from small_mirror_home import draw_preview_state
        canvas=self.app.preview
        for size in ((220,180),(320,260),(620,320)):
            # An independent canvas gives exact small geometry without a real screen.
            popup=tk.Toplevel(self.root);popup.geometry(f'{size[0]}x{size[1]}')
            probe=tk.Canvas(popup,highlightthickness=0);probe.pack(fill='both',expand=True)
            self.root.update();self.app.preview=probe
            try:
                draw_preview_state(self.app,'正在连接摄像头 · 驱动响应较慢','请关闭占用相机的其他应用，或者在设置里更换相机编号后重试。')
                boxes=[probe.bbox(item) for item in probe.find_all() if probe.type(item)=='text']
                self.assertTrue(all(a[3]<b[1] for a,b in zip(boxes,boxes[1:])),boxes)
                self.assertTrue(all(b[0]>=0 and b[2]<=size[0]+2 for b in boxes),boxes)
                self.assertTrue(all(b[1]>=0 and b[3]<=size[1]+2 for b in boxes),boxes)
            finally:self.app.preview=canvas;popup.destroy()

    def test_hiding_preview_clears_pixels_without_stopping_worker(self):
        app=self.app
        worker=SimpleNamespace(stop_event=threading.Event(),identity_version=0,
            latest=lambda:mirror.core.FacePacket())
        app.worker=worker;app.paused=False;app.preview_enabled.set(True)
        frame=np.full((180,320,3),120,dtype=np.uint8)
        app.render_preview(mirror.core.FacePacket(frame=frame,serial=1,ready=True))
        self.assertIsNotNone(app.preview_image)
        app.preview_enabled.set(False);app.toggle_preview()
        self.assertIsNone(app.preview_image);self.assertIsNone(app.preview_map)
        self.assertFalse(app.preview.find_withtag('preview-frame'))
        self.assertIs(app.worker,worker);self.assertFalse(app.paused)
        self.assertTrue(app.camera_button.winfo_ismapped())

    def test_slow_driver_and_inference_do_not_block_ui_or_hold_packet_lock(self):
        core=mirror.core;driver_gate=threading.Event();inference_gate=threading.Event()
        entered_driver=threading.Event();entered_model=threading.Event()
        worker=core.CameraWorker(0);worker.configure([(0,0,1,1)])
        class Capture:
            def isOpened(self):return True
            def set(self,*_args):return False
            def get(self,*_args):return 0.
            def read(self):
                entered_driver.set();driver_gate.wait(2)
                time.sleep(.01)
                return True,np.zeros((180,320,3),np.uint8)
            def release(self):pass
        def infer(*_args):
            entered_model.set();inference_gate.wait(2)
            return core.DetectionResult()
        frames=[]
        def pulse():
            frames.append(time.perf_counter())
            if len(frames)<20:self.root.after(5,pulse)
        with patch.object(core.cv2,'VideoCapture',return_value=Capture()), \
             patch.object(core,'YuNetDetector',return_value=SimpleNamespace(detect=infer)), \
             patch('small_mirror_identity.SFace',return_value=SimpleNamespace(cv2=core.cv2)):
            worker.start()
            try:
                self.assertTrue(entered_driver.wait(2))
                self.assertEqual(worker.progress()[0],'正在等待相机画面')
                self.root.after(0,pulse)
                end=time.perf_counter()+.15
                while time.perf_counter()<end:self.root.update();time.sleep(.002)
                self.assertGreater(len(frames),8)
                started=time.perf_counter();worker.latest();self.assertLess(time.perf_counter()-started,.05)
                driver_gate.set();self.assertTrue(entered_model.wait(2))
                self.assertEqual(worker.progress()[0],'正在识别人脸')
                first=worker.preview_latest()[0]
                time.sleep(.06)
                self.assertGreater(worker.preview_latest()[0],first,'Capture waited for inference')
                self.app.worker=worker;self.app.paused=False
                preview=self.app.live_preview_packet(core.FacePacket())
                self.assertIsNotNone(preview.frame)
                self.assertFalse(preview.identity_ready)
                self.assertEqual(preview.faces,[])
                self.assertFalse(self.app.valid_observer(preview))
                prior=core.FacePacket(frame=np.zeros((180,320,3),np.uint8),timestamp=time.monotonic(),
                    face=(20,20,40,40),identity_ready=True,ready=True)
                self.assertFalse(self.app.valid_observer(self.app.live_preview_packet(prior)))
            finally:
                worker.stop();driver_gate.set();inference_gate.set();worker.join(2)
            self.assertFalse(worker.is_alive())
            self.app.worker=None;self.app.paused=True

    def test_callback_failure_pauses_safely_and_keeps_timer_scheduled(self):
        app=self.app
        with patch.object(app,'tick_once',side_effect=RuntimeError('synthetic draw failure')):
            app.tick()
        self.assertTrue(app.paused)
        self.assertIsNotNone(app.tick_after)
        self.assertIn('synthetic draw failure',app.detail_var.get())
        self.assertFalse(app.bar_shown)

    def test_ui_failure_does_not_reveal_an_existing_protection_mask(self):
        self.app.mask_active=True
        with patch.object(self.app,'restore') as restore:
            self.app.handle_runtime_error(RuntimeError('synthetic failure'))
        restore.assert_not_called()
        self.assertTrue(self.app.mask_active)
        self.app.mask_active=False

    def test_camera_settings_button_lands_on_camera_tab(self):
        self.app.open_preferences('相机');self.root.update()
        self.assertEqual(self.app.preferences_tabs.index('current'),1)
        self.app.open_preferences('动画');self.root.update()
        self.assertEqual(self.app.preferences_tabs.index('current'),3)
        self.app.close_preferences()

    def test_camera_failure_keeps_owner_retry_available_across_ticks(self):
        app=self.app;app.open_owner_settings()
        app.enrolling=True;app.enroll_button.configure(state='disabled')
        app.camera_error='无法开启摄像头，请检查权限'
        app.worker=None;app.paused=True
        for _ in range(3):
            packet=app.current_packet()
            app.update_enrollment(packet)
            self.assertEqual(app.owner_status.get(),app.camera_error)
            self.assertFalse(app.enroll_button.instate(['disabled']))
        app.cancel_enrollment()

    def test_disconnected_capture_reports_error_and_releases_device(self):
        core=mirror.core;worker=core.CameraWorker(0)
        capture=SimpleNamespace(isOpened=lambda:True,set=lambda *_args:False,get=lambda *_args:0.,
                                read=lambda:(False,None),release=lambda:None)
        with patch.object(core.cv2,'VideoCapture',return_value=capture), \
             patch.object(core,'YuNetDetector',return_value=SimpleNamespace()), \
             patch('small_mirror_identity.SFace',return_value=SimpleNamespace(cv2=core.cv2)), \
             patch.object(capture,'release') as release:
            worker.start();worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertIn('相机画面已中断',worker.latest().error)
            self.assertIsNone(worker.preview_latest())
            release.assert_called_once()

    def test_detection_packet_keeps_original_capture_timestamp(self):
        worker=mirror.core.CameraWorker(0)
        worker.publish(frame=np.zeros((20,20,3),np.uint8),ready=True,captured_at=12.5)
        self.assertEqual(worker.latest().timestamp,12.5)


if __name__=='__main__':unittest.main()
