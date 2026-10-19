"""Timing, local media reuse and truthful compositor status regression checks."""
import unittest
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from small_mirror_performer import (
    PerformerFrameRenderer, MOTIONS, PHRASES, DEFAULT_MOTIONS, DEFAULT_PHRASES,
    normalize_choices, CLIP_FILES,
)


class PerformerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = PerformerFrameRenderer(Path(__file__).parent/'assets'/'yaoshui')

    def setUp(self):
        self.renderer.configure(DEFAULT_MOTIONS,DEFAULT_PHRASES)

    def test_every_scene_boundary_and_infinite_loop(self):
        schedule = self.renderer.program
        self.assertEqual(schedule.duration_ms,41490)
        self.assertEqual(len(schedule.steps),18)
        for index,(_motion,_style,step,start_ms) in enumerate(schedule.steps):
            at_start = schedule.state(start_ms/1000)
            self.assertEqual(at_start.step_index,index)
            self.assertEqual(at_start.clip,step['clip'])
            self.assertEqual(at_start.local_ms,step.get('start_ms',0))
            just_before_end = schedule.state((schedule.ends[index]-.01)/1000)
            self.assertEqual(just_before_end.step_index,index)
            after_cycles = schedule.state(start_ms/1000+schedule.duration*7)
            self.assertEqual(after_cycles.step_index,index)
        self.assertEqual(schedule.state(schedule.duration).step_index,0)

    def test_choices_and_phrase_rotation(self):
        self.assertEqual(normalize_choices(['combo','bad','combo','patrol'],DEFAULT_MOTIONS,DEFAULT_MOTIONS),['combo','patrol'])
        self.assertEqual(normalize_choices('patrol',DEFAULT_MOTIONS,['punch']),['punch'])
        self.renderer.configure(['judge','combo'],['guard','absurd'])
        timeline = list(self.renderer.program.timeline())
        self.assertEqual([entry[0] for entry in timeline],['judge','combo'])
        self.assertEqual(self.renderer.program.state(0).style,'guard')
        self.assertEqual(self.renderer.program.state(timeline[1][1]).style,'absurd')
        self.assertEqual(self.renderer.program.state(float('nan')).step_index,0)

    @unittest.skipUnless(all((Path(__file__).parent/'assets'/'yaoshui'/name).is_file() for name in CLIP_FILES.values()),
                         'Optional local GIF pack absent; original fallback is tested separately')
    def test_all_frames_predecoded_with_original_durations(self):
        self.assertFalse(self.renderer.missing_assets)
        self.assertEqual(len(self.renderer._clips),12)
        clip = self.renderer._clips['combo']
        self.assertEqual(len(clip.frames),91)
        self.assertEqual(clip.duration_ms,2760)
        self.assertEqual(clip.index_at(clip.ends[0]-.01),0)
        self.assertEqual(clip.index_at(clip.ends[0]),1)
        self.assertEqual(clip.index_at(999999),90)

    def test_status_distinguishes_preview_monitoring_and_actual_mask(self):
        self.assertEqual(self.renderer.status_text(None,False),'效果预览 · 相机未开启')
        self.assertEqual(self.renderer.status_text(object(),False),'观察者画面 · 未开启内容遮挡')
        self.assertEqual(self.renderer.status_text(None,True),'内容已暂时隐藏')
        self.assertEqual(self.renderer.status_text(object(),True),'内容已暂时隐藏')

    def test_fresh_rgb_observer_pil_array_and_no_retained_face(self):
        red = Image.new('RGB',(120,150),'red')
        array = np.asarray(red)
        pil_render = self.renderer.render(red,(640,400),1)
        array_render = self.renderer.render(array,(640,400),1)
        self.assertEqual(pil_render.tobytes(),array_render.tobytes())
        blank = self.renderer.render(None,(640,400),1)
        self.assertNotEqual(pil_render.tobytes(),blank.tobytes())
        self.assertEqual(pil_render.getpixel((450,190)),(255,0,0))
        self.assertNotEqual(blank.getpixel((450,190)),(255,0,0))
        self.assertFalse(any('observer' in key or 'owner' in key for key in vars(self.renderer)))
        for size in ((420,260),(640,400),(1000,620)):
            output = self.renderer.render(None,size,3,protected=True)
            self.assertEqual(output.size,size)
            self.assertEqual(output.mode,'RGB')

    def test_render_reuses_media_without_reading_files_and_bounded_cache(self):
        # All GIFs are decoded before playback. A different scene must not open
        # a media file, even when rendered at a new floating-window size.
        with patch('PIL.Image.open',side_effect=AssertionError('media reopened')):
            for tick in (0,2.7,6.2,14.9,23,31.1,39.5,45):
                self.renderer.render(None,(640,400),tick)
                self.renderer.render(None,(420,260),tick)
        self.assertLessEqual(self.renderer._scaled_bytes,self.renderer.cache_limit_bytes)

    def test_missing_optional_media_uses_original_animation_without_retaining_observer(self):
        with patch('small_mirror_performer._Clip',side_effect=OSError('optional media absent')):
            renderer=PerformerFrameRenderer(Path(__file__).parent/'assets'/'yaoshui')
        self.assertEqual(len(renderer.missing_assets),12)
        first=renderer.render(None,(640,400),0)
        moved=renderer.render(None,(640,400),.8)
        self.assertNotEqual(first.tobytes(),moved.tobytes())
        self.assertEqual(renderer.render(None,(640,400),float('nan')).size,(640,400))
        red=renderer.render(Image.new('RGB',(100,100),'red'),(640,400),.8)
        blank=renderer.render(None,(640,400),.8)
        self.assertEqual(red.getpixel((450,190)),(255,0,0))
        self.assertNotEqual(blank.getpixel((450,190)),(255,0,0))
        self.assertFalse(renderer._clips)

    def test_background_decoder_returns_before_assets_and_shares_only_static_clips(self):
        release=threading.Event()
        entered=threading.Event()
        def slow_clip(_path):
            entered.set()
            if not release.wait(3):raise OSError('test decoder release timed out')
            return SimpleNamespace(frames=[Image.new('RGB',(30,30),'green')],
                                   ends=[100],duration_ms=100,index_at=lambda _tick:0)
        folder=Path(__file__).parent/'assets'/'yaoshui'
        with patch('small_mirror_performer._LOCAL_LIBRARIES',{}),patch('small_mirror_performer._Clip',side_effect=slow_clip):
            try:
                first=PerformerFrameRenderer(folder,background=True)
                self.assertTrue(entered.wait(1))
                self.assertFalse(first._library.ready.is_set())
                self.assertEqual(first.render(None,(640,400),.2).size,(640,400))
                second=PerformerFrameRenderer(folder,background=True)
                self.assertIs(first._library,second._library)
                self.assertIsNot(first._scaled,second._scaled)
            finally:
                release.set()
            self.assertTrue(first.wait_for_assets(2))
            self.assertEqual(len(first._clips),12)
            self.assertFalse(first.missing_assets)


if __name__ == '__main__':
    unittest.main()
