"""Native state-transition and responsiveness checks; synthetic pixels only."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch, MagicMock
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

    def fake_worker(self,snapshot,packet=None):
        state={'alive':True}
        worker=SimpleNamespace(stop_event=threading.Event(),identity_version=0,
            latest=lambda:packet or mirror.core.FacePacket(),preview_latest=lambda:snapshot[0],
            progress=lambda:('正在识别人脸',0),is_alive=lambda:state['alive'],
            start=MagicMock(),configure=MagicMock(),identity_command=MagicMock())
        worker.stop=MagicMock(side_effect=worker.stop_event.set)
        return worker,state

    def test_first_start_opens_owner_guide_without_claiming_protection(self):
        app=self.app
        with patch.object(app,'open_owner_settings') as guide,patch.object(app,'begin_enrollment') as enroll:
            app.camera_button.invoke()
        guide.assert_called_once();enroll.assert_called_once()
        self.assertTrue(app.resume_after_enrollment)
        self.assertIsNone(app.worker);self.assertNotIn('保护中',app.status_var.get())

    def test_single_start_expands_video_and_ignores_double_click_while_starting(self):
        app=self.app;app.owner_samples=[np.ones(128,dtype=np.float32)]
        worker,_state=self.fake_worker([None])
        with patch.object(mirror.core,'CameraWorker',return_value=worker) as factory:
            app.camera_button.invoke();app.camera_button.invoke()
            factory.assert_called_once();worker.start.assert_called_once()
            self.assertTrue(app.preview_enabled.get())
            self.assertEqual(app.preview_toggle.cget('text'),'收起画面')
            app.tick_once();self.root.update()
            self.assertTrue(app.camera_cancel_button.winfo_ismapped())
            app.camera_cancel_button.invoke();worker.stop.assert_called_once()
        self.assertTrue(app.paused)

    def test_first_owner_save_continues_monitoring_without_reopening_camera(self):
        app=self.app;app.open_owner_settings()
        worker,_state=self.fake_worker([None]);app.worker=worker;app.paused=False
        app.owner_verifying=True;app.owner_verified=True;app.resume_after_enrollment=True
        app.pending_owner_samples=[np.ones(128,dtype=np.float32)]
        app.remember_owner.set(False)
        app.save_verified_owner()
        self.assertFalse(app.paused);worker.stop.assert_not_called()
        self.assertIsNone(app.owner_window);self.assertTrue(app.owner_samples)

    def test_collapsing_view_uses_same_camera_and_reopens_latest_frame(self):
        app=self.app;frame=np.zeros((180,320,3),np.uint8)
        snapshot=[(1,time.monotonic(),frame)]
        worker,_state=self.fake_worker(snapshot);app.worker=worker;app.paused=False
        app.preview_enabled.set(True);app.tick_once()
        app.preview_toggle.invoke()
        self.assertFalse(app.preview_enabled.get());self.assertIsNone(app.preview_image)
        worker.stop.assert_not_called()
        snapshot[0]=(2,time.monotonic(),np.full_like(frame,160))
        app.preview_toggle.invoke();app.tick_once()
        self.assertTrue(app.preview_enabled.get());self.assertIsNotNone(app.preview_image)
        self.assertEqual(app._preview_render_key[0],2)
        self.assertIs(app.worker,worker)

    def test_maximized_preview_uses_capture_serial_even_when_detector_is_pending(self):
        app=self.app;frame=np.zeros((180,320,3),np.uint8)
        snapshot=[(1,time.monotonic(),frame)]
        worker,_state=self.fake_worker(snapshot)
        app.worker=worker;app.paused=False;app.preview_enabled.set(True)
        with patch.object(self.root,'state',return_value='zoomed'):
            app.tick_once();first=app.preview.itemcget('preview-frame','image')
            frame[:]=170;snapshot[0]=(2,time.monotonic(),frame)
            app.tick_once();second=app.preview.itemcget('preview-frame','image')
        self.assertTrue(first and second);self.assertNotEqual(first,second)
        self.assertIn('识别处理中',app.status_var.get())
        self.assertEqual(app._preview_render_key[0],2)

    def test_stale_capture_clears_video_offers_retry_and_keeps_existing_cover(self):
        app=self.app;frame=np.zeros((180,320,3),np.uint8)
        packet=mirror.core.FacePacket(frame=frame,serial=1,timestamp=100,ready=True)
        worker,_state=self.fake_worker([(1,100,frame)],packet)
        app.worker=worker;app.paused=False;app.preview_enabled.set(True);app.camera_started_at=100
        with patch.object(mirror.time,'monotonic',return_value=100):app.tick_once()
        self.assertIsNotNone(app.preview_image)
        app.mask_active=True;app.auto_mask=True;app.record_enabled.set(True)
        try:
            with patch.object(mirror.time,'monotonic',return_value=103), \
                 patch.object(app,'sync_window_masks'),patch.object(app,'restore') as restore, \
                 patch.object(app.recorder,'feed') as feed:
                app.tick_once();restore.assert_not_called()
                self.assertIsNone(feed.call_args.args[0])
            self.assertEqual(app.camera_health,'stalled')
            self.assertIsNone(app.preview_image)
            self.assertIn('相机画面已中断',self.texts())
            self.assertTrue(app.camera_retry_button.winfo_manager())
            self.assertTrue(app.mask_active)
        finally:app.mask_active=False;app.auto_mask=False

    def test_retry_waits_for_old_camera_release_before_starting_new_one(self):
        app=self.app;app.owner_samples=[np.ones(128,dtype=np.float32)]
        worker,state=self.fake_worker([None]);replacement,_next=self.fake_worker([None])
        app.worker=worker;app.paused=False
        with patch.object(app,'restore') as restore,patch.object(mirror.core,'CameraWorker',return_value=replacement) as factory:
            app.retry_camera();app.tick_once()
            factory.assert_not_called();restore.assert_not_called()
            self.assertTrue(worker.stop_event.is_set())
            state['alive']=False;app.tick_once()
            factory.assert_called_once();replacement.start.assert_called_once()
            self.assertIs(app.worker,replacement);self.assertFalse(app.retry_after_stop)

    def test_live_status_explicitly_reports_monitoring_without_content_cover(self):
        app=self.app;frame=np.zeros((180,320,3),np.uint8);now=time.monotonic()
        packet=mirror.core.FacePacket(frame=frame,serial=1,timestamp=now,ready=True)
        worker,_state=self.fake_worker([(1,now,frame)],packet)
        app.worker=worker;app.paused=False;app.owner_samples=[np.ones(128,dtype=np.float32)]
        app.tick_once()
        self.assertIn('仅监测，未设置遮挡',app.status_var.get())

    def region_editor(self):
        app=self.app
        app.choose_selection('detect');self.root.update()
        app.render_preview(mirror.core.FacePacket(frame=np.zeros((360,640,3),np.uint8),serial=9))
        def event(x,y,state=0):
            ox,oy,w,h=app.preview_map
            return SimpleNamespace(x=ox+x*w,y=oy+y*h,state=state)
        return app,event

    def test_drag_corner_is_draft_until_release_then_saved_and_undoable(self):
        app,event=self.region_editor()
        original=[(.1,.1,.4,.5),(.6,.2,.9,.8)];app.rois=list(original)
        with patch.object(app,'update_settings',wraps=app.update_settings) as update:
            app.roi_start(event(.4,.5));app.roi_drag(event(.5,.65))
            self.assertEqual(app.rois,original);update.assert_not_called()
            self.assertAlmostEqual(app.roi_draft[2],.5)
            self.assertAlmostEqual(app.roi_draft[3],.65)
            app.roi_finish(event(.5,.65));update.assert_called_once()
            self.assertEqual(app.rois[1],original[1])
            saved=mirror.json.loads(app.config_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['rois'],[list(r) for r in app.rois])
        app.undo_selected();self.assertEqual(app.rois,original)

    def test_move_clamps_without_changing_size_or_other_regions(self):
        app,event=self.region_editor();app.rois=[(.1,.1,.4,.5),(.6,.2,.9,.8)]
        app.roi_start(event(.2,.3));app.roi_finish(event(-1,-1))
        for actual,expected in zip(app.rois[0],(0,0,.3,.4)):self.assertAlmostEqual(actual,expected)
        self.assertEqual(app.rois[1],(.6,.2,.9,.8))

    def test_nested_new_region_works_with_shift_or_add_button(self):
        app,event=self.region_editor();app.rois=[(0,0,1,1)]
        app.roi_start(event(.1,.1,state=1));app.roi_finish(event(.4,.4,state=1))
        self.assertEqual(len(app.rois),2)
        app.new_roi();app.roi_start(event(.6,.6));app.roi_finish(event(.8,.9))
        self.assertEqual(len(app.rois),3);self.assertFalse(app.roi_add_mode)
        app.undo_selected();self.assertEqual(len(app.rois),2)
        app.clear_selected();self.assertEqual(app.rois,[])
        app.undo_selected();self.assertEqual(len(app.rois),2)

    def test_finishing_edit_cancels_uncommitted_drag_and_clears_handles(self):
        app,event=self.region_editor();app.rois=[(.1,.1,.4,.5)];original=list(app.rois)
        app.roi_start(event(.4,.5));app.roi_drag(event(.8,.8));app.finish_selection()
        self.assertEqual(app.rois,original);self.assertIsNone(app.roi_draft)
        self.assertEqual(app.preview.cget('cursor'),'')

    def test_all_resize_edges_stay_inside_image_and_never_invert(self):
        rect=(.2,.2,.8,.8)
        for action in ('n','s','e','w','ne','nw','se','sw'):
            point={'n':(.5,.2),'s':(.5,.8),'e':(.8,.5),'w':(.2,.5),
                   'ne':(.8,.2),'nw':(.2,.2),'se':(.8,.8),'sw':(.2,.8)}[action]
            self.assertEqual(mirror.roi_hit_test([rect],point,(20,90,640,360)),(0,action))
            for dx,dy in ((-2,-2),(2,2),(-2,2),(2,-2)):
                x0,y0,x1,y1=mirror.transform_roi(rect,action,dx,dy)
                self.assertTrue(0<=x0<x1<=1 and 0<=y0<y1<=1)
                self.assertGreaterEqual(x1-x0,.025-1e-9);self.assertGreaterEqual(y1-y0,.025-1e-9)

    def test_starting_and_error_replace_hidden_preview_without_leaking_items(self):
        app=self.app
        self.assertIn('开始后查看后方画面',self.texts())
        app.preview_enabled.set(True);app.toggle_preview()
        app.worker=SimpleNamespace();app.paused=False
        for _ in range(4):
            app._preview_render_key=None
            app.render_preview(mirror.core.FacePacket(capture_info='正在连接摄像头'))
            self.assertEqual(self.texts()[0],'正在连接摄像头')
            self.assertEqual(len(self.texts()),2)
            self.assertNotIn('开始后查看后方画面',self.texts())
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
