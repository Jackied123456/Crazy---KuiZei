"""Small mirror integration checks; no camera access or real AppData writes."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import hashlib
import json
import tempfile
import shutil
import uuid
import unittest
from unittest.mock import patch,MagicMock
import numpy as np

HERE = Path(__file__).resolve().parent

class Value:
    def __init__(self, value=None): self.value = value; self.options = {}
    def get(self): return self.value
    def set(self, value): self.value = value
    def configure(self, **kwargs): self.options.update(kwargs)
    def winfo_exists(self): return True
class Root:
    def __init__(self): self.calls = []
    def after(self, *args): self.calls.append(('after', args))
    def after_cancel(self, *args): self.calls.append(('after_cancel', args))
    def state(self): return 'withdrawn'
    def destroy(self): self.calls.append(('destroy',))
    def focus_force(self): raise AssertionError('Daily floating windows must not steal focus.')
class Resource:
    def __init__(self): self.stopped = self.closed = False; self.calls = []
    def stop(self): self.stopped = True
    def close(self): self.closed = True
    def withdraw(self): self.calls.append('withdraw')
    def deiconify(self): self.calls.append('deiconify')
    def update(self, *args): self.calls.append(('update', args))
    def destroy(self): self.closed = True
    def configure(self, **kwargs): pass
    def overrideredirect(self, value): pass
    def attributes(self, *args): pass
    def winfo_viewable(self): return True
    def bind(self, *args): pass
    def focus_force(self): self.calls.append('selector-focus')
    def grab_set(self): pass
    def grab_release(self): pass
class Canvas:
    def __init__(self): self.calls = []; self.bindings = {}
    def delete(self, *args): self.calls.append('delete')
    def create_image(self, *args, **kwargs): self.calls.append('image')
    def create_text(self, *args, **kwargs): self.calls.append('text')
    def bind(self, event, callback): self.bindings[event] = callback
    def pack(self, **kwargs): pass
    def configure(self, **kwargs): pass
    def itemconfigure(self, *args, **kwargs): self.calls.append(('itemconfigure',args,kwargs))
    def coords(self, *args): pass
    def tag_raise(self, *args): pass
class Worker(Resource):
    def __init__(self, packet):
        super().__init__(); self.packet = packet
        self.stop_event = SimpleNamespace(is_set=lambda: self.stopped)
        self.identity_version = 0
    def is_alive(self): return True
    def latest(self): return self.packet
    def configure(self, *args, **kwargs): self.calls.append(('configure', args, kwargs))
    def identity_command(self,*args):self.calls.append(('identity',args))

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = importlib.import_module('small_mirror')
        cls.core = importlib.import_module('small_mirror_core')
        cls.events = importlib.import_module('event_stats_v4')
        cls.data_dir = HERE/('integration-test-'+uuid.uuid4().hex)
        cls.data_dir.mkdir()
    @classmethod
    def tearDownClass(cls):
        assert cls.data_dir.resolve().parent==HERE
        shutil.rmtree(cls.data_dir)

    def make_app(self):
        obj = self.app.PrivacyApp.__new__(self.app.PrivacyApp)
        obj.root, obj.data_dir = Root(), self.data_dir
        obj.config = {}
        obj.rois, obj.exclusions, obj.protect_regions, obj.masks = [], [], [], []
        obj.observer_active, obj.observer_absent_since = False, None
        obj.owner_samples = [np.ones(128,dtype=np.float32)]
        obj.enrolling = False;obj.owner_window = None;obj.custom_bar_size = None
        obj.owner_verifying=False;obj.ui_scale=1.
        obj.attention_required=Value(False);obj.effect=Value('普通特写');obj.bugs=Value('关闭')
        obj.record_enabled=Value(False);obj.preview_enabled=Value(False)
        obj.owner_summary=Value();obj.record_summary=Value();obj.record_indicator=Value()
        obj.frame_style=Value('普通特写')
        obj.performer_renderer=None;obj.performer_started_at=None;obj.performer_target=None;obj.tick_after=None
        obj.auto_mask=False;obj.protection_absent_since=None
        obj.attention_since=None;obj.attention_target=None;obj.attention_hits=0;obj.attention_serial=-1
        obj.recorder=SimpleNamespace(target=None,recording=False,error='',finish=lambda:None,close=lambda:None)
        obj.protection_gate=cls_gate=self.events.EventSessionGate(threshold=.3,min_hits=2)
        obj.protection_serial=-1;obj.mask_active=False;obj.protected_windows=[];obj.protected_masks={}
        obj.window_api=obj.window_picker=obj.recordings_panel=None
        obj.selection_mode, obj.debug = 'none', Value(False)
        obj.sensitivity, obj.resolution = Value('高'), Value('1280×720')
        obj.display_size, obj.camera_index = Value('明显 360×280'), Value('0')
        obj.paused = obj.closing = obj.adjusting = obj.demo_bar = obj.bar_shown = False
        obj.bar = obj.bar_canvas = obj.bar_image = obj.stats_window = obj.selector = None
        obj.preview_map = None
        obj.drag_start = obj.drag_end = None
        obj.bar_position = None
        obj.bar_until, obj.last_serial, obj.last_error = 0, -1, ''
        obj.camera_button, obj.index_input, obj.resolution_picker = Value(), Value(), Value()
        obj.detail_var, obj.status_var, obj.region_var, obj.detector_var = Value(), Value(), Value(), Value()
        obj.sensitive = Value(False)
        obj.tray, obj.hotkey = Resource(), Resource()
        obj.worker = Worker(self.core.FacePacket())
        obj.session_gate = obj.new_session_gate()
        short_name = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10]
        obj.stats = self.events.StatsStore(self.data_dir / (short_name + '.json'))
        obj.save_config = lambda: True
        obj.render_preview = lambda packet: None
        return obj

    def test_legacy_single_roi_and_protect_region_migrate_to_lists(self):
        obj = self.make_app()
        obj.config_path = self.data_dir / 'legacy-settings.json'
        obj.config_path.write_text(json.dumps({'roi': [.5, .1, .9, .8], 'protect_region': [30, 40, 200, 100],
                                               'crop_mode': '眼部横条'}), encoding='utf-8')
        config = obj.load_config()
        self.assertEqual([tuple(r) for r in config['rois']], [(.5, .1, .9, .8)])
        self.assertEqual(config['protect_regions'], [[30, 40, 200, 100]])
        self.assertEqual((config['display_size'], config['sensitivity']), ('相框 560×340', '高'))
        self.assertEqual(config['exclusions'], [])

    def tick_face(self, obj, serial, now, face=(100,40,40,40)):
        obj.worker.packet = self.core.FacePacket(serial=serial, timestamp=now,
            frame=np.zeros((180,320,3),dtype=np.uint8), face=face, ready=True,identity_ready=True)
        with patch.object(self.app.time, 'monotonic', return_value=now):
            obj.tick()

    def test_continuous_presence_displays_beyond_five_seconds_and_counts_once(self):
        obj = self.make_app(); obj.rois = [(0,0,1,1)]
        obj.show_bar = lambda *a, **kw: setattr(obj,'bar_shown',True)
        for serial,now in enumerate((0,.2,.51,1,5.1,15,30),1):
            self.tick_face(obj,serial,now)
            if now >= .51:self.assertTrue(obj.bar_shown)
        self.assertEqual(obj.stats.total,1)
        self.assertEqual(obj.bar_until,0)

    def test_short_loss_clears_old_image_and_resumes_without_recounting(self):
        obj = self.make_app(); obj.rois = [(0,0,1,1)]
        obj.show_bar = lambda *a, **kw: setattr(obj,'bar_shown',True)
        for serial,now in enumerate((0,.2,.51),1):self.tick_face(obj,serial,now)
        self.tick_face(obj,4,.8,None)
        self.assertFalse(obj.bar_shown)
        self.assertTrue(obj.observer_active)
        self.tick_face(obj,5,1.0)
        self.assertTrue(obj.bar_shown)
        self.assertEqual(obj.stats.total,1)

    def test_two_seconds_absence_rearms_and_next_visit_requires_acquisition(self):
        obj = self.make_app(); obj.rois = [(0,0,1,1)]
        obj.show_bar = lambda *a, **kw: setattr(obj,'bar_shown',True)
        for serial,now in enumerate((0,.2,.51),1):self.tick_face(obj,serial,now)
        self.tick_face(obj,4,1,None)
        self.tick_face(obj,5,3.01,None)
        self.assertFalse(obj.observer_active or obj.bar_shown)
        self.tick_face(obj,6,3.1)
        self.assertFalse(obj.bar_shown)
        self.tick_face(obj,7,3.3)
        self.tick_face(obj,8,3.61)
        self.assertTrue(obj.bar_shown)
        self.assertEqual(obj.stats.total,2)

    def test_owner_packet_never_displays_or_counts(self):
        obj = self.make_app(); obj.rois = [(0,0,1,1)]
        obj.show_bar = lambda *a, **kw: self.fail('Owner must not be displayed')
        for serial,now in enumerate((0,.2,.51,2,10),1):
            obj.worker.packet=self.core.FacePacket(serial=serial,timestamp=now,identity_ready=True,
                frame=np.zeros((180,320,3),dtype=np.uint8),face=(100,40,40,40),owner_boxes=[(100,40,40,40)])
            with patch.object(self.app.time,'monotonic',return_value=now):obj.tick()
        self.assertEqual(obj.stats.total,0)
        self.assertFalse(obj.observer_active)

    def test_stale_frame_and_pause_clear_display_without_more_counts(self):
        obj = self.make_app(); obj.rois = [(0,0,1,1)]
        obj.show_bar = lambda *a, **kw: setattr(obj,'bar_shown',True)
        for serial,now in enumerate((0,.2,.51),1):self.tick_face(obj,serial,now)
        with patch.object(self.app.time, 'monotonic',return_value=1.5):obj.tick()
        self.assertFalse(obj.bar_shown)
        obj.toggle_pause()
        self.assertTrue(obj.worker.stopped and obj.paused)
        self.assertFalse(obj.observer_active)
        self.assertEqual(obj.stats.total,1)

    def test_duplicate_frame_does_not_acquire_and_delayed_timestamp_does_not_reverse_clock(self):
        obj = self.make_app(); obj.rois = [(0,0,1,1)]
        for now in (0,.2,.51):self.tick_face(obj,1,now)
        self.assertEqual(obj.stats.total,0)
        obj.worker.packet = self.core.FacePacket()
        with patch.object(self.app.time, 'monotonic',return_value=.6):obj.tick()
        obj.worker.packet = self.core.FacePacket(serial=2,timestamp=.59,
            frame=np.zeros((180,320,3),dtype=np.uint8),face=(100,40,40,40),ready=True,identity_ready=True)
        with patch.object(self.app.time, 'monotonic',return_value=.65):obj.tick()

    def test_settings_save_coordinates_only_and_preserve_existing_stats(self):
        obj = self.make_app()
        obj.config_path = self.data_dir/'new-settings.json'
        obj.rois = [(0,0,1,1)];obj.exclusions = [(.25,.1,.6,.9)]
        obj.stats.record_event(self.app.local_now())
        self.assertTrue(self.app.PrivacyApp.save_config(obj))
        saved = json.loads(obj.config_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['version'],10)
        self.assertEqual(saved['exclusions'],[])
        self.assertEqual(set(saved),{'version','rois','exclusions','camera_index','sensitivity','resolution',
                                     'display_size','bar_position','protect_regions','debug','bar_size',
                                     'effect','bugs','record_enabled','attention_required','frame_style',
                                     'performer_motions','performer_phrases'})
        self.assertEqual(self.events.StatsStore(obj.stats.path).total,1)

    def test_unregistered_or_enrolling_never_projects_counts_or_masks(self):
        for enrolled,enrolling in ((False,False),(True,True)):
            obj=self.make_app();obj.rois=[(0,0,1,1)];obj.enrolling=enrolling
            if not enrolled:obj.owner_samples=[]
            obj.show_bar=lambda *a,**kw:self.fail('Registration must suppress projections')
            obj.show_mask=lambda:self.fail('Registration must suppress protection triggers')
            for serial,now in enumerate((0,.2,.51,2),1):self.tick_face(obj,serial,now)
            self.assertEqual(obj.stats.total,0)

    def test_program_config_accepts_only_six_programs_and_three_copy_styles(self):
        obj=self.make_app();obj.config_path=self.data_dir/'program-settings.json'
        self.assertEqual({row[0] for row in self.app.MOTIONS},{'patrol','judge','punch','combo','barrage','encore'})
        self.assertEqual({row[0] for row in self.app.PHRASES},{'cheeky','guard','absurd'})
        obj.config_path.write_text(json.dumps({'performer_motions':['patrol','unknown','judge','patrol'],
                                               'performer_phrases':['guard','unknown','guard']}),encoding='utf-8')
        loaded=obj.load_config()
        self.assertEqual(loaded['performer_motions'],['patrol','judge'])
        self.assertEqual(loaded['performer_phrases'],['guard'])
        obj.config_path.write_text(json.dumps({'performer_motions':['unknown'],
                                               'performer_phrases':False}),encoding='utf-8')
        loaded=obj.load_config()
        self.assertEqual(loaded['performer_motions'],list(self.app.DEFAULT_MOTIONS))
        self.assertEqual(loaded['performer_phrases'],list(self.app.DEFAULT_PHRASES))

    def test_data_directory_is_isolated_from_other_application_versions(self):
        with patch.dict(self.app.os.environ,{'LOCALAPPDATA':'C:/Synthetic/AppData'}):
            self.assertEqual(self.app.default_data_dir(),Path('C:/Synthetic/AppData')/'Crazy'/'PublicBeta')

    def test_floating_frame_only_repositions_when_geometry_changes(self):
        obj=self.make_app();obj.bar=Resource();obj.bar_canvas=Canvas();obj.bar_position=[100,100]
        with patch.object(self.core,'desktop_bounds',return_value=(0,0,1920,1080,1920,1080)), \
             patch.object(self.app,'place_window') as place, \
             patch.object(self.app,'configure_floating_window') as configure:
            obj.ensure_bar();obj.ensure_bar()
            self.assertEqual(place.call_count,1)
            self.assertEqual(configure.call_count,1)
            obj.bar_position=[180,100];obj.ensure_bar()
            self.assertEqual(place.call_count,2)
            obj.adjusting=True;obj.ensure_bar()
            self.assertEqual(place.call_count,2)
            self.assertEqual(configure.call_count,2)

    def test_mask_animation_reuses_canvas_items_without_recreating_them(self):
        self.core.load_dependencies()
        obj=self.make_app();mask=Resource();mask._canvas=Canvas();mask._bounds=(0,0,800,600);obj.masks=[mask]
        picture=self.core.Image.new('RGB',(560,340),'white')
        with patch.object(self.core.ImageTk,'PhotoImage',lambda image:object()):
            self.assertTrue(obj.paint_mask_frames(picture))
            self.assertTrue(obj.paint_mask_frames(picture))
        self.assertEqual(mask._canvas.calls.count('image'),1)
        self.assertEqual(mask._canvas.calls.count('text'),1)
        self.assertNotIn('delete',mask._canvas.calls)
        obj.clear_mask_frames()
        self.assertFalse(mask._performer_active)

    def test_profile_change_rejects_previous_worker_packet(self):
        obj=self.make_app();obj.worker.identity_version=1
        obj.worker.packet=self.core.FacePacket(serial=1,timestamp=1,identity_version=0,
            frame=np.zeros((100,100,3),dtype=np.uint8),identity_ready=True,face=(20,20,40,40))
        with patch.object(self.app.time,'monotonic',return_value=1):
            self.assertIsNone(obj.current_packet().frame)

    def enrollment_app(self):
        obj=self.make_app()
        obj.owner_status=Value();obj.enroll_button=Value();obj.delete_owner_button=Value()
        obj.retry_owner_button=Value()
        obj.remember_owner=Value(True)
        obj.owner_store=SimpleNamespace(save=MagicMock(),delete=MagicMock())
        packet=self.core.FacePacket(enrollment_status={'done':True},
            enrollment_samples=[np.ones(128,dtype=np.float32) for _ in range(12)])
        obj.current_packet=lambda:packet
        obj.enrolling=True;obj.enroll_selected=True
        return obj

    def test_completed_enrollment_saved_and_sent_to_worker(self):
        obj=self.enrollment_app();obj.finish_enrollment()
        obj.owner_store.save.assert_called_once()
        self.assertEqual(len(obj.owner_samples),12)
        self.assertFalse(obj.enrolling)
        self.assertEqual(obj.worker.calls[-1][1][0],'templates')
        self.assertEqual(obj.stats.total,0)

    def test_memory_only_enrollment_removes_previous_saved_template(self):
        obj=self.enrollment_app();obj.remember_owner.set(False);obj.finish_enrollment()
        obj.owner_store.save.assert_not_called();obj.owner_store.delete.assert_called_once()
        self.assertEqual(len(obj.owner_samples),12)

    def test_failed_save_keeps_enrollment_and_previous_memory_template(self):
        obj=self.enrollment_app();old=obj.owner_samples
        obj.owner_store.save.side_effect=OSError('injected write failure')
        obj.finish_enrollment()
        self.assertIs(obj.owner_samples,old)
        self.assertTrue(obj.enrolling)
        self.assertFalse(obj.worker.calls)

    def test_cancel_keeps_existing_profile(self):
        obj=self.enrollment_app();old=obj.owner_samples
        obj.cancel_enrollment()
        self.assertIs(obj.owner_samples,old)
        obj.owner_store.delete.assert_not_called()
        self.assertFalse(obj.enrolling)

    def test_delete_clears_memory_and_worker_profile_and_stops_display(self):
        obj=self.enrollment_app();obj.enrolling=False
        with patch.object(self.app.messagebox,'askyesno',return_value=True):obj.delete_owner_template()
        obj.owner_store.delete.assert_called_once()
        self.assertEqual(obj.owner_samples,[])
        self.assertEqual(obj.worker.calls[-1][1],('templates',[]))
        self.assertFalse(obj.bar_shown or obj.observer_active)

    def test_roi_add_undo_clear_preserve_previous_areas_and_refresh_worker(self):
        obj = self.make_app()
        first, second = (.1, .1, .3, .6), (.5, .1, .9, .8)
        obj.add_roi(first); obj.add_roi(second)
        self.assertEqual(obj.rois, [first, second])
        obj.undo_roi()
        self.assertEqual(obj.rois, [first])
        obj.clear_roi()
        self.assertEqual(obj.rois, [])
        self.assertEqual(obj.worker.calls[-1][1][0], [])

    def test_protect_regions_append_undo_clear_and_all_masks_restore(self):
        obj = self.make_app()
        obj.protect_regions = [[20, 20, 100, 80]]
        obj.masks, obj.sensitive = [Resource()], Value(True)
        initial_mask = obj.masks[0]
        canvas = Canvas()
        label = SimpleNamespace(pack=lambda **kw: None, bind=lambda *a: None)
        windows = []
        def make_window(*args):
            window = Resource(); windows.append(window); return window
        with patch.object(self.core, 'desktop_bounds', return_value=(0, 0, 1920, 1080, 1920, 1080)), \
             patch.object(self.app.tk, 'Toplevel', side_effect=make_window), \
             patch.object(self.app.tk, 'Canvas', return_value=canvas), \
             patch.object(self.app.tk, 'Label', return_value=label), \
             patch.object(self.app, 'place_window') as place, \
             patch.object(self.app, 'configure_floating_window'):
            obj.select_protect_region()
            canvas.bindings['<ButtonPress-1>'](SimpleNamespace(x=100, y=100))
            canvas.bindings['<ButtonRelease-1>'](SimpleNamespace(x=300, y=240))
            self.assertEqual(obj.protect_regions, [[20, 20, 100, 80], [100, 100, 200, 140]])
            self.assertTrue(initial_mask.closed)
            self.assertEqual(len(obj.masks), 2, 'Editing active protection must rebuild all valid masks.')
            obj.show_mask()
            self.assertEqual(len(obj.masks), 2)
            old_masks = list(obj.masks)
            obj.restore()
            self.assertTrue(all(mask.closed for mask in old_masks))
            self.assertEqual(obj.masks, [])
            obj.undo_protect_region()
            self.assertEqual(obj.protect_regions, [[20, 20, 100, 80]])
            obj.clear_protect_regions()
            self.assertEqual(obj.protect_regions, [])
            self.assertFalse(obj.sensitive.get())

    def test_undo_while_protecting_rebuilds_remaining_masks_and_disables_when_empty(self):
        obj = self.make_app()
        obj.protect_regions = [[20, 20, 100, 80], [200, 200, 100, 80]]
        obj.masks, obj.sensitive = [Resource(), Resource()], Value(True)
        old_masks = list(obj.masks)
        def rebuild(): obj.masks = [Resource() for _ in obj.protect_regions]
        obj.show_mask = rebuild
        with patch.object(self.core, 'desktop_bounds', return_value=(0, 0, 1920, 1080, 1920, 1080)):
            obj.undo_protect_region()
            self.assertTrue(all(mask.closed for mask in old_masks))
            self.assertEqual((len(obj.protect_regions), len(obj.masks)), (1, 1))
            self.assertTrue(obj.sensitive.get())
            obj.undo_protect_region()
        self.assertEqual((obj.protect_regions, obj.masks), ([], []))
        self.assertFalse(obj.sensitive.get())

    def test_head_window_sizes_real_crop_and_no_activation_flags(self):
        obj = self.make_app()
        self.assertEqual(obj.bar_size(), (360, 280))
        sizes = set()
        for name in self.app.HEAD_SIZES:
            obj.display_size.set(name); sizes.add(obj.bar_size())
        self.assertEqual(sizes, {(320, 240), (360, 280), (480, 360)})
        obj.display_size.set('明显 360×280')
        obj.rois = [(.25, .1, .6, .9)]
        obj.bar, obj.bar_canvas = Resource(), Canvas()
        obj.bar.focus_force = lambda: self.fail('Daily head window must not force focus.')
        obj.ensure_bar = lambda: None
        frame = np.full((180, 320, 3), 10, dtype=np.uint8)
        frame[18:162, 80:192] = 20
        resized = []
        class Picture:
            def __init__(self, pixels): self.height, self.width = pixels.shape[:2]
            def resize(self, size, *args): resized.append(size); return self
        def fromarray(pixels):
            self.assertTrue(np.all(pixels == 20), 'Expanded head crop included own-face region.')
            return Picture(pixels)
        image = SimpleNamespace(fromarray=fromarray, Resampling=SimpleNamespace(BILINEAR=1))
        cv = SimpleNamespace(cvtColor=lambda frame, code: frame, COLOR_BGR2RGB=1)
        with patch.object(self.core, 'Image', image), patch.object(self.core, 'cv2', cv), \
             patch.object(self.core, 'ImageTk', SimpleNamespace(PhotoImage=lambda picture: object())), \
             patch.object(self.app, 'configure_floating_window') as configure:
            obj.show_bar(self.core.FacePacket(frame=frame, face=(100, 40, 40, 40),identity_ready=True))
        self.assertTrue(obj.bar_shown and resized)
        self.assertIn('image', obj.bar_canvas.calls)
        configure.assert_called_once_with(obj.bar, click_through=True, no_activate=True)

    def test_sensitivity_thresholds_and_overlapping_rois_emit_one_session_event(self):
        self.assertEqual(self.core.sensitivity_threshold('高'), .55)
        self.assertEqual(self.core.sensitivity_threshold('标准'), .75)
        rows = np.zeros((1, 15), dtype=np.float32)
        rows[0, :4] = [100, 40, 40, 40]
        rows[0, -1] = .65
        rois = [(.25, .1, .6, .9), (.2, .05, .7, .95)]
        high = self.core._interpret_faces(rows, 320, 180, rois, '高')
        standard = self.core._interpret_faces(rows, 320, 180, rois, '标准')
        self.assertEqual(len(high.faces), 1)
        self.assertIsNotNone(high.face)
        self.assertIsNone(standard.face)
        self.assertFalse(standard.faces[0]['accepted'])
        obj = self.make_app(); obj.rois = rois
        obj.show_bar = lambda *a, **k: None
        clock = [0.0]
        with patch.object(self.app.time, 'monotonic', lambda: clock[0]):
            for serial, now in enumerate((0, .2, .51, 1, 3, 6), start=1):
                clock[0] = now
                obj.worker.packet = self.core.FacePacket(serial=serial, timestamp=now, frame=np.zeros((180,320,3),dtype=np.uint8),
                    face=high.face, roi=high.roi, region_index=high.region_index, faces=high.faces, ready=True,identity_ready=True)
                obj.tick()
        self.assertEqual(obj.stats.total, 1)

    def test_capture_reports_actual_size_and_zoom_request_without_physical_wide_claim(self):
        self.core.load_dependencies()
        worker = self.core.CameraWorker(0)
        worker.configure([(.5, 0, 1, 1)], resolution='1920×1080')
        frame, snapshots = np.zeros((900, 1600, 3), dtype=np.uint8), []
        class Capture:
            def __init__(self): self.properties = []; self.reads = 0; self.released = False
            def isOpened(self): return True
            def set(self, prop, value): self.properties.append((prop, value)); return prop != self_core.cv2.CAP_PROP_ZOOM
            def get(self, prop): return 2.5
            def read(self):
                self.reads += 1
                if self.reads == 1: return True, frame
                snapshots.append(worker.latest()); worker.stop(); return False, None
            def release(self): self.released = True
        self_core = self.core
        capture = Capture()
        detector = SimpleNamespace(detect=lambda original, rois, sensitivity, exclusions: self.core.DetectionResult())
        with patch.object(self.core.cv2, 'VideoCapture', return_value=capture), \
             patch.object(self.core, 'YuNetDetector', return_value=detector): worker.run()
        self.assertTrue(capture.released)
        packet = snapshots[0]
        self.assertIs(packet.frame, frame)
        self.assertIn('实际 1600×900（请求 1920×1080）', packet.capture_info)
        self.assertIn('未接受/未支持', packet.capture_info)
        self.assertIn('设备回报 2.5', packet.capture_info)
        self.assertIn('镜头视角由硬件决定', packet.capture_info)
        self.assertIn((self.core.cv2.CAP_PROP_ZOOM, 0), capture.properties)

    def test_acquisition_tolerates_short_dropout_and_requires_three_hits(self):
        gate = self.events.EventSessionGate()
        self.assertFalse(gate.observe(0, True))
        self.assertFalse(gate.observe(.2, False))
        self.assertFalse(gate.observe(.4, True))
        self.assertTrue(gate.observe(.51, True))
        self.assertFalse(gate.observe(2, False))
        self.assertFalse(gate.observe(3.5, True))  # Existing visit, even after acquisition dropout.
        self.assertFalse(gate.observe(4, False))
        self.assertFalse(gate.observe(6.01, False))
        self.assertFalse(gate.observe(6.1, True))
        self.assertFalse(gate.observe(6.4, True))
        self.assertTrue(gate.observe(6.61, True))
        gate.reset()
        self.assertFalse(gate.observe(0, True))
        self.assertFalse(gate.observe(.1, False))
        self.assertFalse(gate.observe(.6, True))  # Long acquisition miss resets candidate.
        self.assertFalse(gate.observe(.8, True))
        self.assertTrue(gate.observe(1.11, True))

    def test_multi_roi_normalization_matching_and_head_crop_stay_inside_one_roi(self):
        first = (.25, .1, .6, .9)
        second = (.65, .1, .95, .9)
        self.assertEqual([tuple(r) for r in self.core.normalize_rois(first)], [first])
        self.assertEqual([tuple(r) for r in self.core.normalize_rois([first, second])], [first, second])
        face = (100, 40, 40, 40)
        self.assertEqual(self.core.matching_region(face, 320, 180, [first, second]), 0)
        self.assertIsNone(self.core.matching_region((0, 40, 30, 30), 320, 180, [first, second]))
        self.assertIsNone(self.core.observer_crop((0, 40, 30, 30), 320, 180, first))
        bounds = self.core.observer_crop(face, 320, 180, first)
        left, top, right, bottom = bounds
        self.assertTrue(80 <= left < 100 and 18 <= top < 40)
        self.assertTrue(140 < right <= 192 and 80 < bottom <= 162)
        synthetic = np.full((180, 320, 3), 10, dtype=np.uint8)
        synthetic[18:162, 80:192] = 20
        self.assertTrue(np.all(synthetic[top:bottom, left:right] == 20), 'Head crop exposed pixels outside the observer ROI.')
        self.assertIsNone(self.core.observer_crop(face, 320, 180, None))

    def test_official_local_model_can_run_actual_inference_without_a_camera(self):
        self.core.load_dependencies()
        detector = self.core.YuNetDetector()
        result = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8), [(0, 0, 1, 1)], sensitivity='高')
        self.assertTrue(hasattr(result, 'face') and hasattr(result, 'faces'))
        self.assertIsInstance(result.faces, (list, tuple))

    def observer_packet(self,serial,now,target=1,present=True):
        face=(100,40,40,40)
        row=np.array([100,40,40,40,110,50,130,50,120,62,112,74,128,74,.99],dtype=np.float32)
        return self.core.FacePacket(serial=serial,timestamp=now,ready=True,identity_ready=True,
            frame=np.full((180,320,3),80,dtype=np.uint8),face=face if present else None,
            faces=[{'bbox':face,'row':row,'track_id':target}] if present else [])

    def test_attention_requires_a_second_per_target_and_resets_on_turning_away(self):
        obj=self.make_app();obj.rois=[(0,0,1,1)];obj.attention_required.set(True)
        obj.session_gate=obj.new_session_gate()
        for serial,now,expected in ((1,0,False),(2,.5,False),(3,1.01,True)):
            self.assertEqual(obj.observe_packet(self.observer_packet(serial,now),now),expected)
        self.assertEqual(obj.stats.total,1)
        for serial,now,expected in ((4,1.1,False),(5,1.6,False),(6,2.11,True)):
            self.assertEqual(obj.observe_packet(self.observer_packet(serial,now,2),now),expected)
        away=self.observer_packet(7,2.2,2);away.faces[0]['row'][8]=134
        self.assertFalse(obj.observe_packet(away,2.2))
        for serial,now,expected in ((8,2.3,False),(9,2.8,False),(10,3.31,True)):
            self.assertEqual(obj.observe_packet(self.observer_packet(serial,now,2),now),expected)

    def test_attention_duplicate_frames_never_acquire(self):
        obj=self.make_app();obj.rois=[(0,0,1,1)];obj.attention_required.set(True)
        obj.session_gate=obj.new_session_gate()
        packet=self.observer_packet(1,0)
        for now in (0,.6,1.2,3):self.assertFalse(obj.observe_packet(packet,now))
        self.assertEqual(obj.stats.total,0)

    def test_automatic_cover_rearms_after_restore_before_two_second_visit_gap(self):
        obj=self.make_app();obj.rois=[(0,0,1,1)];obj.sensitive.set(True)
        obj.has_protection=lambda:True;starts=[]
        def show(automatic=False):
            starts.append(automatic);obj.mask_active=True;obj.auto_mask=automatic
        obj.show_mask=show
        for serial,now,present in ((1,0,True),(2,.31,True),(3,.5,False),(4,1.71,False)):
            obj.process_protection(self.observer_packet(serial,now,present=present),now)
        self.assertEqual(starts,[True]);self.assertFalse(obj.mask_active)
        # The observer comes back before the statistics gate's two-second gap.
        for serial,now in ((5,1.8),(6,2.11)):
            obj.process_protection(self.observer_packet(serial,now),now)
        self.assertEqual(starts,[True,True]);self.assertTrue(obj.mask_active)

    def test_manual_cover_is_not_automatically_restored(self):
        obj=self.make_app();obj.mask_active=True;obj.auto_mask=False
        for now in (0,1.3,4):obj.process_protection(self.core.FacePacket(),now)
        self.assertTrue(obj.mask_active)

    def test_protection_can_be_enabled_after_an_observer_is_already_present(self):
        for initially_has_window in (True,False):
            obj=self.make_app();obj.rois=[(0,0,1,1)]
            selected=[initially_has_window];obj.has_protection=lambda:selected[0]
            obj.sensitive.set(not initially_has_window);starts=[]
            obj.show_mask=lambda automatic=False:starts.append(automatic)
            for serial,now in ((1,0),(2,.31)):
                obj.process_protection(self.observer_packet(serial,now),now)
            self.assertEqual(starts,[])
            selected[0]=True;obj.sensitive.set(True)
            for serial,now in ((3,.6),(4,.91)):
                obj.process_protection(self.observer_packet(serial,now),now)
            self.assertEqual(starts,[True])

    def test_camera_fault_does_not_reveal_content_but_valid_absence_can_restore_it(self):
        obj=self.make_app();obj.rois=[(0,0,1,1)];obj.sensitive.set(True)
        obj.has_protection=lambda:True;obj.auto_mask=True;obj.mask_active=True
        for now in (0,1.3,10):
            obj.process_protection(self.core.FacePacket(error='Camera unavailable'),now)
            self.assertTrue(obj.mask_active)
        for serial,now in ((1,10.1),(2,11.31)):
            obj.process_protection(self.observer_packet(serial,now,present=False),now)
        self.assertFalse(obj.mask_active)

    def performer_app(self):
        self.core.load_dependencies()
        obj=self.make_app();obj.rois=[(0,0,1,1)]
        obj.frame_style.set(self.app.FRAME_STYLES[0]);obj.display_size.set('相框 560×340')
        obj.bar=Resource();obj.bar_canvas=Canvas();obj.ensure_bar=lambda:None
        calls=[]
        def render(rgb,size,elapsed,protected=False):
            calls.append((rgb,size,elapsed,protected))
            return self.core.Image.new('RGB',size,'white')
        obj.performer_renderer=SimpleNamespace(render=render)
        return obj,calls

    def test_performer_camera_off_preview_advances_without_recording_counts_or_masks(self):
        obj,calls=self.performer_app();obj.paused=True;obj.worker=None
        with patch.object(self.core.ImageTk,'PhotoImage',lambda image:object()), \
             patch.object(self.app,'configure_floating_window'):
            with patch.object(self.app.time,'monotonic',return_value=50):obj.preview_performer()
            with patch.object(self.app.time,'monotonic',return_value=51.2):obj.tick()
        self.assertAlmostEqual(calls[-1][2],1.2)
        self.assertIsNone(calls[-1][0]);self.assertFalse(calls[-1][3])
        self.assertIsNone(obj.worker);self.assertIsNone(obj.recorder.target)
        self.assertEqual(obj.stats.total,0);self.assertEqual(obj.masks,[])
        self.assertTrue(obj.demo_bar);self.assertEqual(obj.bar_until,float('inf'))
        obj.preview_performer()
        self.assertFalse(obj.demo_bar or obj.bar_shown)

    def test_performer_animation_follows_current_target_and_hides_stale_image(self):
        obj,calls=self.performer_app()
        with patch.object(self.core.ImageTk,'PhotoImage',lambda image:object()), \
             patch.object(self.app,'configure_floating_window'):
            for now,target in ((50,1),(53,1),(54,2)):
                with patch.object(self.app.time,'monotonic',return_value=now):
                    obj.show_bar(self.observer_packet(int(now),now,target))
        self.assertEqual([call[2] for call in calls],[0,3,0])
        obj.hide_bar()
        self.assertFalse(obj.bar_shown);self.assertIsNone(obj.bar_image)
        self.assertIsNone(obj.performer_started_at)

    def test_performer_on_mask_uses_protection_wording_and_suppresses_duplicate_float(self):
        obj,calls=self.performer_app();obj.masks=[Resource()];obj.bar_shown=True
        obj.mask_active=True;obj.coverage_expected=1;obj.coverage_checked=1;obj.coverage_errors=[]
        obj.paint_mask_frames=MagicMock(return_value=True)
        with patch.object(self.app.time,'monotonic',return_value=5):
            obj.show_bar(self.observer_packet(1,5))
        self.assertTrue(calls[-1][3]);obj.paint_mask_frames.assert_called_once()
        self.assertFalse(obj.bar_shown);self.assertIn('withdraw',obj.bar.calls)

    def test_recording_gets_clean_observer_crop_with_performer_and_effects_selected(self):
        obj,calls=self.performer_app();obj.record_enabled.set(True)
        obj.effect.set('大头');obj.bugs.set('特写里')
        packet=self.observer_packet(1,5);original=packet.frame.copy()
        crops=[];obj.recorder.feed=lambda crop,*args:crops.append(crop)
        obj.process_recording(packet,5,True)
        self.assertEqual(len(crops),1)
        np.testing.assert_array_equal(crops[0],obj.clean_observer_crop(packet))
        np.testing.assert_array_equal(packet.frame,original)
        self.assertEqual(calls,[])

if __name__ == '__main__': unittest.main(verbosity=2)
