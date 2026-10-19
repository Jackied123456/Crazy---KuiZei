"""Programme selection and a silent, camera-free local animation preview."""
import time
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from small_mirror_performer import (
    PerformerFrameRenderer, MOTIONS, PHRASES, DEFAULT_MOTIONS, DEFAULT_PHRASES,
    normalize_choices, BG, PANEL, TEXT, MUTED, BORDER,
)


class PerformerPanel:
    def __init__(self, app, asset_dir):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('窥贼 · 动作与台词')
        scale = min(1.5,max(.8,getattr(app,'ui_scale',1)))
        width = min(round(980*scale), self.window.winfo_screenwidth()-70)
        height = min(round(620*scale), self.window.winfo_screenheight()-100)
        self.window.geometry(f'{width}x{height}')
        self.window.minsize(740,470)
        self.window.configure(bg=BG)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.playing = False
        self.image = None
        self.after_id = None
        self.started_at = None
        self.elapsed = 0
        self._closed = False
        self._resize_id = None
        self.motion_vars = {}
        self.phrase_vars = {}
        selected = normalize_choices(getattr(app,'performer_motions',None),[row[0] for row in MOTIONS],DEFAULT_MOTIONS)
        styles = normalize_choices(getattr(app,'performer_phrases',None),[row[0] for row in PHRASES],DEFAULT_PHRASES)
        self.renderer = PerformerFrameRenderer(asset_dir,selected,styles,background=True)
        outer = ttk.Frame(self.window,padding=20)
        outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='动作与台词',font=('Microsoft YaHei UI',17,'bold')).pack(anchor='w')
        ttk.Label(outer,text='选择喜欢的节目，检测到持续围观时轮流播放。',foreground=MUTED).pack(anchor='w',pady=(6,18))
        content = ttk.Frame(outer)
        content.pack(fill='both',expand=True)
        controls = ttk.Frame(content,width=210)
        controls.pack(side='left',fill='y',padx=(0,20))
        ttk.Label(controls,text='节目',font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w',pady=(0,8))
        for motion,label,_placeholder,_style in MOTIONS:
            row = ttk.Frame(controls)
            row.pack(fill='x',pady=3)
            variable = tk.BooleanVar(value=motion in selected)
            self.motion_vars[motion] = variable
            ttk.Checkbutton(row,text=label,variable=variable).pack(side='left')
            ttk.Button(row,text='试看',width=4,command=lambda value=motion:self.preview(value)).pack(side='right',padx=(8,0))
        ttk.Separator(controls).pack(fill='x',pady=14)
        ttk.Label(controls,text='台词口味',font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w',pady=(0,6))
        for style,label,_group,_moods in PHRASES:
            variable = tk.BooleanVar(value=style in styles)
            self.phrase_vars[style] = variable
            ttk.Checkbutton(controls,text=label,variable=variable).pack(anchor='w',pady=3)
        ttk.Label(controls,text='多选后按节目轮换。',foreground=MUTED).pack(anchor='w',pady=(6,0))
        area = ttk.Frame(content)
        area.pack(side='right',fill='both',expand=True)
        self.canvas = tk.Canvas(area,bg=BG,highlightthickness=1,highlightbackground=BORDER)
        self.canvas.pack(fill='both',expand=True)
        self.canvas_item = self.canvas.create_image(0,0,anchor='center')
        self.canvas.bind('<Configure>',self._resize)
        self.info = tk.StringVar(value='已暂停 · 点击播放看看')
        ttk.Label(area,textvariable=self.info,foreground=MUTED).pack(anchor='w',pady=(10,8))
        preview_controls = ttk.Frame(area)
        preview_controls.pack(fill='x')
        self.play_button = ttk.Button(preview_controls,text='播放所选',command=self._toggle)
        self.play_button.pack(side='left')
        ttk.Button(preview_controls,text='从头播放',command=self.preview).pack(side='left',padx=8)
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x',pady=(18,0))
        ttk.Label(bottom,text='静音预览 · 不开相机、不录像、不计次',foreground=MUTED).pack(side='left')
        ttk.Button(bottom,text='保存选择',command=self.apply).pack(side='right')
        self.window.update_idletasks()
        self._draw()

    def _choices(self):
        motions = normalize_choices([key for key,var in self.motion_vars.items() if var.get()],
                                    [row[0] for row in MOTIONS],DEFAULT_MOTIONS)
        phrases = normalize_choices([key for key,var in self.phrase_vars.items() if var.get()],
                                    [row[0] for row in PHRASES],DEFAULT_PHRASES)
        return motions,phrases

    def _resize(self,_event=None):
        if self._resize_id is not None:
            self.window.after_cancel(self._resize_id)
        self._resize_id = self.window.after(100,self._draw_resized)

    def _draw_resized(self):
        self._resize_id = None
        self._draw()

    def _draw(self):
        if self._closed:
            return
        width,height = max(240,self.canvas.winfo_width()),max(160,self.canvas.winfo_height())
        elapsed = time.monotonic()-self.started_at if self.playing else self.elapsed
        picture = self.renderer.render(None,(width,height),elapsed,protected=False)
        self.image = ImageTk.PhotoImage(picture,master=self.window)
        self.canvas.coords(self.canvas_item,width/2,height/2)
        self.canvas.itemconfigure(self.canvas_item,image=self.image)
        if self.playing:
            state = self.renderer.program.state(elapsed)
            self.info.set(f'{self.renderer.definitions[state.program]["label"]} · {state.label}')

    def _advance(self):
        self.after_id = None
        if self._closed or not self.playing:
            return
        self._draw()
        self.after_id = self.window.after(33,self._advance)

    def preview(self,motion=None):
        motions,phrases = self._choices()
        if motion in [row[0] for row in MOTIONS]:
            motions = [motion]
        self.renderer.configure(motions,phrases)
        self.elapsed = 0
        self.started_at = time.monotonic()
        self.playing = True
        self.play_button.configure(text='暂停')
        if self.after_id is not None:
            self.window.after_cancel(self.after_id)
        self._advance()

    def _toggle(self):
        if not self.playing:
            if self.started_at is None:
                self.preview()
                return
            self.started_at = time.monotonic()-self.elapsed
            self.playing = True
            self.play_button.configure(text='暂停')
            self._advance()
        else:
            self.elapsed = time.monotonic()-self.started_at
            self.playing = False
            if self.after_id is not None:
                self.window.after_cancel(self.after_id)
                self.after_id = None
            self.play_button.configure(text='继续播放')
            self.info.set('已暂停 · 可以继续播放或更换节目')

    def apply(self):
        motions,phrases = self._choices()
        self.app.performer_motions = motions
        self.app.performer_phrases = phrases
        renderer = getattr(self.app,'performer_renderer',None)
        if renderer is not None:
            renderer.configure(motions,phrases)
        self.app.performer_started_at = None
        self.app.performer_target = None
        self.app.save_config()
        self.app.detail_var.set('动作与台词已保存。')
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.playing = False
        for timer in (self.after_id,self._resize_id):
            if timer is not None:
                self.window.after_cancel(timer)
        self.after_id = self._resize_id = None
        self.image = None
        self.window.destroy()
        self.app.performer_panel = None
