"""Native, local face enrollment guidance. The ring reflects actual samples."""
import math
import tkinter as tk
from tkinter import ttk

from small_mirror_brand import PRODUCT_TITLE


def build_owner_window(app):
    s=app.ui_scale
    window=tk.Toplevel(app.root);app.owner_window=window
    window.title(PRODUCT_TITLE+' · 认识我')
    width=min(round(660*s),app.root.winfo_screenwidth()-80)
    height=min(round(650*s),app.root.winfo_screenheight()-100)
    window.geometry(f'{width}x{height}')
    window.minsize(min(width,round(460*s)),min(height,round(460*s)))
    window.configure(bg=app.BG);window.protocol('WM_DELETE_WINDOW',app.cancel_enrollment)
    panel=ttk.Frame(window,padding=round(18*s));panel.pack(fill='both',expand=True)
    panel.columnconfigure(0,weight=1);panel.rowconfigure(2,weight=1)
    steps=ttk.Frame(panel);steps.grid(row=0,column=0,sticky='ew',pady=(0,round(12*s)))
    app.owner_step_labels=[]
    for i,title in enumerate(('1  确认是你','2  跟着转头','3  验证并保存')):
        steps.columnconfigure(i,weight=1)
        label=ttk.Label(steps,text=title,anchor='center',foreground=app.MUTED,font=('Microsoft YaHei UI',10))
        label.grid(row=0,column=i,sticky='ew');app.owner_step_labels.append(label)
    copy=ttk.Frame(panel);copy.grid(row=1,column=0,sticky='ew')
    app.owner_status=tk.StringVar(value='先认识你，才能把你排除在外')
    app.owner_hint=tk.StringVar(value='准备好后点“开始录入”。请坐在平时工作的地方，脸上保持明亮。')
    title=ttk.Label(copy,textvariable=app.owner_status,anchor='center',justify='center',
        wraplength=width-round(50*s),font=('Microsoft YaHei UI',14,'bold'))
    title.pack(fill='x',pady=(0,round(7*s)))
    hint=ttk.Label(copy,textvariable=app.owner_hint,anchor='center',justify='center',
        wraplength=width-round(50*s),foreground=app.MUTED,font=('Microsoft YaHei UI',10))
    hint.pack(fill='x')
    app.owner_preview=tk.Canvas(panel,bg='#E6EAE3',height=round(290*s),highlightthickness=0)
    app.owner_preview.grid(row=2,column=0,sticky='nsew',pady=round(12*s))
    app.owner_preview.bind('<Button-1>',app.owner_point)
    footer=ttk.Frame(panel);footer.grid(row=3,column=0,sticky='ew')
    app.owner_progress=ttk.Progressbar(footer,maximum=12)
    app.owner_progress.pack(fill='x',pady=(0,round(8*s)))
    app.remember_owner=tk.BooleanVar(value=True)
    ttk.Checkbutton(footer,text='只在这台电脑上加密记住我',variable=app.remember_owner).pack(anchor='w')
    ttk.Label(footer,text='不保存录入照片。验证成功后才替换旧模板。',foreground=app.MUTED,
        font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(round(4*s),round(10*s)))
    row=ttk.Frame(footer);row.pack(fill='x')
    app.enroll_button=ttk.Button(row,text='重新录入' if app.owner_samples else '开始录入',
        style='Primary.TButton',command=app.begin_enrollment)
    app.enroll_button.pack(side='right')
    ttk.Button(row,text='取消',command=app.cancel_enrollment).pack(side='right',padx=round(6*s))
    app.retry_owner_button=ttk.Button(row,text='重试这一步',command=app.retry_enrollment,state='disabled')
    app.retry_owner_button.pack(side='left')
    # Keep destructive maintenance separate from the primary enrollment action.
    maintenance=ttk.Frame(footer);maintenance.pack(fill='x',pady=(round(8*s),0))
    app.delete_owner_button=ttk.Button(maintenance,text='删除已记住的我',style='Quiet.TButton',
        command=app.delete_owner_template,state='normal' if app.owner_samples else 'disabled')
    app.delete_owner_button.pack(side='left')
    if app.owner_samples:
        app.owner_hint.set('已记住你。重新录入时会保留旧模板，直到新模板通过验证。')
    def reflow(event):
        if event.widget is panel:
            title.configure(wraplength=max(200,event.width-round(20*s)))
            hint.configure(wraplength=max(200,event.width-round(20*s)))
    panel.bind('<Configure>',reflow,add='+')
    app.owner_camera_was_paused=app.paused


def set_step(app,phase,hint=None):
    for i,label in enumerate(getattr(app,'owner_step_labels',[])):
        label.configure(foreground=app.ACCENT if i==phase else app.MUTED,
                        font=('Microsoft YaHei UI',10,'bold' if i==phase else 'normal'))
    if hint is not None and hasattr(app,'owner_hint'):app.owner_hint.set(hint)


def draw_guide(app,packet,mapping):
    canvas=app.owner_preview;s=app.ui_scale
    ox,oy,dw,dh=mapping
    fw,fh=packet.frame.shape[1],packet.frame.shape[0]
    selected=next((face for face in packet.faces if face.get('track_id')==app.owner_candidate),None)
    if app.owner_verifying:
        selected=next((face for face in packet.faces if face.get('identity')=='owner'),selected)
    # Follow the selected head rather than implying that an arbitrary screen
    # position identifies the owner. Multiple people still require a click.
    if selected:
        x,y,w,h=selected['bbox']
        cx=ox+(fw-x-w/2)*dw/fw;cy=oy+(y+h/2)*dh/fh
        radius=max(32*s,min(w*dw/fw*.95,h*dh/fh*.85))
    else:
        cx,cy=ox+dw/2,oy+dh/2;radius=min(dw*.24,dh*.38)
    radius=max(16,min(radius,dw*.46,dh*.46))
    cx=max(ox+radius+5,min(cx,ox+dw-radius-5))
    cy=max(oy+radius+5,min(cy,oy+dh-radius-5))
    status=packet.enrollment_status or {}
    count=12 if getattr(app,'owner_verified',False) else status.get('count',0)
    if app.owner_verifying:count=12
    for i in range(12):
        canvas.create_arc(cx-radius,cy-radius,cx+radius,cy+radius,
            start=90-i*30,extent=-24,style='arc',
            outline=app.ACCENT if i<count else '#BCC7BA',width=max(2,round(4*s)))
    if app.enrolling and app.enroll_selected and not status.get('done'):
        stage=status.get('stage',0)
        direction=('看镜头','← 往左一点','往右一点 →','↓ 下巴收一点')[min(3,stage)]
        y=max(oy+16*s,min(oy+dh-16*s,cy+radius+23*s))
        canvas.create_rectangle(cx-92*s,y-16*s,cx+92*s,y+16*s,fill=app.TEXT,outline='')
        canvas.create_text(cx,y,text=direction,fill='#FFFFFF',font=('Microsoft YaHei UI',12,'bold'))
    elif app.owner_verifying and getattr(app,'owner_verified',False):
        canvas.create_text(cx,cy-radius-12*s,text='✓ 已认出你',fill=app.ACCENT,
            font=('Microsoft YaHei UI',11,'bold'))
