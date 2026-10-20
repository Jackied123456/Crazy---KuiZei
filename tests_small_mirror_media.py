"""Local media import uses synthetic pixels and disposable project-local paths."""
from io import BytesIO
from pathlib import Path
import json
import shutil
import unittest
import uuid
from unittest.mock import patch

from PIL import Image
import small_mirror_media as media
from small_mirror_performer import PerformerFrameRenderer


def gif():
    stream=BytesIO()
    a=Image.new('RGB',(12,12),'green');b=Image.new('RGB',(12,12),'white')
    a.save(stream,format='GIF',save_all=True,append_images=[b],duration=100,loop=0)
    return stream.getvalue()


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).parent/('media-test-'+uuid.uuid4().hex)
        self.root.mkdir();self.addCleanup(lambda:shutil.rmtree(self.root))
        self.source=self.root/'input';self.source.mkdir()
        self.data=self.root/'data'
        # A media-free fixture keeps missing/partial-pack tests independent of
        # whether this checkout also contains the 12 optional build inputs.
        self.builtin=self.root/'builtin';self.builtin.mkdir()
        shutil.copyfile(Path(__file__).parent/'assets/yaoshui/programs.json',self.builtin/'programs.json')
        builtin=patch('small_mirror_performer.builtin_asset_dir',return_value=self.builtin)
        builtin.start();self.addCleanup(builtin.stop)

    def test_portable_pack_uses_only_matching_programme_and_can_be_disabled(self):
        sidecar=self.root/'Crazy-local-media';sidecar.mkdir()
        (sidecar/'patrol.gif').write_bytes(gif())
        config=sidecar/'programs.json'
        config.write_bytes((self.builtin/'programs.json').read_bytes())
        executable=self.root/'Crazy.exe'
        self.assertEqual(media.portable_media_dir(self.builtin,executable),sidecar)
        with patch.object(media,'portable_media_dir',return_value=sidecar):
            self.assertEqual(media.media_dir(self.data,self.builtin),sidecar)
            media.clear_packs(self.data,disable_portable=True)
            self.assertEqual(media.media_dir(self.data,self.builtin),self.builtin)
            (self.source/'patrol.gif').write_bytes(gif())
            media.import_pack(self.source,self.data,self.builtin)
            self.assertNotEqual(media.media_dir(self.data,self.builtin),sidecar)
        config.write_text('untrusted programme',encoding='utf-8')
        self.assertEqual(media.portable_media_dir(self.builtin,executable),self.builtin)

    def test_partial_or_broken_import_uses_embedded_clips_before_sentry(self):
        import small_mirror_performer as performer
        embedded=self.root/'embedded';embedded.mkdir()
        for filename in performer.CLIP_FILES.values():(embedded/filename).write_bytes(gif())
        (self.source/'programs.json').write_bytes((self.builtin/'programs.json').read_bytes())
        (self.source/'patrol.gif').write_bytes(b'broken')
        with patch.object(performer,'builtin_asset_dir',return_value=embedded):
            renderer=PerformerFrameRenderer(self.source)
        self.assertEqual(set(renderer._clips),set(performer.CLIP_FILES))
        self.assertEqual(renderer.missing_assets,[])
        self.assertIn('12/12 已加载',renderer.media_status())

    def test_missing_assets_are_visible_in_status(self):
        renderer=PerformerFrameRenderer(self.builtin)
        self.assertIn('0/12',renderer.media_status())
        self.assertIn('原创哨兵',renderer.media_status())

    def test_import_known_slots_and_ignore_unrelated_files_and_config(self):
        (self.source/'patrol.gif').write_bytes(gif())
        (self.source/'unrelated.gif').write_bytes(b'not a GIF')
        (self.source/'programs.json').write_text('untrusted',encoding='utf-8')
        self.assertEqual(media.import_pack(self.source,self.data,self.builtin),1)
        selected=media.media_dir(self.data,self.builtin)
        self.assertNotEqual(selected,self.builtin)
        self.assertEqual({p.name for p in selected.iterdir()},{'patrol.gif','programs.json'})
        self.assertEqual((selected/'programs.json').read_bytes(),(self.builtin/'programs.json').read_bytes())
        renderer=PerformerFrameRenderer(selected)
        self.assertEqual(len(renderer._clips),1)
        self.assertEqual(renderer.render(None,(640,400),.1).size,(640,400))

    def test_failed_reimport_preserves_prior_pointer_and_rolls_back(self):
        (self.source/'patrol.gif').write_bytes(gif())
        media.import_pack(self.source,self.data,self.builtin)
        before=(self.data/'active-media.json').read_bytes()
        (self.source/'frown.gif').write_bytes(b'invalid gif')
        with self.assertRaises((OSError,ValueError)):
            media.import_pack(self.source,self.data,self.builtin)
        self.assertEqual((self.data/'active-media.json').read_bytes(),before)
        self.assertEqual(len(list((self.data/'media-packs').iterdir())),1)

    def test_size_and_frame_limits_reject_untrusted_input(self):
        data=gif()
        with patch.object(media,'MAX_FILE_BYTES',1):
            with self.assertRaises(ValueError):media.validate_gif(data)
        with patch.object(media,'MAX_FRAMES',1):
            with self.assertRaises(ValueError):media.validate_gif(data)
        with patch.object(media,'MAX_FRAME_PIXELS',1):
            with self.assertRaises(ValueError):media.validate_gif(data)

    def test_empty_or_oversized_pack_never_becomes_active(self):
        with self.assertRaises(ValueError):media.import_pack(self.source,self.data,self.builtin)
        self.assertFalse((self.data/'active-media.json').exists())
        (self.source/'patrol.gif').write_bytes(gif())
        with patch.object(media,'MAX_PACK_BYTES',1):
            with self.assertRaises(ValueError):media.import_pack(self.source,self.data,self.builtin)
        self.assertEqual(list((self.data/'media-packs').iterdir()),[])

    def test_pointer_cannot_escape_or_replace_program_definition(self):
        self.data.mkdir()
        (self.data/'active-media.json').write_text(json.dumps({'pack':'../input'}),encoding='utf-8')
        self.assertEqual(media.media_dir(self.data,self.builtin),self.builtin)
        (self.source/'patrol.gif').write_bytes(gif())
        media.import_pack(self.source,self.data,self.builtin)
        active=media.media_dir(self.data,self.builtin)
        (active/'programs.json').write_text('modified',encoding='utf-8')
        self.assertEqual(media.media_dir(self.data,self.builtin),self.builtin)

    def test_delete_imports_preserves_identity_settings_and_unrelated_user_files(self):
        (self.source/'patrol.gif').write_bytes(gif())
        media.import_pack(self.source,self.data,self.builtin)
        for name in ('owner-template.bin','settings.json','keep.txt'):(self.data/name).write_bytes(b'synthetic')
        self.assertEqual(media.clear_packs(self.data),1)
        self.assertEqual(media.media_dir(self.data,self.builtin),self.builtin)
        self.assertTrue(all((self.data/name).read_bytes()==b'synthetic' for name in ('owner-template.bin','settings.json','keep.txt')))

    def test_delete_refuses_unexpected_files_before_modifying_any_pack(self):
        (self.source/'patrol.gif').write_bytes(gif())
        media.import_pack(self.source,self.data,self.builtin)
        active=media.media_dir(self.data,self.builtin)
        (active/'keep.txt').write_text('synthetic',encoding='utf-8')
        with self.assertRaises(ValueError):media.clear_packs(self.data)
        self.assertTrue((active/'patrol.gif').exists())
        self.assertTrue((self.data/'active-media.json').exists())

    def test_ui_import_is_async_and_poll_publishes_success(self):
        import small_mirror as app
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        obj=app.PrivacyApp.__new__(app.PrivacyApp)
        obj.media_task=None;obj.media_poll=None;obj.preferences_window=None
        obj.root=MagicMock();obj.data_dir=self.data;obj.detail_var=MagicMock()
        obj.reload_local_media=MagicMock();obj.closing=False
        (self.source/'patrol.gif').write_bytes(gif())
        # Run the worker synchronously only in this test; production uses Thread.
        def start_thread(**kwargs):return SimpleNamespace(start=kwargs['target'])
        with patch.object(app.filedialog,'askdirectory',return_value=str(self.source)),patch.object(app.messagebox,'askyesno',return_value=True),patch.object(app.threading,'Thread',side_effect=start_thread):
            obj.import_local_media()
        self.assertIsNotNone(obj.media_task)
        obj.poll_media_import()
        obj.reload_local_media.assert_called_once()
        self.assertIsNone(obj.media_task)


if __name__=='__main__':unittest.main()
