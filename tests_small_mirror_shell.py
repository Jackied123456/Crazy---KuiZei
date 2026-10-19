"""Mock native calls: simple tray labels, callback compatibility and cleanup."""

import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

import small_mirror_shell as shell


class NativeShellTests(unittest.TestCase):
    def setUp(self):
        self.user = MagicMock()
        self.user.RegisterWindowMessageW.return_value = 0x8888
        self.user.LoadIconW.return_value = 123
        self.user.CreatePopupMenu.return_value = 456
        self.user.AppendMenuW.return_value = True
        self.user.GetCursorPos.return_value = True
        self.user.TrackPopupMenu.return_value = 0
        self.native_shell = MagicMock()
        self.native_shell.Shell_NotifyIconW.return_value = True
        self.api = SimpleNamespace(user32=self.user, shell32=self.native_shell)
        self.window = MagicMock()
        self.window.hwnd = 789
        self.root = MagicMock()
        self.callbacks = [MagicMock(name=f"callback_{number}") for number in range(1, 7)]
        self.api_patch = patch.object(shell, "_get_api", return_value=self.api)
        self.window_patch = patch.object(shell, "_MessageWindow", return_value=self.window)
        self.api_patch.start()
        self.window_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.addCleanup(self.window_patch.stop)
        self.tray = shell.NativeTray(self.root, *self.callbacks)

    def test_initial_tooltip_and_native_icon_flags(self):
        self.assertEqual(self.tray.icon_data.szTip, "窥贼 · Crazy · 监测中")
        self.assertEqual(self.tray.icon_data.uFlags, shell.NIF_MESSAGE | shell.NIF_ICON | shell.NIF_TIP)
        self.assertEqual(self.tray.icon_data.uCallbackMessage, shell.WM_TRAY)
        self.assertEqual(self.tray.icon_data.uID, 1)
        self.assertEqual(self.native_shell.Shell_NotifyIconW.call_args.args[0], shell.NIM_ADD)

    def test_menu_contains_exactly_four_simple_items(self):
        self.tray._show_menu()
        self.assertEqual(self.user.AppendMenuW.call_args_list, [
            call(456, shell.MF_STRING, 1, "打开窥贼"),
            call(456, shell.MF_STRING, 2, "暂停"),
            call(456, shell.MF_STRING, 4, "统计"),
            call(456, shell.MF_STRING, 6, "退出"),
        ])
        self.user.DestroyMenu.assert_called_once_with(456)
        flags = self.user.TrackPopupMenu.call_args.args[1]
        self.assertEqual(flags, shell.TPM_RIGHTBUTTON | shell.TPM_RETURNCMD | shell.TPM_NONOTIFY)

    def test_paused_label_and_tooltip_do_not_expose_sensitive_mode(self):
        self.tray.update(paused=True, sensitive=True)
        self.assertTrue(self.tray.sensitive)
        self.assertEqual(self.tray.icon_data.szTip, "窥贼 · Crazy · 已暂停")
        self.tray._show_menu()
        self.assertEqual(self.user.AppendMenuW.call_args_list[1], call(456, shell.MF_STRING, 2, "开始"))
        self.assertEqual(len(self.user.AppendMenuW.call_args_list), 4)
        self.tray.update(paused=False, sensitive=True)
        self.assertEqual(self.tray.icon_data.szTip, "窥贼 · Crazy · 监测中")

    def test_hidden_callbacks_remain_compatible_and_visible_callbacks_dispatch(self):
        self.assertEqual(set(self.tray.callbacks), {1, 2, 3, 4, 5, 6})
        self.assertIs(self.tray.callbacks[3], self.callbacks[2])
        self.assertIs(self.tray.callbacks[5], self.callbacks[4])
        for command in (1, 2, 4, 6):
            with self.subTest(command=command):
                self.user.TrackPopupMenu.return_value = command
                self.tray._show_menu()
                self.window.schedule.assert_called_with(self.callbacks[command - 1])

    def test_double_click_keeps_open_callback(self):
        self.tray._on_message(self.tray.hwnd, shell.WM_TRAY, 1, shell.WM_LBUTTONDBLCLK)
        self.window.schedule.assert_called_once_with(self.callbacks[0])

    def test_tray_close_is_idempotent_and_unregisters_icon(self):
        self.native_shell.Shell_NotifyIconW.reset_mock()
        self.tray.close()
        self.tray.close()
        self.assertEqual(self.native_shell.Shell_NotifyIconW.call_count, 1)
        self.assertEqual(self.native_shell.Shell_NotifyIconW.call_args.args[0], shell.NIM_DELETE)
        self.window.close.assert_called_once_with()
        self.assertIsNone(self.tray.hwnd)
        self.assertTrue(self.tray.closed)

    def test_menu_is_destroyed_on_build_failure(self):
        self.user.AppendMenuW.return_value = False
        with patch.object(shell, "_error", return_value=RuntimeError("append failed")):
            with self.assertRaisesRegex(RuntimeError, "append failed"):
                self.tray._show_menu()
        self.user.DestroyMenu.assert_called_once_with(456)

    def test_taskbar_restart_restores_icon_and_closed_menu_does_nothing(self):
        self.native_shell.Shell_NotifyIconW.reset_mock()
        self.assertEqual(self.tray._on_message(self.tray.hwnd, self.tray.taskbar_created, 0, 0), 0)
        self.assertEqual(self.native_shell.Shell_NotifyIconW.call_args.args[0], shell.NIM_ADD)
        self.tray.close()
        self.tray._show_menu()
        self.user.CreatePopupMenu.assert_not_called()

    def test_hotkey_keeps_no_repeat_flag_and_unregisters_once(self):
        self.user.RegisterHotKey.return_value = True
        hotkey = shell.GlobalHotkey(self.root, MagicMock())
        self.user.RegisterHotKey.assert_called_once_with(
            789, 1, shell.MOD_CONTROL | shell.MOD_ALT | shell.MOD_NOREPEAT, shell.VK_H)
        self.window.close.reset_mock()
        hotkey.close()
        hotkey.close()
        self.user.UnregisterHotKey.assert_called_once_with(789, 1)
        self.window.close.assert_called_once_with()
        self.assertFalse(hotkey.registered)


class FloatingWindowCompatibilityTests(unittest.TestCase):
    def test_style_retains_unrelated_flags_and_sets_activation_modes(self):
        for previous in (0, 0x10000000, shell.WS_EX_TRANSPARENT | shell.WS_EX_NOACTIVATE):
            for through in (False, True):
                for no_activate in (False, True):
                    with self.subTest(previous=previous, through=through, no_activate=no_activate):
                        actual = shell.floating_exstyle(previous, through, no_activate)
                        self.assertEqual(bool(actual & shell.WS_EX_NOACTIVATE), no_activate)
                        self.assertEqual(bool(actual & shell.WS_EX_TRANSPARENT), through)
                        self.assertTrue(actual & shell.WS_EX_TOOLWINDOW)
                        self.assertEqual(actual & 0x10000000, previous & 0x10000000)

    def test_mouse_activation_keeps_no_activate_and_destroy_removes_subclass(self):
        api = SimpleNamespace(comctl32=MagicMock())
        hwnd = 900
        shell._floating_windows.add(hwnd)
        self.addCleanup(shell._floating_windows.discard, hwnd)
        with patch.object(shell, "_get_api", return_value=api):
            self.assertEqual(shell._floating_subclass_impl(hwnd, shell.WM_MOUSEACTIVATE, 0, 0, 0, 0),
                             shell.MA_NOACTIVATE)
            api.comctl32.DefSubclassProc.assert_not_called()
            shell._floating_subclass_impl(hwnd, shell.WM_NCDESTROY, 0, 0, 0, 0)
        api.comctl32.RemoveWindowSubclass.assert_called_once_with(
            hwnd, shell._FLOATING_SUBCLASS, shell._FLOATING_SUBCLASS_ID)
        self.assertNotIn(hwnd, shell._floating_windows)

    def test_configure_preserves_click_through_and_no_activation_flags(self):
        user, comctl = MagicMock(), MagicMock()
        user.GetAncestor.return_value = 333
        user.SetLayeredWindowAttributes.return_value = True
        user.SetWindowPos.return_value = True
        comctl.SetWindowSubclass.return_value = True
        api = SimpleNamespace(user32=user, comctl32=comctl,
                              GetWindowLongPtrW=MagicMock(return_value=0),
                              SetWindowLongPtrW=MagicMock(return_value=1))
        window = MagicMock()
        window.winfo_id.return_value = 222
        self.addCleanup(shell._floating_windows.discard, 333)
        with patch.object(shell, "_get_api", return_value=api):
            self.assertEqual(shell.configure_floating_window(window), 333)
        flags = api.SetWindowLongPtrW.call_args.args[2]
        self.assertTrue(flags & shell.WS_EX_TRANSPARENT)
        self.assertTrue(flags & shell.WS_EX_NOACTIVATE)
        self.assertTrue(flags & shell.WS_EX_TOOLWINDOW)
        self.assertTrue(flags & shell.WS_EX_LAYERED)
        positioning_flags = user.SetWindowPos.call_args.args[-1]
        self.assertEqual(positioning_flags,
                         shell.SWP_NOMOVE | shell.SWP_NOSIZE | shell.SWP_NOACTIVATE | shell.SWP_FRAMECHANGED)
        self.assertIn(333, shell._floating_windows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
