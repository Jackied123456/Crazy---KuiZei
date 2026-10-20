"""Camera-first desktop console, wired to the existing local app callbacks."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk
from small_mirror_brand import PRODUCT_CN, PRODUCT_EN, VERSION_LABEL, USE_NOTICE, SLOGAN, logo_image


def draw_preview_state(app, title, note=''):
    """One tagged state layer, with spacing measured from real wrapped text."""
    canvas=app.preview
    canvas.delete('preview-state')
    width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
    s=app.ui_scale;x=width/2;tag='preview-state'
    wrap=max(40,width-24*s)
    heading=canvas.create_text(x,0,text=title,fill=app.TEXT,anchor='n',justify='center',
        font=('Microsoft YaHei UI',11),width=wrap,tags=tag)
    box=canvas.bbox(heading);title_h=box[3]-box[1]
    description=None;note_h=0
    if note and height>100*s:
        description=canvas.create_text(x,0,text=note,fill=app.MUTED,anchor='n',justify='center',
            font=('Microsoft YaHei UI',9),width=wrap,tags=tag)
        box=canvas.bbox(description);note_h=box[3]-box[1]+10*s
        if title_h+note_h+24*s>height:
            canvas.delete(description);description=None;note_h=0
    mark_h=70*s if height>title_h+note_h+100*s else 0
    top=max(8*s,(height-title_h-note_h-mark_h)/2)
    if mark_h:
        cy=top+22*s;r=22*s;arm=9*s
        for dx,dy in ((-1,-1),(1,-1),(-1,1),(1,1)):
            a,b=x+dx*r,cy+dy*r
            canvas.create_line(a-dx*arm,b,a,b,a,b-dy*arm,fill=app.MUTED,width=max(1,round(2*s)),tags=tag)
        canvas.create_oval(x-4*s,cy-4*s,x+4*s,cy+4*s,fill=app.ACCENT,outline='',tags=tag)
    canvas.coords(heading,x,top+mark_h)
    if description:canvas.coords(description,x,top+mark_h+title_h+10*s)


def draw_preview_placeholder(app):
    """A hidden preview never retains camera pixels or an earlier status."""
    if app.preview_enabled.get():
        app._preview_render_key=None
        return
    canvas=app.preview;canvas.delete('all')
    app.preview_image=app.preview_map=None;app._preview_render_key=None
    if getattr(app,'camera_health','') in ('stalled','error'):
        draw_preview_state(app,'相机画面已中断','点“重试相机”重新连接；已有内容遮挡会保留。')
    elif app.paused:
        draw_preview_state(app,'开始后查看后方画面','点击“开始保护”，相机会自动打开。')
    else:
        draw_preview_state(app,'画面已收起','收起不会暂停监测；点“展开画面”查看后方。')


def build_home(app):
    root, s = app.root, app.ui_scale
    outer=ttk.Frame(root,padding=(round(26*s),round(14*s)))
    outer.pack(fill='both',expand=True)
    outer.columnconfigure(0,weight=1);outer.rowconfigure(3,weight=1)
    heading=ttk.Frame(outer);heading.grid(row=0,column=0,sticky='ew',pady=(0,round(10*s)))
    ttk.Label(heading,text=PRODUCT_CN,font=('Microsoft YaHei UI',20,'bold')).pack(side='left')
    ttk.Label(heading,text=PRODUCT_EN.upper()+' / '+VERSION_LABEL,style='Beta.TLabel').pack(side='left',padx=round(12*s),pady=(round(5*s),0))
    ttk.Button(heading,text='后台运行',style='Quiet.TButton',command=app.hide_settings).pack(side='right')
    ttk.Button(heading,text='设置',style='Quiet.TButton',command=app.open_preferences).pack(side='right',padx=round(5*s))
    app.home_anchor=ttk.Separator(outer);app.home_anchor.grid(row=1,column=0,sticky='ew')

    app.home_panel=ttk.Frame(outer);app.home_panel.grid(row=2,column=0,sticky='ew',pady=(round(10*s),round(10*s)))
    copy=ttk.Frame(app.home_panel);copy.pack(side='left',fill='x',expand=True)
    ttk.Label(copy,text=SLOGAN,font=('Microsoft YaHei UI',24,'bold')).pack(anchor='w')
    subtitle=ttk.Label(copy,text='你盯工作，我盯后排。',foreground=app.MUTED)
    subtitle.pack(anchor='w',pady=(round(6*s),0))
    ttk.Label(app.home_panel,textvariable=app.status_var,style='Status.TLabel',wraplength=round(200*s),justify='right').pack(side='right',padx=(round(12*s),0))

    app.home_cards=ttk.Frame(outer);app.home_cards.grid(row=3,column=0,sticky='nsew')
    app.home_cards.columnconfigure(0,weight=3)
    app.home_cards.columnconfigure(1,weight=2,minsize=round(230*s))
    app.home_cards.rowconfigure(0,weight=1)
    camera=ttk.Frame(app.home_cards,style='Card.TFrame',padding=round(14*s))
    camera.grid(row=0,column=0,sticky='nsew',padx=(0,round(16*s)))
    title=ttk.Frame(camera,style='Paper.TFrame');title.pack(fill='x',pady=(0,round(9*s)))
    ttk.Label(title,text='后方画面',style='Paper.TLabel',font=('Microsoft YaHei UI',10,'bold')).pack(side='left')
    app.preview_toggle=ttk.Button(title,text='展开画面',style='Compact.TButton',command=app.switch_preview,state='disabled')
    app.edit_bar=ttk.Frame(camera,style='Paper.TFrame');app.edit_hint=tk.StringVar(value='')
    edit_note=ttk.Label(app.edit_bar,textvariable=app.edit_hint,style='Paper.TLabel',foreground=app.MUTED,wraplength=round(330*s))
    edit_note.pack(anchor='w')
    app.edit_bar.bind('<Configure>',lambda event:edit_note.configure(wraplength=max(80,event.width-round(8*s))))
    edit_buttons=ttk.Frame(app.edit_bar,style='Paper.TFrame');edit_buttons.pack(fill='x',pady=(round(4*s),round(6*s)))
    for label,callback in (('完成',app.finish_selection),('新增框',app.new_roi),('撤销',app.undo_selected),('清空',app.clear_selected)):
        ttk.Button(edit_buttons,text=label,style='Compact.TButton',command=callback).pack(side='left',padx=(0,round(5*s)))
    app.preview=tk.Canvas(camera,height=round(210*s),bg=app.BG,highlightthickness=0)
    app.preview.bind('<ButtonPress-1>',app.roi_start)
    app.preview.bind('<B1-Motion>',app.roi_drag)
    app.preview.bind('<ButtonRelease-1>',app.roi_finish)
    app.preview.bind('<Motion>',app.roi_cursor)
    app.preview.bind('<Configure>',lambda _e:draw_preview_placeholder(app))
    app.preview_toolbar=ttk.Frame(camera,style='Paper.TFrame');app.preview_toolbar.pack(side='bottom',fill='x',pady=(round(9*s),0))
    ttk.Button(app.preview_toolbar,text='设置检测区',style='Paper.TButton',command=lambda:app.choose_selection('detect')).pack(side='left')
    ttk.Button(app.preview_toolbar,text='相机设置',style='Compact.TButton',command=lambda:app.open_preferences('相机')).pack(side='right')
    app.camera_retry_button=ttk.Button(app.preview_toolbar,text='重试相机',style='Compact.TButton',command=app.retry_camera)
    app.preview.pack(fill='both',expand=True)

    checklist=ttk.Frame(app.home_cards,style='Card.TFrame',padding=round(12*s))
    checklist.grid(row=0,column=1,sticky='nsew')
    def section(title,variable,note):
        frame=ttk.Frame(checklist,style='Paper.TFrame');frame.pack(fill='x')
        header=ttk.Frame(frame,style='Paper.TFrame');header.pack(fill='x')
        ttk.Label(header,text=title,style='Paper.TLabel',font=('Microsoft YaHei UI',11,'bold')).pack(side='left')
        description=ttk.Frame(frame,style='Paper.TFrame');description.pack(fill='x',pady=(round(2*s),round(5*s)))
        label=ttk.Label(description,textvariable=variable,style='Paper.TLabel',foreground=app.MUTED,wraplength=round(240*s))
        label.pack(side='left',fill='x',expand=True)
        if note:
            ttk.Label(frame,text=note,style='Paper.TLabel',foreground=app.MUTED,font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(0,round(7*s)))
        return frame,label,header,description
    owner,owner_label,owner_header,_owner_description=section('认识我',app.owner_summary,None)
    ttk.Button(owner_header,text='录入本人',style='Compact.TButton',command=app.open_owner_settings).pack(side='right')
    ttk.Separator(checklist).pack(fill='x',pady=round(5*s))
    protection,protection_label,protection_header,protection_description=section('保护窗口',app.region_var,None)
    ttk.Button(protection_header,text='选择窗口',style='Compact.TButton',command=app.open_window_picker).pack(side='right')
    actions=ttk.Frame(protection,style='Paper.TFrame');actions.pack(fill='x',pady=(0,round(4*s)))
    app.test_mask_button=ttk.Button(actions,text='测试遮挡',style='Compact.TButton',command=app.test_protection)
    app.test_mask_button.pack(side='left')
    app.manual_mask_button=ttk.Button(actions,text='立即遮挡',style='Compact.TButton',command=app.toggle_mask)
    app.manual_mask_button.pack(side='left',padx=(round(6*s),0))
    ttk.Checkbutton(protection,text='发现围观时自动遮挡',variable=app.sensitive,command=app.sensitive_changed).pack(anchor='w')
    protection_label.pack_forget();protection_label.pack(side='left',fill='x',expand=True)
    ttk.Separator(checklist).pack(fill='x',pady=round(5*s))
    actor,actor_label,actor_header,_actor_description=section('回敬动画',app.media_status,None)
    ttk.Button(actor_header,text='药水哥设置',style='Compact.TButton',command=app.open_performer_panel).pack(side='right')

    control=ttk.Frame(outer);control.grid(row=4,column=0,sticky='ew',pady=(round(12*s),round(5*s)))
    summary=ttk.Label(control,textvariable=app.protection_summary,foreground=app.MUTED,wraplength=round(620*s))
    summary.pack(side='left',fill='x',expand=True,padx=(0,round(15*s)))
    app.camera_button=ttk.Button(control,text='开始保护',style='Primary.TButton',command=app.start_protection)
    app.camera_button.pack(side='right')
    app.camera_cancel_button=ttk.Button(control,text='取消启动',style='Compact.TButton',command=lambda:app.toggle_pause(keep_cover=True))
    lower=ttk.Frame(outer);lower.grid(row=5,column=0,sticky='ew',pady=(0,round(5*s)))
    ttk.Checkbutton(lower,text='自动录像（原始特写）',variable=app.record_enabled,command=app.recording_changed).pack(side='left')
    ttk.Button(lower,text='统计记录',style='Quiet.TButton',command=app.open_stats).pack(side='right')
    ttk.Button(lower,text='录像记录',style='Quiet.TButton',command=app.open_recordings).pack(side='right',padx=round(5*s))
    app.footer=ttk.Label(outer,textvariable=app.detail_var,foreground=app.MUTED,wraplength=round(850*s),font=('Microsoft YaHei UI',9))
    app.footer.grid(row=6,column=0,sticky='ew',pady=(0,round(7*s)))
    ttk.Separator(outer).grid(row=7,column=0,sticky='ew')
    credit=ttk.Frame(outer);credit.grid(row=8,column=0,sticky='ew',pady=(round(7*s),0))
    ttk.Label(credit,text=USE_NOTICE,foreground=app.MUTED,font=('Microsoft YaHei UI',8)).pack(side='left')
    ttk.Label(credit,text='本机处理 · 默认静音',foreground=app.MUTED,font=('Microsoft YaHei UI',8)).pack(side='right')

    app.icon_image=ImageTk.PhotoImage(logo_image(),master=root);root.iconphoto(True,app.icon_image)
    def reflow(event):
        if event.widget is outer:
            width=max(200,event.width-round(52*s))
            app.footer.configure(wraplength=width)
            summary.configure(wraplength=max(160,width-round(145*s)))
            # Give the real actions room on a short laptop window.
            if event.height<round(650*s):subtitle.pack_forget()
            elif not subtitle.winfo_manager():subtitle.pack(anchor='w',pady=(round(6*s),0))
        elif event.widget is checklist:
            for label in (owner_label,protection_label,actor_label):
                reserved=0
                label.configure(wraplength=max(60,event.width-round(24*s)-reserved))
    outer.bind('<Configure>',reflow,add='+')
    checklist.bind('<Configure>',reflow,add='+')
    root.after_idle(lambda:draw_preview_placeholder(app))
