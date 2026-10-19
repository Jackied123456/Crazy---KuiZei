#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local observer mirror. Camera requires a click; optional clips stay on this PC."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import queue
from pathlib import Path
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import small_mirror_core as core
from small_mirror_brand import PRODUCT_TITLE,PRODUCT_CN,VERSION_LABEL
from event_stats_v4 import EventSessionGate, StatsStore, StatsDataError
from small_mirror_shell import NativeTray, GlobalHotkey, configure_floating_window
from small_mirror_identity import OwnerStore, POSES
from small_mirror_effects import EFFECTS,deform,draw_bugs
from small_mirror_attention import facing_screen, FacingTimer
from small_mirror_recording import EventRecorder
from small_mirror_panels import WindowPicker,RecordingsPanel
from small_mirror_performer import (PerformerFrameRenderer, MOTIONS, PHRASES, DEFAULT_MOTIONS,
    DEFAULT_PHRASES, normalize_choices, forget_media_libraries)
from small_mirror_performer_panel import PerformerPanel
from small_mirror_media import media_dir, import_pack, clear_packs


DEMO_SECONDS = math.inf
HEAD_SIZES = {'适中 320×240': (320, 240), '明显 360×280': (360, 280), '大号 480×360': (480, 360)}
PERFORMER_SIZES = {'相框 560×340': (560, 340), '大相框 640×400': (640, 400)}
FRAME_STYLES = ('动态相框', '普通特写')


def performer_asset_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets' / 'yaoshui'


def local_now():
    return datetime.now().astimezone()


def default_data_dir():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Crazy' / 'PublicBeta'


class SingleInstance:
    """Keep two copies from independently overwriting the same event totals."""
    def __init__(self, data_dir):
        self.handle = None
        self.already_running = False
        if sys.platform == 'win32':
            self.api = ctypes.WinDLL('kernel32', use_last_error=True)
            self.api.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            self.api.CreateMutexW.restype = ctypes.c_void_p
            self.api.CloseHandle.argtypes = [ctypes.c_void_p]
            self.api.CloseHandle.restype = ctypes.c_int
            key = hashlib.sha256(str(Path(data_dir).resolve()).casefold().encode('utf-8')).hexdigest()[:20]
            ctypes.set_last_error(0)
            self.handle = self.api.CreateMutexW(None, False, 'Local\\Crazy-PublicBeta-' + key)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), '无法创建应用运行标记')
            self.already_running = ctypes.get_last_error() == 183

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def place_window(window, x, y, width, height):
    window.geometry(f'{int(width)}x{int(height)}{int(x):+d}{int(y):+d}')
    window.update_idletasks()
    if sys.platform == 'win32':
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_int
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), int(x), int(y), int(width), int(height), 0x0010)


def resized_rect(rect, edges, dx, dy, desktop, minimum=(140,100), maximum=(1920,1440)):
    x,y,w,h=rect;vx,vy,vw,vh=desktop
    minw,minh=min(minimum[0],vw),min(minimum[1],vh)
    maxw,maxh=min(maximum[0],vw),min(maximum[1],vh)
    if not edges:
        return (max(vx,min(vx+vw-w,x+dx)),max(vy,min(vy+vh-h,y+dy)),w,h)
    right,bottom=x+w,y+h
    if 'e' in edges:w=max(minw,min(maxw,vx+vw-x,w+dx))
    if 's' in edges:h=max(minh,min(maxh,vy+vh-y,h+dy))
    if 'w' in edges:
        w=max(minw,min(maxw,right-vx,w-dx));x=right-w
    if 'n' in edges:
        h=max(minh,min(maxh,bottom-vy,h-dy));y=bottom-h
    return tuple(round(value) for value in (x,y,w,h))


class PrivacyApp:
    BG = '#F4F5F2'
    PANEL = '#FFFFFF'
    TEXT = '#202722'
    MUTED = '#5F6D63'
    ACCENT = '#55715D'

    def __init__(self, root, data_dir=None, enable_shell=True):
        self.root = root
        try:
            self.ui_scale = max(.75, root.winfo_fpixels('1i') / 96.0)
        except (AttributeError, tk.TclError):
            self.ui_scale = 1.0
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.active_media_dir=media_dir(self.data_dir,performer_asset_dir())
        self.media_task=None
        self.media_poll=None
        self.config_path = self.data_dir / 'settings.json'
        self.stats_path = self.data_dir / 'statistics.json'
        self.config = self.load_config()
        self.owner_store = OwnerStore(self.data_dir/'owner-template.bin')
        self.owner_error = ''
        try:
            self.owner_samples = self.owner_store.load()
        except (OSError,ValueError) as exc:
            self.owner_samples = [];self.owner_error = str(exc)
        self.owner_window = None
        self.owner_preview=None;self.owner_preview_image=None;self.owner_preview_map=None
        self.owner_candidate=None;self.owner_verifying=False
        self.enrolling = False
        self.enroll_selected = False
        self.pending_owner_samples=None;self.owner_verified=False
        self.owner_verification_since=None;self.owner_verification_serial=-1
        self.owner_verification_hits=0;self.owner_verification_target=None
        self.owner_camera_was_paused=False
        self.stats_error = ''
        try:
            self.stats = StatsStore(self.stats_path)
        except (StatsDataError, OSError) as exc:
            self.stats = None
            self.stats_error = str(exc)
        self.worker = None
        self.rois = core.normalize_rois(self.config.get('rois', [(0,0,1,1)]))
        self.exclusions = core.normalize_rois(self.config.get('exclusions', []))
        self.paused = True
        self.closing = False
        self.last_serial = -1
        self.last_error = ''
        self.camera_fault = False
        self.observer_active = False
        self.observer_absent_since = None
        self.selection_mode = 'none'
        self.preferences_window = None
        self.index_input = None
        self.resolution_picker = None
        self.preview_image = None
        self.preview_map = None
        self.drag_start = None
        self.drag_end = None
        self.bar = None
        self.bar_canvas = None
        self.bar_image = None
        self.bar_shown = False
        self.bar_until = 0.0
        self.demo_bar = False
        self.performer_renderer = None
        self.performer_panel = None
        self.performer_preview_button = None
        self.performer_motions = self.config.get('performer_motions',list(DEFAULT_MOTIONS))
        self.performer_phrases = self.config.get('performer_phrases',list(DEFAULT_PHRASES))
        self.performer_started_at = None
        self.performer_target = None
        self.tick_after = None
        self.adjusting = False
        self.drag_origin = None
        self.bar_position = self.config.get('bar_position')
        self.custom_bar_size = self.config.get('bar_size')
        self.masks = []
        self.mask_active=False;self.auto_mask=False;self.protection_absent_since=None
        self.coverage_errors=[];self.coverage_expected=0;self.coverage_checked=0
        self.protected_windows=[];self.protected_masks={}
        self.window_api=None;self.window_picker=None;self.recordings_panel=None
        self.recorder=EventRecorder(self.data_dir/'recordings',core.cv2)
        self.record_serial=-1
        self.protect_regions = self.config.get('protect_regions', [])
        self.selector = None
        self.stats_window = None
        self.tray = None
        self.hotkey = None
        self.notices = []
        self.camera_index = tk.StringVar(value=str(self.config.get('camera_index', 0)))
        self.sensitivity = tk.StringVar(value=self.config.get('sensitivity', '高'))
        self.resolution = tk.StringVar(value=self.config.get('resolution', '1280×720'))
        self.frame_style = tk.StringVar(value=self.config.get('frame_style', FRAME_STYLES[0]))
        self.display_size = tk.StringVar(value=self.config.get('display_size', '相框 560×340'))
        self.debug = tk.BooleanVar(value=bool(self.config.get('debug', False)))
        self.effect=tk.StringVar(value=self.config.get('effect',EFFECTS[0]))
        self.bugs=tk.StringVar(value=self.config.get('bugs','关闭'))
        self.record_enabled=tk.BooleanVar(value=bool(self.config.get('record_enabled',False)))
        self.attention_required=tk.BooleanVar(value=bool(self.config.get('attention_required',True)))
        self.preview_enabled=tk.BooleanVar(value=False)
        self.owner_summary=tk.StringVar(value='本人：已录入' if self.owner_samples else '本人：还没录入')
        self.record_summary=tk.StringVar(value='录像：已开启' if self.record_enabled.get() else '录像：关闭')
        self.record_indicator=tk.StringVar(value='')
        self.protection_summary=tk.StringVar(value='待开启 · 先认识你，再选择保护窗口')
        self.attention_since=None;self.attention_target=None;self.attention_hits=0;self.attention_serial=-1
        self.facing_timer=FacingTimer()
        self.session_gate = self.new_session_gate()
        self.protection_gate=EventSessionGate(threshold=.3,min_hits=2)
        self.protection_serial=-1
        self.sensitive = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value='已暂停')
        self.detail_var = tk.StringVar(value='先认识你、选好窗口，再开始保护。动画预览在设置里。')
        self.region_var = tk.StringVar(value='保护区 0 个')
        self.detector_var = tk.StringVar(value='相机关闭 · 高灵敏度 · 完整头部特写')
        self.root.title(PRODUCT_TITLE+' · '+VERSION_LABEL)
        window_height = min(round(640*self.ui_scale), max(500, self.root.winfo_screenheight()-80))
        window_width = min(round(820*self.ui_scale), max(680, self.root.winfo_screenwidth()-60))
        start_x=max(0,(self.root.winfo_screenwidth()-window_width)//2)
        start_y=max(0,(self.root.winfo_screenheight()-window_height)//2)
        self.root.geometry(f'{window_width}x{window_height}+{start_x}+{start_y}')
        self.root.minsize(min(round(680*self.ui_scale), window_width), min(round(620*self.ui_scale), window_height))
        self.root.configure(bg=self.BG)
        self.root.protocol('WM_DELETE_WINDOW', lambda: self.hide_settings() if self.tray else self.close())
        self.root.bind('<Escape>', lambda _e: self.restore())
        self.build_ui()
        # Static local media decodes away from Tk's event loop. No camera or
        # observer pixels enter this loader; protection can respond meanwhile.
        self.performer_renderer=PerformerFrameRenderer(self.active_media_dir,
            self.performer_motions,self.performer_phrases,background=True)
        if self.owner_error:self.detail_var.set('本人模板暂不可用，请重新录入：'+self.owner_error)
        elif self.owner_samples:self.detail_var.set('已载入本机本人模板，开始后自动核验并排除本人。')
        self.update_region_label()
        if self.stats_error:
            self.detail_var.set('统计数据暂不可用，旧文件已保留；可在统计面板备份并重置。')
        if enable_shell and sys.platform == 'win32':
            try:
                self.tray = NativeTray(root, self.show_settings, self.toggle_pause,
                                       self.toggle_sensitive, self.open_stats,
                                       self.adjust_bar, self.close)
            except Exception as exc:
                self.notices.append('托盘暂不可用：' + str(exc))
            try:
                self.hotkey = GlobalHotkey(root, self.toggle_mask)
            except Exception as exc:
                self.notices.append('全局快捷键暂不可用：' + str(exc))
            self.update_tray()
        if self.notices:
            self.detail_var.set('；'.join(self.notices))
        self.tick_after = self.root.after(60, self.tick)

    def load_config(self):
        try:
            data = json.loads(self.config_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return {}
            data['rois'] = core.normalize_rois(data.get('rois', data.get('roi')))
            data['exclusions'] = core.normalize_rois(data.get('exclusions', []))
            regions = data.get('protect_regions')
            if regions is None:
                regions = [data['protect_region']] if data.get('protect_region') else []
            data['protect_regions'] = [r for r in regions if isinstance(r, (list, tuple)) and len(r) == 4
                                       and all(type(v) in (int, float) for v in r)
                                       and r[2] >= 60 and r[3] >= 40] if isinstance(regions, list) else []
            position = data.get('bar_position')
            if position is not None and (not isinstance(position, list) or len(position) != 2
                                          or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)):
                data.pop('bar_position', None)
            size = data.get('bar_size')
            if (not isinstance(size,list) or len(size)!=2
                    or any(type(v) not in (int,float) or not math.isfinite(v) for v in size)
                    or not 140<=size[0]<=960 or not 100<=size[1]<=720):
                data.pop('bar_size',None)
            if data.get('sensitivity') not in ('高', '标准'):
                data['sensitivity'] = '高'
            if data.get('resolution') not in ('1280×720', '1920×1080', '1280×960'):
                data['resolution'] = '1280×720'
            if data.get('frame_style') not in FRAME_STYLES:data['frame_style']=FRAME_STYLES[0]
            data['performer_motions']=normalize_choices(data.get('performer_motions'),[row[0] for row in MOTIONS],DEFAULT_MOTIONS)
            data['performer_phrases']=normalize_choices(data.get('performer_phrases'),[row[0] for row in PHRASES],DEFAULT_PHRASES)
            if data.get('display_size') not in {**HEAD_SIZES, **PERFORMER_SIZES}:
                data['display_size'] = '明显 360×280'
            if data['frame_style']==FRAME_STYLES[0] and data['display_size'] not in PERFORMER_SIZES:
                data['display_size']='相框 560×340'
            if data['frame_style']==FRAME_STYLES[0] and 'bar_size' in data and (data['bar_size'][0]<420 or data['bar_size'][1]<260):
                data.pop('bar_size')
            if data.get('effect') not in EFFECTS:data['effect']=EFFECTS[0]
            if data.get('bugs') not in ('关闭','特写里','保护遮罩上'):data['bugs']='关闭'
            data['record_enabled']=data.get('record_enabled') is True
            if type(data.get('attention_required')) is not bool:data['attention_required']=True
            if type(data.get('camera_index')) is not int or not 0 <= data['camera_index'] <= 10:
                data['camera_index'] = 0
            return data
        except (OSError, ValueError, TypeError):
            return {}

    def save_config(self):
        try:
            camera_index = int(self.camera_index.get())
            if not 0 <= camera_index <= 10:
                raise ValueError()
        except ValueError:
            camera_index = self.config.get('camera_index', 0)
        data = dict(version=10, rois=self.rois, exclusions=[], camera_index=camera_index,
                    sensitivity=self.sensitivity.get(), resolution=self.resolution.get(),
                    display_size=self.display_size.get(), bar_position=self.bar_position,
                    protect_regions=self.protect_regions, debug=self.debug.get(),bar_size=self.custom_bar_size,
                    effect=self.effect.get(),bugs=self.bugs.get(),record_enabled=self.record_enabled.get(),
                    attention_required=self.attention_required.get(),frame_style=self.frame_style.get(),
                    performer_motions=getattr(self,'performer_motions',list(DEFAULT_MOTIONS)),
                    performer_phrases=getattr(self,'performer_phrases',list(DEFAULT_PHRASES)))
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.config_path)
            return True
        except (OSError, ValueError) as exc:
            self.detail_var.set('设置未能保存：' + str(exc))
            return False

    def build_ui(self):
        from small_mirror_theme import configure_theme
        configure_theme(self)
        from small_mirror_home import build_home
        build_home(self)

    def toggle_preview(self):
        if self.preview_enabled.get():
            self.home_panel.pack_forget()
            self.home_cards.pack_forget()
            self.preview_toolbar.pack(fill='x',pady=(10,0),before=self.footer)
            self.preview.pack(fill='both',expand=True,pady=14,before=self.footer)
        else:
            self.finish_selection();self.preview.pack_forget();self.preview_toolbar.pack_forget()
            self.home_panel.pack(fill='x',pady=(14,6),after=self.home_anchor)
            self.home_cards.pack(fill='both',expand=True,pady=(0,12),before=self.footer)

    def open_preferences(self, page='显示'):
        if self.preferences_window is not None and self.preferences_window.winfo_exists():
            if page == '保护':
                self.preferences_tabs.select(2)
            elif page == '动画':self.preferences_tabs.select(3)
            self.preferences_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.preferences_window = window
        window.title(PRODUCT_TITLE+' · 设置')
        window.geometry(f'{round(680*self.ui_scale)}x{min(round(500*self.ui_scale),self.root.winfo_screenheight()-80)}')
        window.configure(bg=self.BG)
        window.protocol('WM_DELETE_WINDOW',self.close_preferences)
        outer = ttk.Frame(window, padding=16)
        outer.pack(fill='both', expand=True)
        tabs = ttk.Notebook(outer)
        self.preferences_tabs = tabs
        tabs.pack(fill='both', expand=True)
        display = ttk.Frame(tabs, padding=18)
        camera = ttk.Frame(tabs, padding=18)
        protection = ttk.Frame(tabs, padding=18)
        performer = ttk.Frame(tabs,padding=18)
        for frame, label in ((display,'显示'),(camera,'相机'),(protection,'保护'),(performer,'动画与台词')):
            tabs.add(frame, text=label)
        frame_row=ttk.Frame(display);frame_row.pack(fill='x',pady=(0,12))
        ttk.Label(frame_row,text='显示方式').pack(side='left',padx=(0,10))
        style_picker=ttk.Combobox(frame_row,textvariable=self.frame_style,values=FRAME_STYLES,state='readonly',width=16)
        style_picker.pack(side='left',padx=(0,10));style_picker.bind('<<ComboboxSelected>>',lambda _e:self.frame_style_changed())
        size_row = ttk.Frame(display);size_row.pack(fill='x', pady=(0, 14))
        ttk.Label(size_row, text='画面大小').pack(side='left', padx=(0, 10))
        picker = ttk.Combobox(size_row, textvariable=self.display_size, state='readonly',
                              values=tuple(PERFORMER_SIZES if self.using_performer() else HEAD_SIZES), width=16)
        self.size_picker=picker
        picker.pack(side='left', padx=(0, 10))
        picker.bind('<<ComboboxSelected>>', lambda _e:self.change_crop())
        ttk.Button(size_row, text='调整窗口', command=self.adjust_bar).pack(side='left')
        effects_row=ttk.Frame(display);effects_row.pack(fill='x',pady=(0,12))
        ttk.Label(effects_row,text='回敬效果').pack(side='left',padx=(0,10))
        effects_picker=ttk.Combobox(effects_row,textvariable=self.effect,values=EFFECTS,state='readonly',width=14)
        effects_picker.pack(side='left',padx=(0,14));effects_picker.bind('<<ComboboxSelected>>',lambda _e:self.save_config())
        ttk.Label(effects_row,text='小虫子').pack(side='left',padx=(0,8))
        bugs_picker=ttk.Combobox(effects_row,textvariable=self.bugs,values=('关闭','特写里','保护遮罩上'),state='readonly',width=12)
        bugs_picker.pack(side='left');bugs_picker.bind('<<ComboboxSelected>>',lambda _e:self.save_config())
        ttk.Checkbutton(display,text='面朝屏幕约 1 秒后才回敬、录像（近似判断）',variable=self.attention_required,
                        command=self.update_settings).pack(anchor='w',pady=(0,12))
        ttk.Checkbutton(display, text='显示识别调试信息', variable=self.debug,
                        command=self.save_config).pack(anchor='w', pady=(0, 14))
        ttk.Button(display, text='统计与成就', command=self.open_stats).pack(anchor='w')
        ttk.Label(display, text='检测到观察者时持续显示；离开画面后收起。',
                  foreground=self.MUTED).pack(anchor='w', pady=(18,0))
        ttk.Label(display,text='调整时可拖动边缘缩放，双击完成。',foreground=self.MUTED).pack(anchor='w',pady=(8,0))
        row = ttk.Frame(camera);row.pack(fill='x', pady=(0, 14))
        ttk.Label(row, text='相机').pack(side='left', padx=(0, 10))
        self.index_input = ttk.Spinbox(row, from_=0, to=10, width=4, textvariable=self.camera_index)
        self.index_input.pack(side='left', padx=(0, 18))
        ttk.Label(row, text='分辨率').pack(side='left', padx=(0, 10))
        self.resolution_picker = ttk.Combobox(row, textvariable=self.resolution, state='readonly',
                    values=('1280×720','1920×1080','1280×960'), width=13)
        self.resolution_picker.pack(side='left')
        self.resolution_picker.bind('<<ComboboxSelected>>', lambda _e:self.save_config())
        row2 = ttk.Frame(camera);row2.pack(fill='x', pady=(0, 14))
        ttk.Label(row2, text='灵敏度').pack(side='left', padx=(0, 10))
        sensitivity_picker = ttk.Combobox(row2,textvariable=self.sensitivity,state='readonly',values=('高','标准'),width=8)
        sensitivity_picker.pack(side='left')
        sensitivity_picker.bind('<<ComboboxSelected>>', lambda _e:self.update_settings())
        ttk.Label(camera,text='暂停后可更换相机或分辨率。',foreground=self.MUTED).pack(anchor='w')
        ttk.Label(camera,textvariable=self.detector_var,foreground=self.MUTED,
                  wraplength=530*self.ui_scale).pack(anchor='w',pady=(14,0))
        ttk.Checkbutton(protection,text='敏感内容自动遮挡',variable=self.sensitive,
                        command=self.sensitive_changed).pack(anchor='w',pady=(0,14))
        row3=ttk.Frame(protection);row3.pack(fill='x',pady=(0,14))
        ttk.Button(protection,text='选择要保护的窗口',command=self.open_window_picker).pack(anchor='w',pady=(0,12))
        for label,callback in (('添加固定区域',self.select_protect_region),('撤销',self.undo_protect_region),
                               ('清空',self.clear_protect_regions),('遮挡／恢复',self.toggle_mask)):
            ttk.Button(row3,text=label,command=callback).pack(side='left',padx=(0,8))
        ttk.Label(protection,textvariable=self.region_var,foreground=self.MUTED).pack(anchor='w')
        ttk.Label(protection,text='Ctrl+Alt+H 同时遮挡／恢复全部保护区。',foreground=self.MUTED).pack(anchor='w',pady=(14,0))
        ttk.Button(protection,text='试遮挡／恢复',command=self.test_protection).pack(anchor='w',pady=(14,0))
        ttk.Label(protection,textvariable=self.protection_summary,foreground=self.MUTED,wraplength=520*self.ui_scale).pack(anchor='w',pady=(12,0))
        ttk.Label(performer,text='动画与台词',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(performer,text='看看触发后的相框。预览不会遮挡窗口、计次或录像。',foreground=self.MUTED,wraplength=520*self.ui_scale).pack(anchor='w',pady=(8,10))
        self.performer_preview_button=ttk.Button(performer,text='预览动画',command=self.preview_performer)
        self.performer_preview_button.pack(anchor='w',pady=(0,12))
        ttk.Button(performer,text='选择动作与台词',command=self.open_performer_panel).pack(anchor='w')
        row=ttk.Frame(performer);row.pack(anchor='w',pady=(10,0))
        ttk.Button(row,text='导入本地动图',command=self.import_local_media).pack(side='left',padx=(0,8))
        ttk.Button(row,text='删除导入素材',command=self.remove_local_media).pack(side='left')
        ttk.Label(performer,text='默认使用原创哨兵。可导入有权使用的 GIF；命名见 README。\n6 组节目、3 种台词风格，默认静音。',foreground=self.MUTED,wraplength=520*self.ui_scale).pack(anchor='w',pady=(10,0))
        self.set_camera_controls(not self.paused)
        if page == '保护':tabs.select(protection)
        elif page == '动画':tabs.select(performer)

    def close_preferences(self):
        if self.demo_bar:self.hide_bar(clear_deadline=True)
        self.performer_preview_button=None
        if self.preferences_window:self.preferences_window.destroy()
        self.preferences_window=None

    def import_local_media(self):
        if self.media_task is not None:
            self.detail_var.set('动图正在准备，稍等一下。');return
        folder=filedialog.askdirectory(parent=self.preferences_window or self.root,title='选择按素材说明命名的 GIF 文件夹')
        if not folder:return
        if not messagebox.askyesno('导入本地素材','请确认这些动图是你有权使用的。素材只保存在本机，不会进入公开包。\n缺少的动作会显示原创哨兵。继续导入？',parent=self.preferences_window or self.root):return
        self.media_task=queue.SimpleQueue()
        results=self.media_task;profile=self.data_dir;builtin=performer_asset_dir()
        def work():
            try:results.put((True,import_pack(folder,profile,builtin)))
            except Exception as exc:results.put((False,str(exc)))
        # All file decoding stays away from Tk. Worker never receives camera data.
        threading.Thread(target=work,name='LocalMediaImport',daemon=True).start()
        self.detail_var.set('正在检查本地动图，你可以继续操作。')
        self.media_poll=self.root.after(80,self.poll_media_import)

    def poll_media_import(self):
        self.media_poll=None
        if self.closing:return
        try:okay,result=self.media_task.get_nowait()
        except queue.Empty:
            self.media_poll=self.root.after(80,self.poll_media_import);return
        self.media_task=None
        if okay:
            self.reload_local_media()
            self.detail_var.set(f'已导入 {result} 个本地动图。到设置里预览看看。')
        else:self.detail_var.set('动图没导入：'+str(result))

    def reload_local_media(self):
        if self.performer_panel:self.performer_panel.close()
        self.hide_bar(clear_deadline=True)
        self.active_media_dir=media_dir(self.data_dir,performer_asset_dir())
        self.performer_renderer=PerformerFrameRenderer(self.active_media_dir,self.performer_motions,self.performer_phrases,background=True)

    def remove_local_media(self):
        if self.media_task is not None:
            self.detail_var.set('请等动图导入完成后再删除。');return
        if not messagebox.askyesno('删除导入素材','删除本应用导入的动图并恢复原创哨兵？本人模板、设置和录像不受影响。',parent=self.preferences_window or self.root):return
        try:
            clear_packs(self.data_dir,disable_portable=True);forget_media_libraries(self.data_dir/'media-packs');self.reload_local_media()
            self.detail_var.set('导入素材已删除，已恢复原创哨兵。')
        except (OSError,ValueError) as exc:self.detail_var.set('素材未能删除：'+str(exc))

    def set_camera_controls(self, running):
        for widget, idle in ((self.index_input,'normal'),(self.resolution_picker,'readonly')):
            if widget is not None and widget.winfo_exists():
                widget.configure(state='disabled' if running else idle)

    def clean_observer_crop(self,packet):
        if not self.valid_observer(packet):return None
        fh,fw=packet.frame.shape[:2]
        index=core.matching_region(packet.face,fw,fh,self.rois)
        if index is None:return None
        bounds=core.observer_crop(packet.face,fw,fh,self.rois[index],eye_mode=False)
        return core.safe_observer_crop(packet.frame,packet.face,bounds,
                                      packet.private_boxes or packet.owner_boxes) if bounds else None

    def recording_changed(self):
        self.recorder.finish()
        if self.record_enabled.get() and self.recorder.error:
            self.recorder.close();self.recorder=EventRecorder(self.data_dir/'recordings',core.cv2)
        self.save_config()

    def process_recording(self,packet,now,show_observer):
        if not self.record_enabled.get() or self.paused or self.enrolling or self.owner_verifying:
            if self.recorder.target is not None:self.recorder.finish()
        else:
            crop=self.clean_observer_crop(packet) if show_observer else None
            item=next((item for item in packet.faces if item['bbox']==packet.face),{})
            self.recorder.feed(crop,now,packet.serial,item.get('track_id','observer'))
        if self.recorder.error:
            self.record_summary.set('录像：保存遇到问题')
            if self.recorder.error!=getattr(self,'last_record_error',''):
                self.detail_var.set(self.recorder.error);self.last_record_error=self.recorder.error
        elif self.recorder.recording:self.record_summary.set('● 正在录像 · 原始特写')
        else:self.record_summary.set('录像：已开启，等待目标' if self.record_enabled.get() else '录像：关闭')
        self.record_indicator.set('● 录像中' if self.recorder.recording else '')

    def open_recordings(self):
        if self.recordings_panel is not None:
            self.recordings_panel.window.lift();return
        self.recordings_panel=RecordingsPanel(self)

    def process_protection(self,packet,now):
        # Do not consume the one-per-visit trigger before protection is enabled.
        # A user may select a window while the same observer is still present.
        if not self.sensitive.get() or not self.has_protection():
            self.protection_gate.reset();self.protection_serial=-1
            return
        # No fresh image is not evidence that an observer left. Keep an existing
        # automatic cover until valid camera frames establish an actual absence.
        trustworthy=(packet.frame is not None and packet.ready and packet.identity_ready and not packet.error
                     and not self.paused and not self.enrolling and not self.owner_verifying and bool(self.owner_samples))
        if not trustworthy:
            self.protection_absent_since=None
            return
        present=self.valid_observer(packet)
        height,width=packet.frame.shape[:2]
        # An uncertain identity is not absence. Cover content while verification
        # catches up, but never use that face for a close-up or a recording.
        present=present or any(item.get('identity') not in ('owner','unregistered')
            and item.get('detection_accepted',item.get('protection_candidate',False))
            and core.matching_region(item['bbox'],width,height,self.rois) is not None
            for item in packet.faces)
        if present:self.protection_absent_since=None
        elif self.protection_absent_since is None:self.protection_absent_since=now
        if self.auto_mask and self.protection_absent_since is not None and now-self.protection_absent_since>=1.2:
            self.restore()
            # Auto-restoring content must also rearm protection for a quick return.
            self.protection_gate.reset();self.protection_serial=-1
        if packet.serial==self.protection_serial and packet.frame is not None:return
        self.protection_serial=packet.serial
        if self.protection_gate.observe(now,present):
            if self.sensitive.get() and self.has_protection():self.show_mask(automatic=True)

    def open_window_picker(self):
        if self.window_picker is not None:self.window_picker.window.lift();return
        try:
            if self.window_api is None:
                from small_mirror_windows import WindowAPI
                self.window_api=WindowAPI()
            self.window_picker=WindowPicker(self,self.window_api)
        except Exception as exc:
            self.detail_var.set('窗口列表暂时没打开：'+str(exc))

    def set_protected_windows(self,tokens):
        was_active=self.mask_active;was_automatic=self.auto_mask
        self.restore();self.protected_windows=list(tokens)
        self.protection_gate.reset();self.protection_serial=-1
        self.sensitive.set(self.has_protection());self.update_region_label();self.save_config();self.update_tray()
        self.detail_var.set('选好了。窗口移动或缩放时，保护范围会跟着走。' if tokens else '已取消窗口保护。')
        if was_active and self.has_protection():self.show_mask(automatic=was_automatic)
        self.update_protection_summary()

    def has_protection(self):
        return self.valid_protect_region() or bool(self.protected_windows)

    def make_mask(self,region,target=None):
        mask=tk.Toplevel(self.root);mask.withdraw();mask.overrideredirect(True);mask.configure(bg=self.BG)
        canvas=tk.Canvas(mask,bg=self.BG,highlightthickness=0);canvas.pack(fill='both',expand=True)
        canvas.bind('<Double-Button-1>',lambda _e:self.restore())
        mask._canvas=canvas;mask._bounds=tuple(region);mask._target=target
        x,y,w,h=region
        canvas.create_text(w/2,h/2,text='内容已遮挡\n双击这里，或按 Ctrl+Alt+H 恢复',fill=self.MUTED,
                           font=('Microsoft YaHei UI',10),width=max(40,w-20),justify='center',tags='message')
        place_window(mask,x,y,w,h);configure_floating_window(mask,click_through=False,no_activate=True)
        if target:self.window_api.cover(mask,target,region)
        mask.deiconify();configure_floating_window(mask,click_through=False,no_activate=True)
        if target:self.window_api.cover(mask,target,region)
        self.masks.append(mask)
        return mask

    def protection_confirmed(self):
        return bool(self.mask_active and not getattr(self,'coverage_errors',[])
                    and getattr(self,'coverage_expected',0)>0
                    and self.coverage_checked==self.coverage_expected)

    def update_protection_summary(self):
        if not hasattr(self,'protection_summary'):return
        errors=getattr(self,'coverage_errors',[])
        if self.mask_active and errors:
            text='遮挡异常 · '+errors[0]
        elif self.protection_confirmed():
            text='已遮挡 · '+('监测中断，保持遮挡' if getattr(self,'camera_fault',False) or self.paused and self.auto_mask else '双击遮罩或按 Ctrl+Alt+H 恢复')
        elif self.mask_active:text='等待保护窗口显示 · 尚未确认遮挡'
        elif not self.owner_samples and not self.mask_active:text='待设置 · 先认识你、选好窗口'
        elif not self.has_protection():text='未选保护窗口 · 先选择并试遮挡'
        elif self.paused:text='待开启 · 已选窗口，先试遮挡'
        elif not self.owner_samples:text='待录入本人 · 自动遮挡尚未开启'
        elif not self.sensitive.get():text='自动遮挡关闭 · 可手动试遮挡'
        elif not self.rois:text='未设置检测区 · 自动遮挡尚未开启'
        else:text='监测中 · 等待后方目标'
        self.protection_summary.set(text)
        if getattr(self,'test_mask_button',None):self.test_mask_button.configure(text='恢复内容' if self.mask_active else '试遮挡')

    def test_protection(self):
        if self.mask_active:
            self.restore();self.detail_var.set('内容已恢复。自动遮挡按当前开关继续等待检测。')
        elif not self.has_protection():
            self.open_window_picker();self.detail_var.set('先选择一个窗口，再点“试遮挡”。这一步不需要相机。')
        else:
            self.show_mask(automatic=False)
            self.detail_var.set('请确认选定窗口的内容已完全遮住。双击遮罩或按 Ctrl+Alt+H 恢复。' if self.protection_confirmed() else '尚未确认遮挡成功，请查看保护状态。')
        self.update_protection_summary()

    def sync_window_masks(self,now):
        self.coverage_errors=[];self.coverage_expected=0;self.coverage_checked=0
        for mask in self.masks:
            if getattr(mask,'_target',None) is None and hasattr(mask,'_bounds'):
                self.coverage_expected+=1
                if mask.winfo_viewable():self.coverage_checked+=1
        if self.window_api is not None:
            valid=[]
            for token in self.protected_windows:
                key=(token.hwnd,token.pid,token.epoch)
                mask=self.protected_masks.get(key)
                if not self.window_api.valid(token):
                    if mask:mask.destroy();self.masks.remove(mask);self.protected_masks.pop(key,None)
                    continue
                valid.append(token)
                if not self.mask_active:continue
                bounds=self.window_api.bounds(token)
                if bounds is None:
                    if self.window_api.target_visible(token):
                        self.coverage_expected+=1;self.coverage_errors.append('无法获取保护窗口的位置')
                    elif mask and mask.state()!='withdrawn':mask.withdraw()
                    continue
                self.coverage_expected+=1
                try:
                    if mask is None:
                        mask=self.make_mask(bounds,token);self.protected_masks[key]=mask
                    elif mask.state()=='withdrawn':
                        mask.deiconify()
                        # Tk remaps a withdrawn native wrapper. Reapply its
                        # overlay styles once, then cover() demotes and orders
                        # it relative to the source before the next paint.
                        configure_floating_window(mask,click_through=False,no_activate=True)
                    okay=self.window_api.cover(mask,token,bounds)
                    if okay:okay=self.window_api.verify_cover(mask,token,bounds)
                    repair_state=(bounds,getattr(mask,'_cover_predecessor',None))
                    if not okay and getattr(mask,'_repair_failed_state',None)!=repair_state:
                        # A target may activate while Tk is remapping a wrapper.
                        # Repair an observed failure once; never churn topmost
                        # styles on every frame when protection cannot recover.
                        configure_floating_window(mask,click_through=False,no_activate=True)
                        okay=self.window_api.cover(mask,token,bounds)
                        if okay:okay=self.window_api.verify_cover(mask,token,bounds)
                        mask._repair_failed_state=None if okay else repair_state
                    if okay:self.coverage_checked+=1
                    else:self.coverage_errors.append('未能覆盖所选窗口，请试遮挡确认')
                    x,y,w,h=bounds;mask._bounds=bounds
                    mask._canvas.coords('message',w/2,h/2)
                    mask._canvas.itemconfigure('message',width=max(40,w-20))
                except (OSError,tk.TclError) as exc:
                    self.coverage_errors.append('所选窗口遮挡失败')
                    self.detail_var.set('窗口遮挡没能完成：'+str(exc))
            if len(valid)!=len(self.protected_windows):
                self.protected_windows=valid;self.update_region_label()
        for mask in self.masks:
            if hasattr(mask,'_canvas'):
                if self.bugs.get()=='保护遮罩上':
                    draw_bugs(mask._canvas,mask._bounds[2],mask._bounds[3],now,self.ui_scale)
                else:mask._canvas.delete('bugs')
        self.update_protection_summary()

    def open_owner_settings(self):
        if self.owner_window is not None and self.owner_window.winfo_exists():
            self.owner_window.lift();return
        from small_mirror_onboarding import build_owner_window
        build_owner_window(self)

    def begin_enrollment(self):
        if self.worker and self.worker.stop_event.is_set():
            self.owner_status.set('相机正在关闭，等一下再试。');return
        if self.paused:self.toggle_pause()
        if self.paused:return
        if getattr(self,'pending_owner_samples',None) is not None:
            self.worker.identity_command('templates',self.owner_samples)
        self.pending_owner_samples=None;self.owner_verified=False
        self.owner_verification_since=None;self.owner_verification_hits=0
        self.enrolling=True;self.enroll_selected=False;self.owner_candidate=None;self.owner_verifying=False
        self.worker.identity_command('cancel')
        self.finish_selection();self.reset_observer();self.hide_bar(clear_deadline=True)
        self.enroll_button.configure(text='等相机画面出来…',state='disabled')
        self.delete_owner_button.configure(state='disabled');self.retry_owner_button.configure(state='disabled')
        self.owner_status.set('看这里。画面出来后，确认框里是你。')
        from small_mirror_onboarding import set_step
        set_step(self,0,'只有你一人时直接确认；画面里有多人时，点一下你自己的脸。')
        self.detail_var.set('正在认识你，先暂停回敬和录像。')

    def owner_point(self,event):
        if not self.enrolling or self.enroll_selected or not self.owner_preview_map:return
        ox,oy,dw,dh=self.owner_preview_map
        if not ox<=event.x<=ox+dw or not oy<=event.y<=oy+dh:return
        packet=self.current_packet()
        if packet.frame is None:return
        fh,fw=packet.frame.shape[:2];x=(1-(event.x-ox)/dw)*fw;y=(event.y-oy)/dh*fh
        matches=[item for item in packet.faces if item['bbox'][0]<=x<=item['bbox'][0]+item['bbox'][2] and item['bbox'][1]<=y<=item['bbox'][1]+item['bbox'][3]]
        if matches:self.owner_candidate=min(matches,key=lambda item:item['bbox'][2]*item['bbox'][3])['track_id']

    def confirm_owner(self):
        packet=self.current_packet()
        if self.owner_candidate is None or not any(item['track_id']==self.owner_candidate for item in packet.faces):
            self.owner_status.set('还没选中你，点一下画面里自己的脸。');return
        self.worker.identity_command('begin',{'track_id':self.owner_candidate,'auto':True})
        self.enroll_selected=True;self.enroll_button.configure(text='跟着提示动一动',state='disabled')
        self.retry_owner_button.configure(state='normal')
        from small_mirror_onboarding import set_step
        set_step(self,1,'跟着圆环旁的箭头慢慢转，不用转很大；看清后会自动进入下一步。')

    def retry_enrollment(self):
        if self.worker:self.worker.identity_command('retry')

    def render_owner_preview(self,packet):
        canvas=self.owner_preview
        if canvas is None or not canvas.winfo_exists():return
        canvas.delete('all');width,height=max(1,canvas.winfo_width()),max(1,canvas.winfo_height())
        if packet.frame is None:
            self.owner_preview_map=None
            message=packet.error or ('正在打开摄像头…' if self.enrolling else '点“开始录入”，把整张脸露出来。')
            canvas.create_text(width/2,height/2,text=message,fill=self.MUTED,width=max(100,width-30),font=('Microsoft YaHei UI',11));return
        fh,fw=packet.frame.shape[:2];dw,dh=core.fit_size(fw,fh,width,height);ox,oy=(width-dw)/2,(height-dh)/2
        self.owner_preview_map=ox,oy,dw,dh
        picture=core.Image.fromarray(core.cv2.cvtColor(packet.frame,core.cv2.COLOR_BGR2RGB))
        picture=picture.transpose(core.Image.Transpose.FLIP_LEFT_RIGHT).resize((dw,dh),core.Image.Resampling.BILINEAR)
        self.owner_preview_image=core.ImageTk.PhotoImage(picture);canvas.create_image(ox,oy,image=self.owner_preview_image,anchor='nw')
        if self.enrolling and not self.enroll_selected:
            ids=[item['track_id'] for item in packet.faces]
            if self.owner_candidate not in ids:self.owner_candidate=ids[0] if len(ids)==1 else None
        for item in packet.faces:
            selected=item['track_id']==self.owner_candidate or self.owner_verifying and item.get('identity')=='owner'
            x,y,w,h=item['bbox'];left=ox+(fw-x-w)*dw/fw;top=oy+y*dh/fh
            if not self.enroll_selected and not self.owner_verifying:
                canvas.create_rectangle(left,top,left+w*dw/fw,top+h*dh/fh,outline=self.ACCENT if selected else '#8796a2',width=3 if selected else 1)
        from small_mirror_onboarding import draw_guide
        draw_guide(self,packet,self.owner_preview_map)

    def update_enrollment(self,packet):
        if not self.owner_window or not self.owner_window.winfo_exists():return
        self.render_owner_preview(packet)
        if self.owner_verifying:
            owners=[item for item in packet.faces if item.get('identity')=='owner']
            now=time.monotonic()
            if packet.error or not packet.ready or len(owners)!=1:
                self.owner_verification_since=None;self.owner_verification_hits=0;self.owner_verified=False
                self.owner_status.set(packet.error or '看一下镜头，让我确认确实认得你')
                self.enroll_button.configure(text='等待验证',state='disabled')
                return
            target=owners[0].get('track_id')
            if self.owner_verification_since is None or target!=self.owner_verification_target:
                self.owner_verification_since=now;self.owner_verification_hits=0
                self.owner_verification_target=target;self.owner_verification_serial=-1
            if packet.serial!=self.owner_verification_serial:
                self.owner_verification_hits+=1;self.owner_verification_serial=packet.serial
            self.owner_verified=now-self.owner_verification_since>=.6 and self.owner_verification_hits>=3
            self.owner_status.set('认出你了，可以保存了' if self.owner_verified else '正在核对，保持一下…')
            self.enroll_button.configure(text='保存并完成' if self.owner_verified else '正在验证…',
                state='normal' if self.owner_verified else 'disabled',command=self.save_verified_owner)
            return
        if not self.enrolling:return
        if packet.error:self.owner_status.set(packet.error);return
        if not self.enroll_selected:
            ready=self.owner_candidate is not None
            self.owner_status.set('框里是你吗？是的话，点下面“是我，开始”。' if ready else '画面里有几个人，点一下你自己的脸。' if packet.faces else '靠近一点，让整张脸出现在画面里。')
            self.enroll_button.configure(text='是我，开始',state='normal' if ready else 'disabled',command=self.confirm_owner);return
        status=packet.enrollment_status
        if not status:return
        self.owner_progress.configure(value=status['count'])
        self.owner_status.set('四个角度都拍好了，接着验证一下' if status['done'] else f'第 {status["stage"]+1}/4 步 · {status["message"]}')
        self.enroll_button.configure(text='下一步：验证' if status['done'] else '正在采集…',state='normal' if status['done'] else 'disabled',command=self.finish_enrollment)
        lost='没跟上' in status['message'] or '不像' in status['message']
        self.retry_owner_button.configure(text='重新选我' if lost else '这一步重来',command=self.begin_enrollment if lost else self.retry_enrollment)

    def capture_enrollment(self):
        if self.worker:self.worker.identity_command('capture')

    def finish_enrollment(self):
        packet=self.current_packet()
        if not packet.enrollment_status.get('done') or len(packet.enrollment_samples)!=12:
            self.owner_status.set('还没拍完，或相机断开了。重新选一下自己再来。');return
        samples=[sample.copy() for sample in packet.enrollment_samples]
        self.pending_owner_samples=samples
        self.worker.identity_command('templates',samples)
        self.enrolling=False;self.enroll_selected=False;self.owner_verifying=True;self.owner_verified=False
        self.owner_verification_since=None;self.owner_verification_hits=0
        self.owner_verification_serial=-1;self.owner_verification_target=None
        self.reset_observer();self.hide_bar(clear_deadline=True)
        self.owner_status.set('最后确认一下：正对镜头，轻轻动动头')
        self.detail_var.set('正在验证新模板；旧模板尚未替换。')
        self.enroll_button.configure(text='正在验证…',state='disabled')
        self.retry_owner_button.configure(text='重新录入',state='normal',command=self.begin_enrollment)
        self.delete_owner_button.configure(state='disabled')
        from small_mirror_onboarding import set_step
        set_step(self,2,'认出你后点“保存并完成”。取消或验证失败时，原来的模板仍保留。')

    def save_verified_owner(self):
        samples=getattr(self,'pending_owner_samples',None)
        if not self.owner_verifying or not getattr(self,'owner_verified',False) or not samples:
            self.owner_status.set('还没确认是你，先看一下镜头。');return
        try:
            if self.remember_owner.get():self.owner_store.save(samples)
            else:self.owner_store.delete()
        except (OSError,ValueError) as exc:
            self.owner_status.set('没保存好，旧模板还在：'+str(exc));return
        self.owner_samples=samples;self.pending_owner_samples=None;self.owner_error=''
        self.owner_summary.set('本人：已录入')
        self.detail_var.set('已经认识你了。选择保护窗口，点“试遮挡”，再开始保护。')
        self.cancel_enrollment()

    def cancel_enrollment(self):
        if self.worker and getattr(self,'pending_owner_samples',None) is not None:
            self.worker.identity_command('templates',self.owner_samples)
        self.pending_owner_samples=None;self.owner_verified=False
        if self.worker:self.worker.identity_command('cancel')
        self.enrolling=False;self.enroll_selected=False;self.owner_verifying=False;self.reset_observer()
        if self.owner_window:self.owner_window.destroy();self.owner_window=None
        self.owner_preview=None;self.owner_preview_image=None
        if getattr(self,'owner_camera_was_paused',False) and not self.paused:
            self.toggle_pause(keep_cover=True)

    def delete_owner_template(self):
        if not messagebox.askyesno('删除本人模板','删除本机模板？重新录入前会暂停自动监测。',parent=self.owner_window):return
        try:self.owner_store.delete()
        except OSError as exc:self.owner_status.set('没能删除：'+str(exc));return
        self.owner_samples=[];self.owner_verifying=False
        if self.worker:self.worker.identity_command('templates',[])
        self.reset_observer();self.hide_bar(clear_deadline=True);self.restore()
        self.owner_status.set('已经删掉了。需要时点“开始录入”重新认识你。')
        self.enroll_button.configure(text='开始录入',state='normal',command=self.begin_enrollment)

    def choose_selection(self, mode):
        self.preview_enabled.set(True);self.toggle_preview()
        self.selection_mode = mode
        self.edit_hint.set('拖拽新增检测区' if mode == 'detect' else '拖拽框住本人活动范围')
        self.edit_bar.pack(fill='x',pady=(10,0),before=self.preview)
        self.detail_var.set('拖一个框，把想监测的后方范围圈起来。可以画多个。')

    def finish_selection(self):
        self.selection_mode = 'none'
        self.drag_start = self.drag_end = None
        self.edit_bar.pack_forget()
        self.detail_var.set(f'已设置 {len(self.rois)} 个检测区。本人会自动排除。')

    def undo_selected(self):
        if self.selection_mode == 'exclude':
            if self.exclusions:self.exclusions.pop()
            self.update_settings()
        else:self.undo_roi()

    def clear_selected(self):
        if self.selection_mode == 'exclude':
            self.exclusions = []
            self.update_settings()
        else:self.clear_roi()

    def show_settings(self):
        self.root.deiconify()
        self.root.lift()

    def new_session_gate(self):
        if self.attention_required.get():return EventSessionGate(threshold=0.,acquire_dropout_seconds=.25,min_hits=1)
        if self.sensitivity.get() == '高':
            return EventSessionGate(threshold=.5, acquire_dropout_seconds=.4, min_hits=3)
        return EventSessionGate(threshold=.8, acquire_dropout_seconds=.25, min_hits=3)

    def hide_settings(self):
        self.save_config()
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def update_tray(self):
        if self.tray:
            self.tray.update(self.paused, self.sensitive.get())

    def toggle_pause(self,keep_cover=False):
        if self.worker and self.worker.is_alive() and not self.worker.stop_event.is_set():
            self.paused = True
            if self.enrolling or self.owner_verifying:self.cancel_enrollment()
            self.worker.stop()
            self.reset_observer()
            self.hide_bar(clear_deadline=True)
            if not keep_cover:self.restore()
            self.camera_button.configure(text='正在关闭相机…', state='disabled')
            self.detail_var.set('正在关闭摄像头；关闭后可继续监测。')
            self.update_tray()
            return
        if self.worker and self.worker.is_alive():
            return
        try:
            index = int(self.camera_index.get())
            if not 0 <= index <= 10:
                raise ValueError()
        except ValueError:
            self.detail_var.set('请输入 0 到 10 的相机编号。')
            return
        self.paused = False
        self.last_error = ''
        self.last_serial = -1
        self.reset_observer()
        self.worker = core.CameraWorker(index,self.owner_samples)
        self.worker.configure(self.rois, True, self.sensitivity.get(), self.resolution.get(), exclusions=self.exclusions)
        self.worker.start()
        self.camera_button.configure(text='暂停', state='normal')
        self.set_camera_controls(True)
        self.detail_var.set('正在开启相机；本人核验后自动排除。' if self.owner_samples else '相机开启后，请点“认识我”录入本人。')
        self.save_config()
        self.update_tray()

    def update_settings(self):
        self.session_gate = self.new_session_gate()
        self.reset_observer()
        self.hide_bar(clear_deadline=True)
        if self.worker:
            self.worker.configure(self.rois, True, self.sensitivity.get(), self.resolution.get(), exclusions=self.exclusions)
        self.save_config()

    def reset_observer(self):
        self.session_gate.reset()
        self.observer_active = False
        self.observer_absent_since = None
        self.last_serial = -1
        self.attention_since=None;self.attention_target=None;self.attention_hits=0;self.attention_serial=-1
        if hasattr(self,'facing_timer'):self.facing_timer.reset()
        self.protection_absent_since=None
        if hasattr(self,'protection_gate'):self.protection_gate.reset();self.protection_serial=-1
        if hasattr(self,'recorder'):self.recorder.finish()

    def valid_observer(self, packet):
        if (packet.frame is None or packet.face is None or self.paused or self.enrolling or self.owner_verifying
                or not self.owner_samples or not packet.identity_ready):
            return False
        height, width = packet.frame.shape[:2]
        return (core.matching_region(packet.face, width, height, self.rois) is not None
                and not any(tuple(packet.face)==tuple(box) for box in packet.owner_boxes))

    def observe_packet(self, packet, now):
        """One count per visit; show only fresh, currently valid observer frames."""
        if self.paused or not self.rois or self.enrolling or self.owner_verifying or not self.owner_samples:
            self.reset_observer()
            return False
        valid = self.valid_observer(packet)
        if self.attention_required.get():
            if not hasattr(self,'facing_timer'):self.facing_timer=FacingTimer()
            candidate=next((item for item in packet.faces if item['bbox']==packet.face),None) if valid else None
            valid=self.facing_timer.update(now,packet.serial,candidate) and valid
        if valid:
            if (self.observer_absent_since is not None
                    and now-self.observer_absent_since >= self.session_gate.absence_seconds):
                self.observer_active = False
            self.observer_absent_since = None
            if packet.serial != self.last_serial:
                self.last_serial = packet.serial
                # Use the UI's monotonic clock for misses and frames alike. A
                # delayed camera packet must not move the session clock backwards.
                if self.session_gate.observe(now, True):
                    self.on_observation(now)
        else:
            if packet.frame is not None:
                self.last_serial = packet.serial
            self.session_gate.observe(now, False)
            if self.observer_absent_since is None:
                self.observer_absent_since = now
            if now-self.observer_absent_since >= self.session_gate.absence_seconds:
                self.observer_active = False
        return valid and self.observer_active

    def current_packet(self):
        if self.worker is None or self.paused or self.worker.stop_event.is_set():
            return core.FacePacket()
        packet = self.worker.latest()
        if (time.monotonic() - packet.timestamp > core.MAX_FRAME_AGE
                or packet.identity_version!=self.worker.identity_version):
            return core.FacePacket(error=packet.error)
        return packet

    def tick(self):
        if self.closing:
            return
        now = time.monotonic()
        packet = self.current_packet()
        if self.worker is not None and not self.worker.is_alive():
            latest = self.worker.latest()
            if latest.error:
                self.last_error = latest.error
                self.detail_var.set(latest.error)
            self.worker = None
            self.paused = True
            self.reset_observer()
            self.hide_bar(clear_deadline=True)
            self.camera_button.configure(text='开始保护', state='normal')
            self.set_camera_controls(False)
            self.update_tray()
            packet = core.FacePacket()
        if packet.error and packet.error != self.last_error:
            self.last_error = packet.error
            self.detail_var.set(packet.error)
        self.camera_fault=bool(packet.error or not self.paused and packet.frame is None)
        show_observer = self.observe_packet(packet, now)
        if show_observer:self.last_reaction_at=now
        self.update_enrollment(packet)
        self.process_protection(packet,now)
        self.process_recording(packet,now,show_observer)
        self.sync_window_masks(now)
        self.owner_summary.set('本人：已认出你' if packet.owner_boxes else '本人：已录入' if self.owner_samples else '本人：还没录入')
        if self.paused:
            self.status_var.set('正在关闭相机…' if self.worker else '已暂停 · 相机关闭')
        elif not packet.ready:
            self.status_var.set('相机开启中' if not packet.error else '相机异常')
        elif self.enrolling:
            self.status_var.set('本人录入中')
        elif self.owner_verifying:
            self.status_var.set('本人验证中')
        elif not self.owner_samples:
            self.status_var.set('请先录入本人')
        elif not self.rois:
            self.status_var.set('请框选后方区域')
        else:
            _summary,inside=core.detection_summary(packet,self.rois)
            if show_observer:status='观察者入镜'
            elif packet.face is not None and self.attention_required.get():
                candidate=next((item for item in packet.faces if item['bbox']==packet.face),None)
                status='面朝屏幕 · 确认中' if facing_screen(candidate) else '检测到人 · 等待朝向'
            elif any(item.get('identity')=='uncertain' for item in inside):status='身份待确认'
            elif any(item.get('identity')=='owner' for item in inside):status='本人已排除 · 监测中'
            else:status='监测中'
            self.status_var.set(status)
        if self.root.state() == 'normal':
            if self.preview_enabled.get():self.render_preview(packet)
        if self.adjusting:
            self.show_bar(packet, force_placeholder=True)
        elif show_observer:
            self.show_bar(packet)
        elif self.keep_performer_during_miss(packet,now):
            self.show_bar(packet,force_placeholder=True,performer_only=True)
        elif self.demo_bar and now < self.bar_until:
            self.show_bar(packet, force_placeholder=True)
        else:
            self.hide_bar()
        self.tick_after=self.root.after(33, self.tick)

    def on_observation(self, now):
        self.observer_active = True
        self.bar_until = 0.0
        self.demo_bar = False
        if getattr(self,'performer_preview_button',None) and self.performer_preview_button.winfo_exists():self.performer_preview_button.configure(text='预览动画')
        if self.stats is not None:
            try:
                self.stats.record_event(local_now())
                if self.stats_window and self.stats_window.winfo_exists():
                    self.render_stats()
            except OSError as exc:
                self.detail_var.set('这次统计未能保存：' + str(exc))

    def preview_point(self, event):
        if not self.preview_map:
            return None
        x, y, width, height = self.preview_map
        return max(0, min(1, (event.x - x) / width)), max(0, min(1, (event.y - y) / height))

    def roi_start(self, event):
        if self.enrolling or self.owner_verifying:return
        if self.selection_mode not in ('detect', 'exclude') or not self.preview_map:
            return
        x, y, w, h = self.preview_map
        if x <= event.x <= x + w and y <= event.y <= y + h:
            self.drag_start = self.preview_point(event)
            self.drag_end = self.drag_start

    def roi_drag(self, event):
        if self.drag_start:
            self.drag_end = self.preview_point(event)

    def roi_finish(self, event):
        if not self.drag_start:
            return
        start, end = self.drag_start, self.preview_point(event)
        self.drag_start = self.drag_end = None
        if end and abs(end[0] - start[0]) >= .025 and abs(end[1] - start[1]) >= .025:
            roi = (min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1]))
            if self.selection_mode == 'exclude':
                self.add_exclusion(roi)
            else:
                self.add_roi(roi)

    def add_exclusion(self, roi):
        valid = core.normalize_rois([roi])
        if valid:
            self.exclusions = core.normalize_rois(self.exclusions + valid)
            self.update_settings()
            self.detail_var.set(f'本人排除区 {len(self.exclusions)} 个。请覆盖转头、前倾时的头部活动范围。')

    def add_roi(self, roi):
        valid = core.normalize_rois([roi])
        if valid:
            self.rois = core.normalize_rois(self.rois + valid)
            self.update_settings()
            self.detail_var.set(f'已添加第 {len(self.rois)} 个检测框，可继续拖拽新增。框内留出头发和耳侧空间，并避开本人。')

    def undo_roi(self):
        if self.rois:
            self.rois.pop()
            self.update_settings()
        self.detail_var.set(f'剩余 {len(self.rois)} 个检测框。拖拽可以继续新增。')

    def clear_roi(self):
        self.rois = []
        self.update_settings()
        self.detail_var.set('检测框已清空。未选择检测框时不会自动触发。')

    def render_preview(self, packet):
        canvas = self.preview
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        key=(packet.serial,id(packet.frame),packet.error,width,height,tuple(map(tuple,self.rois)),
             self.drag_start,self.drag_end,self.debug.get(),self.enrolling,self.attention_required.get(),self.paused)
        if key==getattr(self,'_preview_render_key',None):return
        self._preview_render_key=key
        canvas.delete('preview-overlay')
        if packet.frame is None:
            canvas.delete('preview-frame')
            self.preview_image = self.preview_map = None
            message = packet.error or '相机关闭\n开启后，用鼠标框选后方观察区域。'
            if self.worker and not self.paused and not packet.error:
                message = '正在开启相机…'
            canvas.create_text(width / 2, height / 2, text=message, fill=self.MUTED,
                               width=max(200, width - 60), font=('Microsoft YaHei UI', 11), justify='center',tags='preview-overlay')
            self.detector_var.set('相机关闭' if self.paused else '等待有效画面')
            return
        frame = packet.frame
        fh, fw = frame.shape[:2]
        dw, dh = core.fit_size(fw, fh, width, height)
        ox, oy = (width - dw) / 2, (height - dh) / 2
        self.preview_map = ox, oy, dw, dh
        picture = core.Image.fromarray(core.cv2.cvtColor(frame, core.cv2.COLOR_BGR2RGB))
        previous_image=self.preview_image
        self.preview_image = core.ImageTk.PhotoImage(picture.resize((dw, dh), core.Image.Resampling.BILINEAR))
        if canvas.find_withtag('preview-frame'):
            canvas.itemconfigure('preview-frame',image=self.preview_image)
            canvas.coords('preview-frame',ox,oy)
        else:canvas.create_image(ox,oy,image=self.preview_image,anchor='nw',tags='preview-frame')
        for areas, color, label in ((self.rois, self.ACCENT, '检测'),):
            for number, roi in enumerate(areas, 1):
                x1, y1, x2, y2 = roi
                canvas.create_rectangle(ox+x1*dw, oy+y1*dh, ox+x2*dw, oy+y2*dh, outline=color, width=2)
                canvas.create_text(ox+x1*dw+5, oy+y1*dh+5, text=f'{label} {number}',
                                   anchor='nw', fill=color, font=('Microsoft YaHei UI', 9))
        if self.drag_start and self.drag_end:
            x1, y1 = self.drag_start
            x2, y2 = self.drag_end
            color = '#f29393' if self.selection_mode == 'exclude' else self.ACCENT
            canvas.create_rectangle(ox+x1*dw, oy+y1*dh, ox+x2*dw, oy+y2*dh,
                                    outline=color, width=2, dash=(5, 3))
        detected = getattr(packet, 'faces', []) or []
        accepted = 0
        for item in detected:
            face = item.get('bbox')
            if not face:
                continue
            x, y, w, h = face
            owner = item.get('identity')=='owner'
            selected = (item.get('accepted', False) and not owner
                        and core.matching_region(face, fw, fh, self.rois) is not None)
            accepted += int(selected)
            if not self.debug.get() and item.get('confidence',0)<.55 and not owner:
                continue
            color = self.ACCENT if owner or selected else '#7A8178'
            canvas.create_rectangle(ox+x*dw/fw, oy+y*dh/fh, ox+(x+w)*dw/fw, oy+(y+h)*dh/fh,
                                    outline=color, width=2 if selected else 1)
            text = '本人 · 已排除' if owner else '点击本人' if self.enrolling else item.get('reason','身份待确认')
            if selected and self.attention_required.get():
                text='非本人 · 等待约 1 秒' if facing_screen(item) else '非本人 · 等待面朝屏幕'
            if self.debug.get():text+=f' · 检测分数 {item.get("confidence",0):.2f}'
            canvas.create_text(ox+x*dw/fw, max(oy+15,oy+y*dh/fh-3), text=text,
                               fill=color, anchor='sw', font=('Microsoft YaHei UI', 8))
        for x,y,w,h in packet.owner_boxes:
            canvas.create_rectangle(ox+x*dw/fw,oy+y*dh/fh,ox+(x+w)*dw/fw,oy+(y+h)*dh/fh,
                                    outline=self.ACCENT,width=2,dash=(4,2))
        summary,_inside=core.detection_summary(packet,self.rois)
        self.detector_var.set(summary+'\n'+(getattr(packet,'capture_info','') or f'{fw}×{fh}'))
        if not self.rois:
            canvas.create_text(width / 2, 20, text='点击“检测区”，框选后方区域', fill=self.TEXT, font=('Microsoft YaHei UI', 10))
        canvas.addtag_all('preview-overlay')
        canvas.dtag('preview-frame','preview-overlay')

    def bar_size(self):
        if self.custom_bar_size:return tuple(self.custom_bar_size)
        sizes=PERFORMER_SIZES if self.using_performer() else HEAD_SIZES
        return sizes.get(self.display_size.get(), (560,340) if self.using_performer() else (360,280))

    def using_performer(self):
        return self.frame_style.get()==FRAME_STYLES[0]

    def frame_style_changed(self):
        choices=PERFORMER_SIZES if self.using_performer() else HEAD_SIZES
        self.display_size.set(next(iter(choices)))
        if getattr(self,'size_picker',None) is not None and self.size_picker.winfo_exists():
            self.size_picker.configure(values=tuple(choices))
        self.custom_bar_size=None;self.performer_started_at=None;self.performer_target=None
        self.hide_bar(clear_deadline=True);self.save_config()

    def preview_performer(self):
        if self.demo_bar:
            self.hide_bar(clear_deadline=True)
            return
        if not self.using_performer():
            self.frame_style.set(FRAME_STYLES[0]);self.frame_style_changed()
        self.manual_trigger()

    def open_performer_panel(self):
        if self.performer_panel is not None:
            self.performer_panel.window.lift();return
        self.performer_panel=PerformerPanel(self,getattr(self,'active_media_dir',performer_asset_dir()))

    def clear_mask_frames(self):
        for mask in self.masks:
            if hasattr(mask,'_canvas') and getattr(mask,'_performer_active',False):
                mask._canvas.itemconfigure('performer-frame',state='hidden')
                mask._canvas.itemconfigure('performer-hint',state='hidden')
                mask._canvas.itemconfigure('message',state='normal')
                mask._performer_image=None
                mask._performer_active=False

    def paint_mask_frames(self,picture):
        painted=False
        for mask in self.masks:
            if not hasattr(mask,'_canvas') or not mask.winfo_viewable():continue
            _x,_y,width,height=mask._bounds
            if width<280 or height<200:continue
            size=core.fit_size(picture.width,picture.height,min(width-24,picture.width),min(height-55,picture.height))
            image=picture.resize(size,core.Image.Resampling.BILINEAR)
            mask._performer_image=core.ImageTk.PhotoImage(image)
            if not getattr(mask,'_performer_items_created',False):
                mask._canvas.create_image(width/2,(height-28)/2,anchor='center',tags='performer-frame')
                mask._canvas.create_text(width/2,height-18,text='双击恢复 · Ctrl+Alt+H',fill=self.MUTED,
                    font=('Microsoft YaHei UI',9),tags='performer-hint')
                mask._performer_items_created=True
            mask._canvas.itemconfigure('message',state='hidden')
            mask._canvas.itemconfigure('performer-frame',image=mask._performer_image,state='normal')
            mask._canvas.itemconfigure('performer-hint',state='normal')
            mask._canvas.coords('performer-frame',width/2,(height-28)/2)
            mask._canvas.coords('performer-hint',width/2,height-18)
            mask._performer_active=True
            painted=True
        return painted

    def display_dimensions(self):
        scale = getattr(self, 'ui_scale', 1.0)
        dimensions=tuple(round(size*scale) for size in self.bar_size())
        _x,_y,width,height,_pw,_ph=core.desktop_bounds(self.root)
        return min(dimensions[0],width),min(dimensions[1],height)

    def ensure_bar(self):
        width, height = self.display_dimensions()
        if self.bar is None:
            self.bar = tk.Toplevel(self.root)
            self.bar.withdraw()
            self.bar.overrideredirect(True)
            self.bar.configure(bg=self.BG)
            self.bar_canvas = tk.Canvas(self.bar, width=width, height=height,
                                        highlightthickness=0, bg='#080d12')
            self.bar_canvas.pack(fill='both', expand=True)
            self.bar_canvas.bind('<ButtonPress-1>', self.start_bar_drag)
            self.bar_canvas.bind('<B1-Motion>', self.drag_bar)
            self.bar_canvas.bind('<Motion>',self.bar_cursor)
            self.bar_canvas.bind('<ButtonRelease-1>',lambda _e:setattr(self,'drag_origin',None))
            self.bar_canvas.bind('<Double-Button-1>', lambda _e: self.finish_adjust())
        vx, vy, vw, vh, pw, _ph = core.desktop_bounds(self.root)
        width,height=min(width,vw),min(height,vh)
        if not self.bar_position or not (vx <= self.bar_position[0] <= vx+vw-width
                                         and vy <= self.bar_position[1] <= vy+vh-height):
            self.bar_position = [max(0, pw-width-round(24*self.ui_scale)), round(64*self.ui_scale)]
        geometry=(*self.bar_position,width,height)
        if geometry!=getattr(self,'_bar_geometry',None):
            self.bar_canvas.configure(width=width, height=height)
            place_window(self.bar, *self.bar_position, width, height)
            self._bar_geometry=geometry
        if self.adjusting!=getattr(self,'_bar_adjusting',None):
            configure_floating_window(self.bar, click_through=not self.adjusting, no_activate=True)
            self._bar_adjusting=self.adjusting

    def update_bar_picture(self,picture,width,height):
        self.bar_image=core.ImageTk.PhotoImage(picture)
        if not getattr(self,'_bar_picture_created',False):
            self.bar_canvas.create_image(width/2,height/2,anchor='center',tags='bar-picture')
            self._bar_picture_created=True
        self.bar_canvas.itemconfigure('bar-picture',image=self.bar_image)
        self.bar_canvas.coords('bar-picture',width/2,height/2)

    def keep_performer_during_miss(self,packet,now):
        """Short display grace contains only the static actor, never stale faces."""
        last=getattr(self,'last_reaction_at',None)
        if (last is None or now-last>.45 or not self.using_performer() or not self.observer_active
                or self.paused or self.enrolling or self.owner_verifying or not self.owner_samples
                or packet.frame is None or not packet.ready or packet.error):return False
        target=getattr(self,'performer_target',None)
        candidate=next((item for item in packet.faces if item.get('track_id')==target),None)
        if candidate is not None:
            if candidate.get('identity')=='owner':return False
            if self.attention_required.get() and not facing_screen(candidate):return False
        return True

    def show_bar(self, packet=None, force_placeholder=False,performer_only=False):
        if packet is None:
            packet = self.current_packet()
        self.ensure_bar()
        width, height = self.display_dimensions()
        canvas = self.bar_canvas
        crop = None if self.demo_bar or performer_only else self.clean_observer_crop(packet)
        overlap_hidden=(not self.demo_bar and not performer_only and self.valid_observer(packet) and crop is None)
        if crop is None:
            if not force_placeholder and not (overlap_hidden and self.using_performer()):
                self.hide_bar()
                return
        mode=('performer' if self.using_performer() else 'placeholder' if crop is None else 'head',
              self.demo_bar,self.adjusting)
        if mode!=getattr(self,'_bar_canvas_mode',None):
            canvas.delete('all')
            self._bar_picture_created=False
            self._bar_canvas_mode=mode
            if self.demo_bar and self.using_performer():
                canvas.create_text(width/2,round(12*self.ui_scale),text='效果预演 · 相机状态不变',fill=self.MUTED,
                    font=('Microsoft YaHei UI',9),tags='bar-demo')
            elif crop is None and not self.using_performer():
                canvas.create_text(width/2,height/2,text='拖动移动 · 拖边缩放\n双击完成' if self.adjusting else '演示 · 等待后方画面',
                    fill=self.MUTED,width=width-10,justify='center',font=('Microsoft YaHei UI',9),tags='bar-placeholder')
        if self.using_performer():
            now=time.monotonic()
            item=next((item for item in packet.faces if item['bbox']==packet.face),{})
            target=('demo' if self.demo_bar or self.adjusting else
                    self.performer_target if performer_only else item.get('track_id','observer'))
            if self.performer_started_at is None or self.performer_target!=target:
                self.performer_started_at=now;self.performer_target=target
            if self.performer_renderer is None:self.performer_renderer=PerformerFrameRenderer(getattr(self,'active_media_dir',performer_asset_dir()),
                getattr(self,'performer_motions',None),getattr(self,'performer_phrases',None),background=True)
            rgb=core.cv2.cvtColor(crop,core.cv2.COLOR_BGR2RGB) if crop is not None else None
            protected=not(self.demo_bar or self.adjusting) and self.protection_confirmed()
            image=self.performer_renderer.render(rgb,(width,height),now-self.performer_started_at,protected=protected)
            if overlap_hidden or performer_only:
                # Keep the local performer running while withholding a portrait
                # that would expose the owner or display a partially black face.
                from PIL import ImageDraw
                layout=self.performer_renderer._layout((width,height))
                box=layout['observer'];draw=ImageDraw.Draw(image)
                draw.rounded_rectangle(box,12,fill=self.PANEL)
                note='画面短暂丢失\n正在重新确认' if performer_only else '人物重叠\n暂不显示特写'
                draw.text(((box[0]+box[2])/2,(box[1]+box[3])/2),note,
                          anchor='mm',align='center',fill=self.MUTED,
                          font=self.performer_renderer._font(13*layout['scale']))
                if performer_only and not protected:
                    draw.rectangle((round(width*.45),0,width,round(40*layout['scale'])),fill=self.BG)
                    draw.text((width-18,round(19*layout['scale'])),'正在重新确认 · 人物画面已暂隐',anchor='ra',
                              fill=self.MUTED,font=self.performer_renderer._font(10*layout['scale']))
            if protected and self.paint_mask_frames(image):
                if self.bar is not None and self.bar_shown:self.bar.withdraw()
                self.bar_shown=False
                return
            self.clear_mask_frames()
            self.update_bar_picture(image,width,height)
            if self.demo_bar:
                canvas.coords('bar-demo',width/2,round(12*self.ui_scale))
                canvas.tag_raise('bar-demo')
        elif crop is None:
            self.clear_mask_frames()
            canvas.coords('bar-placeholder',width/2,height/2)
            canvas.itemconfigure('bar-placeholder',width=width-10)
        else:
            self.clear_mask_frames()
            crop = deform(crop,self.effect.get(),core.cv2)
            image = core.Image.fromarray(core.cv2.cvtColor(crop, core.cv2.COLOR_BGR2RGB))
            image = image.resize(core.fit_size(image.width, image.height, width, height), core.Image.Resampling.BILINEAR)
            self.update_bar_picture(image,width,height)
            if self.bugs.get()=='特写里':draw_bugs(canvas,width,height,time.monotonic(),self.ui_scale)
        if self.adjusting:
            canvas.delete('bar-adjust')
            canvas.create_text(width/2,height-12,text='拖动移动 · 拖边缩放 · 双击完成',fill=self.ACCENT,
                               font=('Microsoft YaHei UI',9),tags='bar-adjust')
            canvas.create_rectangle(1,1,width-2,height-2,outline=self.ACCENT,width=2,tags='bar-adjust')
            for x,y in ((2,2),(width-9,2),(2,height-9),(width-9,height-9)):
                canvas.create_rectangle(x,y,x+7,y+7,fill=self.ACCENT,outline='',tags='bar-adjust')
        if not self.bar_shown:
            self.bar.deiconify()
            configure_floating_window(self.bar, click_through=not self.adjusting, no_activate=True)
            self.bar_shown = True

    def hide_bar(self, clear_deadline=False):
        if self.bar is not None and (self.bar_shown or getattr(self,'_bar_canvas_mode',None) is not None):
            self.bar.withdraw()
            self.bar_canvas.delete('all')
            self._bar_canvas_mode=None
            self._bar_picture_created=False
        self.bar_shown = False
        self.bar_image = None
        self.clear_mask_frames()
        self.performer_started_at=None;self.performer_target=None
        if clear_deadline:
            self.bar_until = 0.0
            self.demo_bar = False
            self.adjusting = False
            if getattr(self,'performer_preview_button',None) and self.performer_preview_button.winfo_exists():self.performer_preview_button.configure(text='预览动画')

    def manual_trigger(self):
        # A preview changes only the small display, never statistics or privacy masks.
        self.bar_until = time.monotonic() + DEMO_SECONDS
        self.demo_bar = True
        if getattr(self,'performer_preview_button',None) and self.performer_preview_button.winfo_exists():self.performer_preview_button.configure(text='停止预览')
        self.performer_started_at=None;self.performer_target=None
        self.show_bar(force_placeholder=True)

    def change_crop(self):
        self.custom_bar_size = None
        self.save_config()
        if self.bar_shown:
            self.show_bar(force_placeholder=self.demo_bar or self.adjusting)

    def adjust_bar(self):
        self.adjusting = True
        self.show_bar(force_placeholder=True)
        self.detail_var.set('拖动移动，拖边缘或四角调整大小，双击完成。')

    def bar_edges(self, event):
        width,height=self.display_dimensions();margin=12*self.ui_scale
        return ('n' if event.y<margin else 's' if event.y>height-margin else '')+('w' if event.x<margin else 'e' if event.x>width-margin else '')

    def bar_cursor(self,event):
        cursor={'n':'top_side','s':'bottom_side','w':'left_side','e':'right_side','nw':'top_left_corner',
                'ne':'top_right_corner','sw':'bottom_left_corner','se':'bottom_right_corner'}
        self.bar_canvas.configure(cursor=cursor.get(self.bar_edges(event),'fleur') if self.adjusting else '')

    def start_bar_drag(self, event):
        if self.adjusting:
            self.drag_origin = (event.x_root, event.y_root, *self.bar_position,*self.display_dimensions(),self.bar_edges(event))

    def drag_bar(self, event):
        if not self.adjusting or not self.drag_origin:
            return
        ex,ey,bx,by,w,h,edges = self.drag_origin
        vx, vy, vw, vh, _pw, _ph = core.desktop_bounds(self.root)
        x,y,width,height=resized_rect((bx,by,w,h),edges,event.x_root-ex,event.y_root-ey,(vx,vy,vw,vh),
                                     (round((420 if self.using_performer() else 140)*self.ui_scale),round((260 if self.using_performer() else 100)*self.ui_scale)),
                                     (round(960*self.ui_scale),round(720*self.ui_scale)))
        self.bar_position=[x,y]
        if edges:self.custom_bar_size=[round(width/self.ui_scale,2),round(height/self.ui_scale,2)]
        place_window(self.bar, *self.bar_position, width, height)

    def finish_adjust(self):
        self.adjusting = False
        self.drag_origin = None
        self.hide_bar(clear_deadline=True)
        self.save_config()
        self.detail_var.set('特写窗口的位置和大小已保存。平时鼠标穿透，不抢焦点。')

    def valid_protect_region(self, region=None):
        if region is None:
            return any(self.valid_protect_region(r) for r in self.protect_regions)
        if not region:
            return False
        x, y, w, h = region
        vx, vy, vw, vh, _pw, _ph = core.desktop_bounds(self.root)
        return w >= 60 and h >= 40 and x >= vx and y >= vy and x+w <= vx+vw and y+h <= vy+vh

    def update_region_label(self):
        self.protect_regions = [r for r in self.protect_regions if self.valid_protect_region(r)]
        self.region_var.set(f'保护：窗口 {len(self.protected_windows)} 个 · 固定区域 {len(self.protect_regions)} 个')

    def undo_protect_region(self):
        was_protecting = bool(self.masks)
        if self.protect_regions:
            self.protect_regions.pop()
        self.restore()
        if not self.has_protection():
            self.sensitive.set(False)
        self.update_region_label()
        self.update_tray()
        self.save_config()
        if was_protecting and self.protect_regions:
            self.show_mask()

    def clear_protect_regions(self):
        self.protect_regions = []
        self.restore()
        self.sensitive.set(bool(self.protected_windows))
        self.update_region_label()
        self.update_tray()
        self.save_config()

    def toggle_sensitive(self):
        self.sensitive.set(not self.sensitive.get())
        self.sensitive_changed()

    def sensitive_changed(self):
        self.protection_gate.reset();self.protection_serial=-1
        if self.sensitive.get() and not self.has_protection():
            self.sensitive.set(False)
            self.show_settings()
            self.open_window_picker()
            self.detail_var.set('先点选要保护的窗口，选好后会开启自动遮挡。')
        elif not self.sensitive.get():
            self.restore()
        elif self.observer_active and self.valid_observer(self.current_packet()):
            self.show_mask(automatic=True)
        self.update_tray()
        self.update_protection_summary()

    def select_protect_region(self):
        if self.selector is not None:
            return
        was_protecting = bool(self.masks)
        self.restore()
        vx, vy, vw, vh, _pw, _ph = core.desktop_bounds(self.root)
        window = tk.Toplevel(self.root)
        self.selector = window
        window.withdraw()
        window.overrideredirect(True)
        window.configure(bg='#183b31')
        window.attributes('-alpha', .35)
        canvas = tk.Canvas(window, bg='#183b31', highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        place_window(window, vx, vy, vw, vh)
        configure_floating_window(window, click_through=False, no_activate=False)
        canvas.create_text(vw/2, 45, text='拖拽框选要隐藏的屏幕区域 · Esc 取消', fill='#ffffff',
                           font=('Microsoft YaHei UI', 18, 'bold'))
        start = [None]
        rectangle = [None]
        def cancel(_event=None):
            try:
                window.grab_release()
            except tk.TclError:
                pass
            window.destroy()
            self.selector = None
            if was_protecting and self.valid_protect_region():
                self.show_mask()
        def begin(event):
            start[0] = (event.x, event.y)
        def drag(event):
            if start[0]:
                if rectangle[0] is not None:
                    canvas.delete(rectangle[0])
                rectangle[0] = canvas.create_rectangle(*start[0], event.x, event.y, outline='#ffffff', width=3)
        def finish(event):
            if not start[0]:
                return
            x1, y1 = start[0]
            x2, y2 = max(0, min(vw, event.x)), max(0, min(vh, event.y))
            width, height = abs(x2-x1), abs(y2-y1)
            if width < 60 or height < 40:
                self.detail_var.set('保护区域太小，请重新选择至少 60×40 的区域。')
            else:
                self.protect_regions.append([vx+min(x1, x2), vy+min(y1, y2), width, height])
                self.update_region_label()
                self.save_config()
                self.detail_var.set(f'已添加第 {len(self.protect_regions)} 个保护区，可再次点击添加。Ctrl+Alt+H 同时遮挡／恢复。')
            cancel()
            if self.sensitive.get() and self.observer_active and self.valid_observer(self.current_packet()):
                self.show_mask()
        canvas.bind('<ButtonPress-1>', begin)
        canvas.bind('<B1-Motion>', drag)
        canvas.bind('<ButtonRelease-1>', finish)
        window.bind('<Escape>', cancel)
        window.deiconify()
        window.focus_force()
        window.grab_set()

    def toggle_mask(self):
        if self.mask_active:
            self.restore()
        elif self.has_protection():
            self.show_mask()
        else:
            self.show_settings()
            self.open_window_picker()
            self.detail_var.set('先选一个需要保护的窗口。')

    def show_mask(self,automatic=False):
        if self.mask_active or not self.has_protection():
            return
        self.mask_active=True
        self.auto_mask=automatic
        for region in self.protect_regions:
            if not self.valid_protect_region(region):
                continue
            self.make_mask(region)
        self.sync_window_masks(time.monotonic())
        self.update_protection_summary()

    def restore(self):
        for mask in self.masks:
            mask.destroy()
        self.masks = []
        self.protected_masks={};self.mask_active=False;self.auto_mask=False
        self.coverage_errors=[];self.coverage_expected=0;self.coverage_checked=0
        self.update_protection_summary()

    def open_stats(self):
        if self.stats_window is not None and self.stats_window.winfo_exists():
            self.stats_window.deiconify()
            self.stats_window.lift()
            return
        self.stats_window = tk.Toplevel(self.root)
        self.stats_window.title(PRODUCT_TITLE+' · 统计与成就')
        self.stats_window.geometry(f'{round(420*self.ui_scale)}x{round(440*self.ui_scale)}')
        self.stats_window.minsize(round(400*self.ui_scale), round(420*self.ui_scale))
        self.stats_window.configure(bg=self.BG)
        self.stats_panel = ttk.Frame(self.stats_window, padding=20)
        self.stats_panel.pack(fill='both', expand=True)
        self.render_stats()

    def render_stats(self):
        for child in self.stats_panel.winfo_children():
            child.destroy()
        panel = self.stats_panel
        ttk.Label(panel, text='后方观察事件', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w', pady=(0, 14))
        if self.stats is None:
            ttk.Label(panel, text='统计数据暂不可用，旧文件已保留。\n可备份旧文件并重新开始。',
                      wraplength=370*self.ui_scale, foreground=self.MUTED).pack(anchor='w', pady=12)
            ttk.Button(panel, text='备份并重置统计…', command=self.clear_stats).pack(anchor='w')
            return
        summary = self.stats.summary(local_now().date())
        ttk.Label(panel, text=f'今日 {summary["today"]} 次    累计 {summary["total"]} 次',
                  font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w', pady=(0, 16))
        for achievement in summary['milestones']:
            row = ttk.Frame(panel)
            row.pack(fill='x', pady=7)
            unlocked = achievement.get('unlocked', achievement.get('unlocked_at') is not None)
            ttk.Label(row, text=('✓  ' if unlocked else '○  ') + achievement['title'],
                      foreground=self.ACCENT if unlocked else self.MUTED).pack(side='left')
            ttk.Label(row, text=f'累计 {achievement["threshold"]} 次', foreground=self.MUTED).pack(side='right')
        ttk.Label(panel, text='一次连续观察记一次；离开至少 2 秒后再来才重计。\n统计只记次数和时间；录像需单独开启。\n成就默认静音，不弹出庆祝窗口。',
                  foreground=self.MUTED, wraplength=370*self.ui_scale).pack(anchor='w', pady=(20, 12))
        ttk.Button(panel, text='清空统计…', command=self.clear_stats).pack(anchor='w')

    def clear_stats(self):
        if not messagebox.askyesno('清空统计', '清空累计次数、每日次数和成就进度？', parent=self.stats_window):
            return
        try:
            if self.stats is None:
                if self.stats_path.exists():
                    backup = self.stats_path.with_name('statistics.backup-' + local_now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
                    self.stats_path.rename(backup)
                self.stats = StatsStore(self.stats_path)
            self.stats.clear()
            self.stats_error = ''
            self.render_stats()
        except (OSError, StatsDataError) as exc:
            messagebox.showerror('统计未能重置', str(exc), parent=self.stats_window)

    def close(self):
        if self.closing:
            return
        self.closing = True
        if getattr(self,'media_poll',None) is not None:
            self.root.after_cancel(self.media_poll);self.media_poll=None
        if self.tick_after is not None:
            self.root.after_cancel(self.tick_after);self.tick_after=None
        if self.worker:
            self.worker.stop()
        self.recorder.close()
        if getattr(self,'performer_panel',None):self.performer_panel.close()
        if self.window_picker:self.window_picker.close()
        if self.recordings_panel:self.recordings_panel.close()
        if self.window_api:self.window_api.close()
        self.save_config()
        self.hide_bar(clear_deadline=True)
        self.restore()
        if self.hotkey:
            self.hotkey.close()
        if self.tray:
            self.tray.close()
        self.root.destroy()


def self_test():
    core.load_dependencies()
    import numpy as np
    detector = core.YuNetDetector()
    result = detector.detect(np.zeros((320, 320, 3), dtype=np.uint8), [(0, 0, 1, 1)], '高')
    assert result.face is None
    from small_mirror_identity import SFace,dpapi
    recognizer=SFace(core.cv2)
    probe=recognizer.engine.feature(np.zeros((112,112,3),dtype=np.uint8))
    assert probe.size==128 and np.isfinite(probe).all()
    assert dpapi(dpapi(b'small-mirror-local-check'),decrypt=True)==b'small-mirror-local-check'
    assert core.observer_crop((10, 10, 20, 20), 100, 100, None) is None
    assert core.fit_size(30, 20, 200, 80) == (120, 80)
    gate = EventSessionGate()
    assert not gate.observe(0, True)
    assert not gate.observe(.25, True)
    assert gate.observe(.51, True)
    assert not gate.observe(5, True)
    gate.observe(6, False)
    assert not gate.observe(6.5, True)
    gate.observe(7, False)
    gate.observe(9.1, False)
    assert not gate.observe(9.2, True)
    assert not gate.observe(9.45, True)
    assert gate.observe(9.71, True)
    assert HEAD_SIZES['明显 360×280'] == (360, 280)
    assert len(MOTIONS)==6 and len(PHRASES)==3
    renderer=PerformerFrameRenderer(performer_asset_dir())
    # Third-party actor media is optional. Missing clips must use the original
    # sentry, while all model, encryption and compositor checks still run.
    renderer.configure([row[0] for row in MOTIONS],[row[0] for row in PHRASES])
    for _motion,start,seconds in renderer.program.timeline():
        picture=renderer.render(None,(560,340),start+.6*seconds,protected=True)
        assert picture.size==(560,340) and picture.mode=='RGB'
    print(f'SELF-TEST PASS: YuNet/SFace, Windows encryption, crop/gating, {len(MOTIONS)} local programmes and {len(renderer._clips)} optional clips; camera remained off.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=PRODUCT_TITLE+'：本机人脸录入、自动排除本人、可缩放特写。')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--self-test', action='store_true')
    group.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-shell', action='store_true')
    args = parser.parse_args()
    instance = None
    app = None
    root = None
    try:
        if args.self_test:
            return self_test()
        if args.smoke_test and args.data_dir is None:
            raise RuntimeError('测试模式必须提供工作目录内的 --data-dir。')
        instance = SingleInstance(args.data_dir or default_data_dir())
        if instance.already_running:
            if not args.smoke_test:
                messagebox.showinfo(PRODUCT_CN+'已运行', '请在任务栏托盘中打开已有的窥贼。')
            return 0
        core.load_dependencies()
        core.enable_dpi_awareness()
        root = tk.Tk()
        if args.smoke_test:
            root.withdraw()
        app = PrivacyApp(root, args.data_dir, enable_shell=not (args.no_shell or args.smoke_test))
        if args.smoke_test:
            root.update_idletasks()
            root.update()
            assert app.worker is None
            assert app.performer_renderer.wait_for_assets(10), 'Local actor asset decoding timed out'
            assert app.stats is not None
            assert app.stats.total == 0
            assert not app.owner_samples and app.recorder.target is None
            app.preview_performer()
            root.update_idletasks()
            assert app.stats.total == 0
            assert app.performer_renderer is not None
            assert app.bar_image is not None and app.worker is None
            assert app.recorder.target is None
            app.open_performer_panel()
            assert len(app.performer_panel.motion_vars)==len(MOTIONS) and len(app.performer_panel.phrase_vars)==len(PHRASES)
            app.performer_panel.preview('patrol')
            assert app.performer_panel.playing and app.performer_panel.image is not None
            app.performer_panel.close()
            root.update()
            app.hide_bar(clear_deadline=True)
            assert not app.masks
            # Exercise the packaged encoder with synthetic pixels, never a camera.
            import numpy as np
            recorder=EventRecorder(args.data_dir/'smoke-codec',core.cv2)
            for index in range(6):
                recorder.feed(np.full((120,160,3),40+index*20,dtype=np.uint8),index/6,index,'synthetic')
            recorder.close()
            assert not recorder.error and recorder.last_saved is not None,recorder.error
            capture=core.cv2.VideoCapture(str(recorder.last_saved))
            try:
                okay,frame=capture.read()
                assert okay and frame.shape[:2]==(240,320)
            finally:
                capture.release()
            recorder.last_saved.unlink()
            recorder.directory.rmdir()
            # Exercise the packaged lazy wizard module with a synthetic camera
            # frame. This must never start the real camera or save a template.
            if app.tick_after is not None:
                root.after_cancel(app.tick_after);app.tick_after=None
            app.open_owner_settings()
            root.update()
            app.enrolling=True;app.enroll_selected=True;app.owner_candidate=7
            synthetic=core.FacePacket(frame=np.full((180,320,3),120,dtype=np.uint8),ready=True,
                faces=[dict(bbox=(140,50,50,60),track_id=7,identity='uncertain')],
                enrollment_status=dict(stage=1,count=4,done=False,message='往左转一点 ←'))
            app.update_enrollment(synthetic);root.update()
            assert sum(app.owner_preview.type(item)=='arc' for item in app.owner_preview.find_all())==12,'Guidance ring did not draw 12 segments'
            assert app.owner_progress['value']==4 and app.worker is None,'Guidance progress or camera state incorrect'
            assert not app.owner_store.path.exists()
            app.cancel_enrollment()
            if app.notices:
                raise RuntimeError('；'.join(app.notices))
            app.close()
            print(f'SMOKE-TEST PASS: UI, guided enrollment ring, local animation preview ({len(app.performer_renderer._clips)} local clips loaded), zero manual counts and synthetic clip encode/decode; camera remained off.')
        else:
            root.after(120, root.deiconify)
            root.mainloop()
        return 0
    except Exception as exc:
        if sys.stderr is not None:
            print('启动失败：' + str(exc), file=sys.stderr)
        if not args.self_test and not args.smoke_test:
            messagebox.showerror('无法启动'+PRODUCT_CN, str(exc))
        return 1
    finally:
        if app is not None and not app.closing:
            app.close()
        elif app is None and root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        if instance:
            instance.close()


if __name__ == '__main__':
    raise SystemExit(main())
