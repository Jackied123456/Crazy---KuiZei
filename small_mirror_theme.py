"""Quiet desktop surfaces with one restrained green accent."""
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk

def configure_theme(app):
    style=ttk.Style(app.root);style.theme_use('clam')
    bg,panel,ink,muted,accent=app.BG,app.PANEL,app.TEXT,app.MUTED,app.ACCENT
    line='#DFE4DE';app.button_images=[]
    style.configure('.',font=('Microsoft YaHei UI',10),background=bg,foreground=ink)
    style.configure('TFrame',background=bg);style.configure('TLabel',background=bg,foreground=ink)
    for name in ('Paper','Card'):
        style.configure(name+'.TFrame',background=panel)
        style.configure(name+'.TLabel',background=panel,foreground=ink)
    for name,colors,text,surface in (
        ('TButton',('#FFFFFF','#F0F2EE','#E8ECE6','#F1F2EF','#FFFFFF'),ink,bg),
        ('Paper.TButton',('#FFFFFF','#F0F2EE','#E8ECE6','#F1F2EF','#FFFFFF'),ink,panel),
        ('Primary.TButton',(accent,'#49654F','#3E5845','#D7DED7',accent),'#FFFFFF',bg),
        ('Quiet.TButton',(bg,'#E9EDE6','#DFE5DC',bg,bg),ink,bg)):
        images=[];edge=round(40*app.ui_scale);radius=round(10*app.ui_scale)
        # Supersample the nine-slice surface; corners remain smooth at 125/150% DPI.
        for index,color in enumerate(colors):
            q=3
            im=Image.new('RGB',(edge*q,edge*q),surface);d=ImageDraw.Draw(im)
            d.rounded_rectangle((q,2*q,(edge-2)*q,(edge-1)*q),radius*q,fill='#E2E6DF')
            outline=accent if index==4 else line if name in ('TButton','Paper.TButton') else color
            d.rounded_rectangle((q,q,(edge-2)*q,(edge-2)*q),radius*q,fill=color,outline=outline,width=q)
            im=im.resize((edge,edge),Image.Resampling.LANCZOS)
            images.append(ImageTk.PhotoImage(im,master=app.root))
        app.button_images.extend(images);element='Crazy.'+name
        style.element_create(element,'image',images[0],('disabled',images[3]),('pressed',images[2]),
                             ('active',images[1]),('focus',images[4]),border=radius+2,sticky='nsew')
        style.layout(name,[(element,{'sticky':'nsew','children':[
            ('Button.focus',{'sticky':'nsew','children':[
                ('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})]})])
        style.configure(name,font=('Microsoft YaHei UI',10),padding=(14,10),foreground=text)
        style.map(name,foreground=[('disabled','#67736A')])
    style.configure('TSeparator',background=line)
    style.configure('TLabelframe',background=bg,bordercolor=line)
    style.configure('TLabelframe.Label',background=bg,foreground=ink)
    style.configure('TNotebook',background=bg,borderwidth=0,tabmargins=(0,0,0,8))
    style.configure('TNotebook.Tab',padding=(16,10),background=bg,foreground=muted,borderwidth=0)
    style.map('TNotebook.Tab',background=[('selected',panel)],foreground=[('selected',ink)])
    style.configure('TCombobox',fieldbackground=panel,background=panel,foreground=ink,
                    arrowcolor=muted,bordercolor=line,lightcolor=line,darkcolor=line,padding=7)
    style.map('TCombobox',fieldbackground=[('readonly',panel)],selectbackground=[('readonly',panel)],selectforeground=[('readonly',ink)])
    style.configure('TEntry',fieldbackground=panel,bordercolor=line,padding=7)
    style.configure('Treeview',background=panel,fieldbackground=panel,foreground=ink,rowheight=round(30*app.ui_scale),bordercolor=line)
    style.configure('Treeview.Heading',background=bg,foreground=muted,padding=(8,9))
    style.map('Treeview',background=[('selected','#E6ECE3')],foreground=[('selected',ink)])
    style.configure('TCheckbutton',background=bg,foreground=ink,indicatorbackground=panel)
    style.map('TCheckbutton',background=[('active',bg)],indicatorbackground=[('selected',accent)])
    style.configure('Paper.TCheckbutton',background=panel,foreground=ink)
    style.map('Paper.TCheckbutton',background=[('active',panel)])
    style.configure('TRadiobutton',background=bg,foreground=ink)
    style.configure('Beta.TLabel',background=bg,foreground=muted,font=('Segoe UI',9))
    style.configure('Status.TLabel',background=bg,foreground=accent,padding=(8,4))
    style.configure('Eyebrow.TLabel',background=bg,foreground=muted,font=('Segoe UI',9))
    # The stock clam indicator remains tiny on some Windows DPI settings.
    # Native ttk state handling is retained, with a scalable drawn indicator.
    for name,surface in (('TCheckbutton',bg),('Paper.TCheckbutton',panel)):
        states=[];edge=round(20*app.ui_scale);q=3
        for selected,focused,disabled in ((False,False,False),(True,False,False),
                (False,True,False),(True,True,False),(False,False,True),(True,False,True)):
            im=Image.new('RGB',(edge*q,edge*q),surface);draw=ImageDraw.Draw(im)
            pad=round(3*app.ui_scale*q);end=edge*q-pad
            color='#8B978D' if disabled else accent
            if focused:
                draw.rounded_rectangle((q,q,edge*q-2*q,edge*q-2*q),round(5*app.ui_scale*q),outline=color,width=q)
            draw.rounded_rectangle((pad,pad,end,end),round(3*app.ui_scale*q),fill=color if selected else panel,
                                   outline=color if selected else muted,width=max(q,round(app.ui_scale*q)))
            if selected:
                points=[(edge*q*.32,edge*q*.50),(edge*q*.45,edge*q*.63),(edge*q*.70,edge*q*.36)]
                draw.line(points,fill='white',width=max(2*q,round(1.6*app.ui_scale*q)),joint='curve')
            states.append(ImageTk.PhotoImage(im.resize((edge,edge),Image.Resampling.LANCZOS),master=app.root))
        app.button_images.extend(states);element='Crazy.'+name+'.indicator'
        style.element_create(element,'image',states[0],('disabled','selected',states[5]),
                             ('disabled',states[4]),('focus','selected',states[3]),
                             ('focus',states[2]),('selected',states[1]),sticky='')
        style.layout(name,[('Checkbutton.padding',{'sticky':'nswe','children':[
            (element,{'side':'left','sticky':''}),('Checkbutton.label',{'side':'left','sticky':'nswe'})]})])
        style.configure(name,background=surface,foreground=ink,padding=(0,4))
        style.map(name,background=[('active',surface)],foreground=[('disabled',muted)])
    return style
