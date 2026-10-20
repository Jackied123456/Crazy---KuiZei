"""Small, local Tk panels for window selection and recorded close-ups."""
from pathlib import Path
import os
import tkinter as tk
from tkinter import ttk,messagebox
import small_mirror_core as core
from small_mirror_recording import recordings,safe_clip
from small_mirror_brand import PRODUCT_TITLE

class CoverSnooze:
    """Clickable, nonmodal notice; the display frame itself stays click-through."""
    def __init__(self,app):
        self.app=app
        self.window=tk.Toplevel(app.root);self.window.withdraw()
        self.window.title('自动遮挡暂缓');self.window.resizable(False,False)
        self.window.configure(bg=app.BG)
        self.window.protocol('WM_DELETE_WINDOW',self.dismiss)
        box=ttk.Frame(self.window,padding=12);box.pack(fill='both',expand=True)
        self.text=tk.StringVar()
        ttk.Label(box,textvariable=self.text).pack(anchor='w',pady=(0,8))
        row=ttk.Frame(box);row.pack(fill='x')
        self.duration=tk.StringVar(value='30 秒')
        picker=ttk.Combobox(row,values=('30 秒','1 分钟','5 分钟'),textvariable=self.duration,
                            state='readonly',width=8)
        picker.pack(side='left',padx=(0,8))
        picker.bind('<<ComboboxSelected>>',lambda _e:app.snooze_protection(
            {'30 秒':30,'1 分钟':60,'5 分钟':300}[self.duration.get()]))
        ttk.Button(row,text='立即恢复自动遮挡',command=app.resume_protection).pack(side='left')
        ttk.Button(row,text='收起',command=self.dismiss).pack(side='left',padx=(8,0))
        self.window.update_idletasks()
        x=max(0,app.root.winfo_screenwidth()-self.window.winfo_reqwidth()-28)
        y=max(0,app.root.winfo_screenheight()-self.window.winfo_reqheight()-80)
        self.window.geometry(f'+{x}+{y}')
        from small_mirror_shell import configure_floating_window
        configure_floating_window(self.window,click_through=False,no_activate=True)
        self.window.deiconify()
        configure_floating_window(self.window,click_through=False,no_activate=True)

    def update(self,seconds):
        self.text.set(f'内容已恢复 · {seconds} 秒后恢复自动遮挡')

    def dismiss(self):
        self.window.destroy();self.app.cover_notice=None


class WindowPicker:
    def __init__(self,app,api):
        self.app,self.api=app,api
        self.window=tk.Toplevel(app.root);self.window.title('选择要保护的窗口')
        self.window.geometry(f'{round(760*app.ui_scale)}x{min(round(530*app.ui_scale),app.root.winfo_screenheight()-100)}')
        self.window.configure(bg=app.BG);self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.thumbnails=[];self.cards=[];self.page=0;self.tokens=[];self.selected={}
        for token in app.protected_windows:self.selected[(token.hwnd,token.pid,token.epoch)]=token
        outer=ttk.Frame(self.window,padding=16);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='选择自动遮挡的窗口，可以选多个。',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w',pady=(0,8))
        self.hint=tk.StringVar();ttk.Label(outer,textvariable=self.hint).pack(anchor='w',pady=(0,10))
        selection=ttk.Frame(outer);selection.pack(fill='x',pady=(0,6))
        ttk.Button(selection,text='全选全部窗口',command=self.select_all).pack(side='left')
        ttk.Button(selection,text='取消全选',command=self.clear_selection).pack(side='left',padx=8)
        self.grid=ttk.Frame(outer);self.grid.pack(fill='both',expand=True)
        for col in range(3):self.grid.columnconfigure(col,weight=1,uniform='cards')
        for row in range(2):self.grid.rowconfigure(row,weight=1,uniform='cards')
        row=ttk.Frame(outer);row.pack(fill='x',pady=(12,0))
        ttk.Button(row,text='刷新',command=self.refresh).pack(side='left')
        ttk.Button(row,text='上一页',command=lambda:self.turn(-1)).pack(side='left',padx=6)
        ttk.Button(row,text='下一页',command=lambda:self.turn(1)).pack(side='left')
        ttk.Button(row,text='启用所选窗口保护',command=self.apply).pack(side='right')
        self.refresh()
        self.window.bind('<Configure>',lambda _e:self.position())

    def refresh(self):
        self.tokens=self.api.list_windows();self.page=0
        self.selected={key:token for key,token in self.selected.items() if self.api.valid(token)}
        self.render()

    def turn(self,delta):
        self.page=max(0,min(max(0,(len(self.tokens)-1)//6),self.page+delta));self.render()

    def select_all(self):
        self.selected={(token.hwnd,token.pid,token.epoch):token for token in self.tokens if self.api.valid(token)}
        self.render()

    def clear_selection(self):
        self.selected={};self.render()

    def release_thumbnails(self):
        for thumb,_widget in self.thumbnails:self.api.close_thumbnail(thumb)
        self.thumbnails=[]

    def render(self):
        self.release_thumbnails()
        for child in self.grid.winfo_children():child.destroy()
        self.cards=[]
        self.hint.set(f'已选 {len(self.selected)} 个 · 第 {self.page+1}/{max(1,(len(self.tokens)+5)//6)} 页'
                      if self.tokens else '没有找到可选窗口。先打开浏览器或文档，再点刷新。')
        for index,token in enumerate(self.tokens[self.page*6:self.page*6+6]):
            card=ttk.Frame(self.grid,padding=8);card.grid(row=index//3,column=index%3,sticky='nsew',padx=5,pady=5)
            key=(token.hwnd,token.pid,token.epoch)
            value=tk.BooleanVar(value=key in self.selected)
            def toggle(token=token,key=key,value=value):
                if value.get():self.selected[key]=token
                else:self.selected.pop(key,None)
                self.hint.set(f'已选 {len(self.selected)} 个 · 选好后点“启用所选窗口保护”')
            preview=tk.Canvas(card,bg='#080d12',height=round(125*self.app.ui_scale),highlightthickness=0)
            preview.pack(fill='both',expand=True)
            preview.create_text(round(70*self.app.ui_scale),round(45*self.app.ui_scale),text='窗口预览',fill=self.app.MUTED)
            preview.bind('<Button-1>',lambda _e,value=value,toggle=toggle:(value.set(not value.get()),toggle()))
            check=ttk.Checkbutton(card,text=token.title[:32],variable=value,command=toggle)
            check.pack(anchor='w',pady=(6,0))
            self.cards.append((token,preview))
        self.window.update_idletasks()
        for token,preview in self.cards:
            thumb=self.api.thumbnail(self.window,token)
            if thumb:self.thumbnails.append((thumb,preview))
        self.position()

    def position(self):
        for thumb,widget in self.thumbnails:
            if widget.winfo_exists():self.api.place_thumbnail(thumb,self.window,widget)

    def apply(self):
        self.app.set_protected_windows([token for token in self.selected.values() if self.api.valid(token)])
        self.close()

    def close(self):
        self.release_thumbnails();self.window.destroy();self.app.window_picker=None


class RecordingsPanel:
    def __init__(self,app):
        self.app=app;self.directory=app.data_dir/'recordings'
        self.window=tk.Toplevel(app.root);self.window.title(PRODUCT_TITLE+' · 录像')
        self.window.geometry(f'{round(720*app.ui_scale)}x{min(round(500*app.ui_scale),app.root.winfo_screenheight()-100)}')
        self.window.configure(bg=app.BG);self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.capture=None;self.after_id=None;self.playing=False;self.image=None;self.paths={};self.current=None
        outer=ttk.Frame(self.window,padding=18);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='只保存观察者原始特写 · 本机文件 · 无声音',foreground=app.MUTED).pack(anchor='w',pady=(0,12))
        content=ttk.Frame(outer);content.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(content,columns=('size',),show='tree headings',height=8)
        self.tree.heading('#0',text='录制时间');self.tree.heading('size',text='大小')
        self.tree.column('#0',width=round(200*app.ui_scale));self.tree.column('size',width=round(70*app.ui_scale))
        self.tree.pack(side='left',fill='both',expand=True,padx=(0,12))
        self.canvas=tk.Canvas(content,bg='#080d12',width=round(320*app.ui_scale),highlightthickness=0)
        self.canvas.pack(side='right',fill='both',expand=True)
        self.info=tk.StringVar(value='点一段录像看看。')
        ttk.Label(outer,textvariable=self.info).pack(anchor='w',pady=10)
        row=ttk.Frame(outer);row.pack(fill='x')
        for title,callback in (('播放／暂停',self.play),('删除此段',self.delete),('打开文件夹',self.folder),('刷新',self.refresh)):
            ttk.Button(row,text=title,command=callback).pack(side='left',padx=(0,8))
        self.tree.bind('<<TreeviewSelect>>',self.select);self.refresh()

    def refresh(self):
        self.stop_media();self.current=None
        self.tree.delete(*self.tree.get_children());self.paths={}
        for index,path in enumerate(recordings(self.directory)):
            try:size=path.stat().st_size
            except OSError:continue
            key=str(index);self.paths[key]=path
            self.tree.insert('', 'end',iid=key,text=path.name[:15],values=(f'{size/1024/1024:.1f} MB',))
        self.info.set(f'共 {len(self.paths)} 段。正在录制的片段会在结束后出现。')

    def select(self,_event=None):
        chosen=self.tree.selection()
        if not chosen or chosen[0] not in self.paths:return
        self.stop_media();self.current=None
        try:self.current=safe_clip(self.paths[chosen[0]],self.directory)
        except (OSError,ValueError):self.info.set('这段录像找不到了，点刷新再看看。');return
        self.capture=core.cv2.VideoCapture(str(self.current))
        if not self.capture.isOpened():
            self.stop_media();self.info.set('这段录像暂时打不开，可以在文件夹中查看。');return
        fps=max(1,self.capture.get(core.cv2.CAP_PROP_FPS));frames=self.capture.get(core.cv2.CAP_PROP_FRAME_COUNT)
        self.info.set(f'{self.current.name} · 约 {frames/fps:.1f} 秒')
        self.read_frame()

    def read_frame(self):
        if self.capture is None:return False
        ok,frame=self.capture.read()
        if not ok:return False
        w,h=max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())
        picture=core.Image.fromarray(core.cv2.cvtColor(frame,core.cv2.COLOR_BGR2RGB))
        picture=picture.resize(core.fit_size(picture.width,picture.height,w,h),core.Image.Resampling.BILINEAR)
        self.image=core.ImageTk.PhotoImage(picture)
        self.canvas.delete('all');self.canvas.create_image(w/2,h/2,image=self.image)
        return True

    def play(self):
        if self.capture is None:return
        self.playing=not self.playing
        if self.after_id:
            self.window.after_cancel(self.after_id);self.after_id=None
        if self.playing:
            if self.capture.get(core.cv2.CAP_PROP_POS_FRAMES)>=self.capture.get(core.cv2.CAP_PROP_FRAME_COUNT):
                self.capture.set(core.cv2.CAP_PROP_POS_FRAMES,0)
            self.advance()

    def advance(self):
        self.after_id=None
        if not self.playing:return
        if not self.read_frame():self.playing=False;return
        fps=max(1,self.capture.get(core.cv2.CAP_PROP_FPS))
        self.after_id=self.window.after(round(1000/fps),self.advance)

    def stop_media(self):
        self.playing=False
        if self.after_id:self.window.after_cancel(self.after_id);self.after_id=None
        if self.capture:self.capture.release();self.capture=None
        self.canvas.delete('all');self.image=None

    def delete(self):
        if self.current is None:return
        if not messagebox.askyesno('删除录像','删除这段本机录像？',parent=self.window):return
        self.stop_media()
        try:path=safe_clip(self.current,self.directory);path.unlink();self.refresh()
        except (OSError,ValueError) as exc:self.info.set('没能删除：'+str(exc))

    def folder(self):
        self.directory.mkdir(parents=True,exist_ok=True);os.startfile(str(self.directory.resolve()))

    def close(self):
        self.stop_media();self.window.destroy();self.app.recordings_panel=None
