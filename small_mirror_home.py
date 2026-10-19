"""Calm home screen wired to real protection, identity and recording callbacks."""
import tkinter as tk
from tkinter import ttk
from PIL import Image,ImageDraw,ImageTk
from small_mirror_brand import PRODUCT_CN,PRODUCT_EN,VERSION_LABEL,USE_NOTICE,SLOGAN

def build_home(app):
    root=app.root;s=app.ui_scale
    outer=ttk.Frame(root,padding=(round(25*s),round(20*s)));outer.pack(fill='both',expand=True)
    heading=ttk.Frame(outer);heading.pack(fill='x',pady=(0,15))
    ttk.Label(heading,text=PRODUCT_CN,font=('Microsoft YaHei UI',20,'bold')).pack(side='left')
    ttk.Label(heading,text=PRODUCT_EN+'  /  '+VERSION_LABEL,foreground=app.MUTED,font=('Segoe UI',9)).pack(side='left',padx=12,pady=(6,0))
    ttk.Button(heading,text='收起',style='Quiet.TButton',command=app.hide_settings).pack(side='right')
    ttk.Button(heading,text='设置',style='Quiet.TButton',command=app.open_preferences).pack(side='right',padx=6)
    app.home_anchor=ttk.Separator(outer);app.home_anchor.pack(fill='x')
    app.home_panel=ttk.Frame(outer);app.home_panel.pack(fill='x',pady=(18,12))
    copy=ttk.Frame(app.home_panel);copy.pack(side='left',fill='both',expand=True)
    ttk.Label(copy,text=SLOGAN.replace('，','，\n',1),font=('Microsoft YaHei UI',24,'bold')).pack(anchor='w')
    ttk.Label(copy,text='专心工作，后排交给我。',foreground=app.MUTED,font=('Microsoft YaHei UI',10)).pack(anchor='w',pady=(9,0))
    w,h=round(160*s),round(106*s);badge=Image.new('RGB',(w,h),app.BG);d=ImageDraw.Draw(badge)
    d.rounded_rectangle((1,1,w-2,h-2),round(16*s),fill=app.PANEL,outline='#DFE4DE')
    for x,y,dx,dy in ((25,23,1,1),(135,23,-1,1),(25,63,1,-1),(135,63,-1,-1)):
        a,b=round(x*s),round(y*s)
        d.line((a,b,a+round(14*s)*dx,b),fill='#7E8F80',width=max(1,round(2*s)))
        d.line((a,b,a,b+round(12*s)*dy),fill='#7E8F80',width=max(1,round(2*s)))
    d.ellipse((round(74*s),round(37*s),round(86*s),round(49*s)),fill=app.ACCENT)
    app.hero_image=ImageTk.PhotoImage(badge,master=root)
    right=ttk.Frame(app.home_panel);right.pack(side='right',padx=(18,0))
    ttk.Label(right,image=app.hero_image).pack()
    ttk.Label(right,textvariable=app.status_var,style='Status.TLabel',font=('Microsoft YaHei UI',9)).pack(pady=(4,0))
    icon=Image.new('RGBA',(128,128),(0,0,0,0));mark=ImageDraw.Draw(icon)
    mark.rounded_rectangle((0,0,127,127),28,fill=app.ACCENT)
    mark.ellipse((22,38,106,90),fill='white')
    mark.ellipse((48,48,80,80),fill=app.ACCENT)
    app.icon_image=ImageTk.PhotoImage(icon,master=root)
    root.iconphoto(True,app.icon_image)
    actions=ttk.Frame(outer);actions.pack(fill='x',pady=(0,14))
    app.camera_button=ttk.Button(actions,text='开始保护',style='Primary.TButton',command=app.toggle_pause);app.camera_button.pack(side='left')
    ttk.Label(actions,textvariable=app.protection_summary,foreground=app.MUTED,wraplength=round(440*s)).pack(side='left',padx=15)
    tools=ttk.Frame(outer);tools.pack(fill='x',pady=(0,15))
    ttk.Button(tools,text='认识我',command=app.open_owner_settings).pack(side='left')
    ttk.Label(tools,textvariable=app.owner_summary,foreground=app.MUTED).pack(side='left',padx=12)
    ttk.Checkbutton(tools,text='相机预览',variable=app.preview_enabled,command=app.toggle_preview).pack(side='right')
    app.edit_bar=ttk.Frame(outer);app.edit_hint=tk.StringVar(value='')
    ttk.Label(app.edit_bar,textvariable=app.edit_hint,foreground=app.MUTED).pack(side='left')
    for text,callback in (('完成',app.finish_selection),('清空此类',app.clear_selected),('撤销上一个',app.undo_selected)):
        ttk.Button(app.edit_bar,text=text,command=callback).pack(side='right',padx=(6,0))
    app.preview=tk.Canvas(outer,height=round(235*s),bg='#E6EAE3',highlightthickness=0)
    app.preview.bind('<ButtonPress-1>',app.roi_start);app.preview.bind('<B1-Motion>',app.roi_drag);app.preview.bind('<ButtonRelease-1>',app.roi_finish)
    app.preview_toolbar=ttk.Frame(outer)
    ttk.Button(app.preview_toolbar,text='设置检测区',command=lambda:app.choose_selection('detect')).pack(side='left')
    ttk.Label(app.preview_toolbar,text='框内检测；录入本人后自动排除你。',foreground=app.MUTED).pack(side='left',padx=12)
    app.home_cards=ttk.Frame(outer);app.home_cards.pack(fill='both',expand=True,pady=(0,12))
    for col in (0,1):app.home_cards.columnconfigure(col,weight=1,uniform='home')
    for col,title,note in ((0,'保护窗口','选好内容，再开始保护'),(1,'事件录像','可选 · 默认关闭')):
        border=tk.Canvas(app.home_cards,bg=app.BG,highlightthickness=0,width=round(290*s))
        border.grid(row=0,column=col,sticky='nsew',padx=(0,7) if col==0 else (7,0))
        panel=ttk.Frame(border,style='Paper.TFrame');inset=round(15*s);content=border.create_window(inset,inset,window=panel,anchor='nw')
        def resize(event,canvas=border,content=content,inset=inset):
            width,height=event.width,event.height
            if getattr(canvas,'surface_size',None)==(width,height):return
            canvas.surface_size=(width,height)
            q=2;image=Image.new('RGB',(max(1,width*q),max(1,height*q)),app.BG)
            draw=ImageDraw.Draw(image)
            draw.rounded_rectangle((q,q,(width-1)*q,(height-1)*q),round(16*s*q),fill=app.PANEL,outline='#DFE4DE',width=q)
            canvas.surface_image=ImageTk.PhotoImage(image.resize((width,height),Image.Resampling.LANCZOS),master=root)
            if not hasattr(canvas,'surface_item'):
                canvas.surface_item=canvas.create_image(0,0,anchor='nw',tags='surface')
            canvas.itemconfigure(canvas.surface_item,image=canvas.surface_image)
            canvas.tag_lower('surface');canvas.itemconfigure(content,width=max(1,width-2*inset))
        border.bind('<Configure>',resize)
        ttk.Label(panel,text=title,style='Paper.TLabel',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w')
        ttk.Label(panel,text=note,style='Paper.TLabel',foreground=app.MUTED,font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(4,0))
        ttk.Label(panel,textvariable=app.region_var if col==0 else app.record_summary,style='Paper.TLabel',foreground=app.MUTED,wraplength=round(260*s)).pack(anchor='w',pady=(12,14))
        row=ttk.Frame(panel,style='Paper.TFrame');row.pack(fill='x')
        if col==0:
            ttk.Button(row,text='选择窗口',style='Paper.TButton',command=app.open_window_picker).pack(side='left',padx=(0,7))
            app.test_mask_button=ttk.Button(row,text='试遮挡',style='Paper.TButton',command=app.test_protection);app.test_mask_button.pack(side='left')
        else:
            ttk.Checkbutton(row,text='保存原始特写',style='Paper.TCheckbutton',variable=app.record_enabled,command=app.recording_changed).pack(side='left')
            ttk.Button(row,text='查看',style='Paper.TButton',command=app.open_recordings).pack(side='right')
        panel.update_idletasks();border.configure(height=panel.winfo_reqheight()+2*inset)
    app.footer=ttk.Label(outer,textvariable=app.detail_var,foreground=app.MUTED,wraplength=round(700*s),font=('Microsoft YaHei UI',9));app.footer.pack(fill='x',pady=(0,10))
    ttk.Separator(outer).pack(fill='x');credit=ttk.Frame(outer);credit.pack(fill='x',pady=(10,0))
    ttk.Label(credit,text=USE_NOTICE,foreground=app.MUTED,font=('Microsoft YaHei UI',8)).pack(side='left')
    def reflow(event):
        if event.widget is not outer:return
        app.footer.configure(wraplength=max(220, event.width-round(50*s)))
    outer.bind('<Configure>',reflow,add='+')
