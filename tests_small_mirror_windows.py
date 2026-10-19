"""Window binding safety, thumbnail ownership and local playback lifecycle."""
import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

import small_mirror_windows as windows
import small_mirror_panels as panels


class WindowBindingTests(unittest.TestCase):
    def setUp(self):
        self.u,self.d=MagicMock(),MagicMock()
        self.u.SetWinEventHook.return_value=10
        self.u.IsWindow.return_value=True
        self.u.IsWindowVisible.return_value=True
        self.u.IsIconic.return_value=False
        self.u.GetWindowTextLengthW.return_value=20
        def process(_hwnd,pid):pid._obj.value=100;return 101
        def title(_hwnd,buffer,_size):buffer.value='Test window';return 11
        def kind(_hwnd,buffer,_size):buffer.value='ExampleClass';return 12
        self.u.GetWindowThreadProcessId.side_effect=process
        self.u.GetWindowTextW.side_effect=title
        self.u.GetClassNameW.side_effect=kind
        with patch.object(ctypes,'WinDLL',side_effect=[self.u,self.d]):
            self.api=windows.WindowAPI()
        self.token=self.api.token(99)
        self.mask=MagicMock();self.mask.winfo_id.return_value=200
        self.u.GetAncestor.return_value=201
        self.u.SetWindowPos.return_value=True

    def test_title_change_keeps_binding(self):
        self.u.GetWindowTextW.side_effect=lambda _h,buffer,_s:setattr(buffer,'value','Renamed')
        self.assertTrue(self.api.valid(self.token))

    def test_closed_window_is_unbound(self):
        self.u.IsWindow.return_value=False
        self.assertFalse(self.api.valid(self.token))
        self.assertIsNone(self.api.bounds(self.token))
        self.assertFalse(self.api.cover(self.mask,self.token,(1,2,3,4)))
        self.u.SetWindowPos.assert_not_called()

    def test_same_process_handle_reuse_is_unbound_after_destroy(self):
        self.api.callback(None,0x8001,99,0,0,101,0)
        self.assertFalse(self.api.valid(self.token))
        self.assertEqual(self.api.token(99).epoch,1)

    def test_child_destroy_does_not_unbind_window(self):
        self.api.callback(None,0x8001,99,-4,0,101,0)
        self.assertTrue(self.api.valid(self.token))

    def test_different_process_handle_reuse_is_unbound(self):
        def process(_hwnd,pid):pid._obj.value=200;return 101
        self.u.GetWindowThreadProcessId.side_effect=process
        self.assertFalse(self.api.valid(self.token))

    def test_minimized_and_hidden_sources_have_no_cover_bounds(self):
        self.u.IsIconic.return_value=True
        self.assertIsNone(self.api.bounds(self.token))
        self.u.IsIconic.return_value=False;self.u.IsWindowVisible.return_value=False
        self.assertIsNone(self.api.bounds(self.token))
        self.u.GetWindowRect.assert_not_called()

    def test_source_moves_and_resizes_are_read_live(self):
        def rect(_hwnd,value):
            value._obj.left=-100;value._obj.top=30;value._obj.right=500;value._obj.bottom=430
            return True
        self.u.GetWindowRect.side_effect=rect
        self.assertEqual(self.api.bounds(self.token),(-100,30,600,400))

    def test_adjacent_mask_is_demoted_before_preserving_z_order(self):
        self.u.GetWindowLongPtrW.side_effect=lambda hwnd,_index:8 if hwnd==201 else 0
        self.u.GetWindow.return_value=201
        self.assertTrue(self.api.cover(self.mask,self.token,(10,20,400,300)))
        calls=self.u.SetWindowPos.call_args_list
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].args[1].value,ctypes.c_void_p(-2).value)
        self.assertEqual(calls[1].args,(201,None,10,20,400,300,0x10|0x4))

    def test_cover_stays_below_unrelated_foreground_window(self):
        self.u.GetWindowLongPtrW.return_value=0;self.u.GetWindow.return_value=300
        self.assertTrue(self.api.cover(self.mask,self.token,(10,20,400,300)))
        self.u.SetWindowPos.assert_called_once_with(201,300,10,20,400,300,0x10)

    def register(self):
        def register(_destination,_source,result):result._obj.value=555;return 0
        self.d.DwmRegisterThumbnail.side_effect=register
        return self.api.thumbnail(self.mask,self.token)

    def test_thumbnail_checks_binding_before_registration(self):
        self.u.IsWindow.return_value=False
        self.assertIsNone(self.api.thumbnail(self.mask,self.token))
        self.d.DwmRegisterThumbnail.assert_not_called()

    def test_thumbnail_release_and_api_close_are_idempotent(self):
        thumb=self.register()
        self.api.close_thumbnail(thumb);self.api.close_thumbnail(thumb)
        self.api.close();self.api.close()
        self.d.DwmUnregisterThumbnail.assert_called_once_with(thumb)
        self.u.UnhookWinEvent.assert_called_once_with(10)

    def test_api_close_releases_remaining_thumbnail(self):
        self.register();self.api.close()
        self.d.DwmUnregisterThumbnail.assert_called_once_with(555)
        self.assertEqual(self.api.thumbnails,set())


class LocalRecordingPanelTests(unittest.TestCase):
    def setUp(self):
        self.panel=panels.RecordingsPanel.__new__(panels.RecordingsPanel)
        self.panel.window=MagicMock();self.panel.canvas=MagicMock()
        self.panel.capture=MagicMock();self.capture=self.panel.capture
        self.panel.playing=False;self.panel.after_id=None;self.panel.image=object()
        self.panel.directory=Path('work/v7-window-check/recordings').resolve()
        self.panel.tree=MagicMock();self.panel.info=MagicMock()
        self.panel.current=None
        self.panel.paths={'0':self.panel.directory/'test.avi'}
        self.panel.app=SimpleNamespace(recordings_panel=self.panel)

    def test_pause_cancels_timer_before_resume(self):
        self.panel.playing=True;self.panel.after_id='scheduled'
        self.panel.play()
        self.assertFalse(self.panel.playing)
        self.panel.window.after_cancel.assert_called_once_with('scheduled')
        self.assertIsNone(self.panel.after_id)
        self.capture.release.assert_not_called()

    def test_close_releases_decoder_timer_and_preview(self):
        self.panel.after_id='scheduled';self.panel.close()
        self.capture.release.assert_called_once_with()
        self.panel.window.after_cancel.assert_called_once_with('scheduled')
        self.panel.canvas.delete.assert_called_once_with('all')
        self.assertIsNone(self.panel.capture);self.assertIsNone(self.panel.image)
        self.assertIsNone(self.panel.app.recordings_panel)

    def test_unopened_file_releases_failed_decoder(self):
        self.panel.tree.selection.return_value=('0',)
        failed=MagicMock();failed.isOpened.return_value=False
        with patch.object(panels.core,'cv2',SimpleNamespace(VideoCapture=MagicMock(return_value=failed))):
            self.panel.select()
        self.capture.release.assert_called_once_with()
        failed.release.assert_called_once_with()
        self.assertIsNone(self.panel.capture)

    def test_stale_selection_does_not_open_file(self):
        self.panel.tree.selection.return_value=('missing',)
        with patch.object(panels.core,'cv2',SimpleNamespace(VideoCapture=MagicMock())) as cv:
            self.panel.select();cv.VideoCapture.assert_not_called()

    def test_external_path_is_not_opened(self):
        self.panel.tree.selection.return_value=('0',)
        self.panel.paths['0']=self.panel.directory.parent/'external.avi'
        with patch.object(panels.core,'cv2',SimpleNamespace(VideoCapture=MagicMock())) as cv:
            self.panel.select();cv.VideoCapture.assert_not_called()
        self.assertIsNone(self.panel.current)

    def test_changed_path_cannot_be_deleted_and_decoder_is_released(self):
        self.panel.current=self.panel.directory.parent/'external.avi'
        with patch.object(panels.messagebox,'askyesno',return_value=True),patch.object(Path,'unlink') as remove:
            self.panel.delete();remove.assert_not_called()
        self.capture.release.assert_called_once_with()
        self.assertIn('没能删除',self.panel.info.set.call_args.args[0])


if __name__=='__main__':unittest.main(verbosity=2)
