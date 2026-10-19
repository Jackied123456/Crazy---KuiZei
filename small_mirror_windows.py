"""Windows-only window selection and DWM live previews. Never saves pixels.

Bindings are deliberately session-only: a restarted application must be selected
again. A destroyed window's handle cannot silently become a new binding.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass
import os

CALLBACK=getattr(ctypes,'WINFUNCTYPE',ctypes.CFUNCTYPE)
ENUMPROC=CALLBACK(W.BOOL,W.HWND,W.LPARAM)
EVENTPROC=CALLBACK(None,W.HANDLE,W.DWORD,W.HWND,W.LONG,W.LONG,W.DWORD,W.DWORD)

class ThumbnailProperties(ctypes.Structure):
    _fields_=[('flags',W.DWORD),('destination',W.RECT),('source',W.RECT),
              ('opacity',W.BYTE),('visible',W.BOOL),('client_only',W.BOOL)]

@dataclass(frozen=True)
class WindowToken:
    hwnd:int
    pid:int
    thread:int
    class_name:str
    epoch:int
    title:str

class WindowAPI:
    def __init__(self):
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.d=ctypes.WinDLL('dwmapi',use_last_error=True)
        self.epochs={}
        self.thumbnails=set()
        declarations={
            'EnumWindows':([ENUMPROC,W.LPARAM],W.BOOL),
            'GetWindowThreadProcessId':([W.HWND,ctypes.POINTER(W.DWORD)],W.DWORD),
            'IsWindow':([W.HWND],W.BOOL),'IsWindowVisible':([W.HWND],W.BOOL),'IsIconic':([W.HWND],W.BOOL),
            'GetWindowTextLengthW':([W.HWND],ctypes.c_int),
            'GetWindowTextW':([W.HWND,W.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetClassNameW':([W.HWND,W.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetWindowRect':([W.HWND,ctypes.POINTER(W.RECT)],W.BOOL),
            'GetWindow':([W.HWND,W.UINT],W.HWND),'GetAncestor':([W.HWND,W.UINT],W.HWND),
            'ClientToScreen':([W.HWND,ctypes.POINTER(W.POINT)],W.BOOL),
            'SetWindowPos':([W.HWND,W.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.UINT],W.BOOL),
            'SetWinEventHook':([W.DWORD,W.DWORD,W.HMODULE,EVENTPROC,W.DWORD,W.DWORD,W.DWORD],W.HANDLE),
            'UnhookWinEvent':([W.HANDLE],W.BOOL),
        }
        for name,(args,result) in declarations.items():
            method=getattr(self.u,name);method.argtypes=args;method.restype=result
        self.u.GetWindowLongPtrW.argtypes=[W.HWND,ctypes.c_int];self.u.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        self.d.DwmGetWindowAttribute.argtypes=[W.HWND,W.DWORD,ctypes.c_void_p,W.DWORD]
        self.d.DwmRegisterThumbnail.argtypes=[W.HWND,W.HWND,ctypes.POINTER(W.HANDLE)]
        self.d.DwmUpdateThumbnailProperties.argtypes=[W.HANDLE,ctypes.POINTER(ThumbnailProperties)]
        self.d.DwmUnregisterThumbnail.argtypes=[W.HANDLE]
        self.d.DwmQueryThumbnailSourceSize.argtypes=[W.HANDLE,ctypes.POINTER(W.SIZE)]
        for name in ('DwmGetWindowAttribute','DwmRegisterThumbnail','DwmUpdateThumbnailProperties',
                     'DwmUnregisterThumbnail','DwmQueryThumbnailSourceSize'):
            getattr(self.d,name).restype=ctypes.c_long
        def destroyed(_hook,_event,hwnd,object_id,child_id,_thread,_time):
            if hwnd and object_id==0 and child_id==0:
                key=int(hwnd);self.epochs[key]=self.epochs.get(key,0)+1
        self.callback=EVENTPROC(destroyed)
        self.hook=self.u.SetWinEventHook(0x8001,0x8001,None,self.callback,0,0,0)
        if not self.hook:raise OSError(ctypes.get_last_error(),'窗口关闭监听不可用')

    def token(self,hwnd):
        if not self.u.IsWindow(hwnd):return None
        pid=W.DWORD();thread=self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        title=ctypes.create_unicode_buffer(min(4096,self.u.GetWindowTextLengthW(hwnd)+1))
        self.u.GetWindowTextW(hwnd,title,len(title))
        name=ctypes.create_unicode_buffer(256);self.u.GetClassNameW(hwnd,name,256)
        return WindowToken(int(hwnd),pid.value,int(thread),name.value,self.epochs.get(int(hwnd),0),title.value)

    def valid(self,token):
        current=self.token(token.hwnd)
        return current is not None and (current.pid,current.thread,current.class_name,current.epoch)==(
            token.pid,token.thread,token.class_name,token.epoch)

    def list_windows(self):
        result=[]
        def visit(hwnd,_):
            token=self.token(hwnd)
            if not token or token.pid==os.getpid() or not token.title.strip() or not self.u.IsWindowVisible(hwnd):return True
            if self.u.GetWindowLongPtrW(hwnd,-20)&0x80:return True
            cloaked=W.DWORD()
            if self.d.DwmGetWindowAttribute(hwnd,14,ctypes.byref(cloaked),ctypes.sizeof(cloaked))==0 and cloaked.value:return True
            if token.class_name in ('Progman','WorkerW','Shell_TrayWnd'):return True
            result.append(token);return True
        callback=ENUMPROC(visit);self.u.EnumWindows(callback,0)
        return result

    def bounds(self,token):
        if not self.target_visible(token):return None
        rect=W.RECT()
        if not self.u.GetWindowRect(token.hwnd,ctypes.byref(rect)):return None
        width,height=rect.right-rect.left,rect.bottom-rect.top
        return (rect.left,rect.top,width,height) if width>0 and height>0 else None

    def target_visible(self,token):
        return bool(self.valid(token) and self.u.IsWindowVisible(token.hwnd) and not self.u.IsIconic(token.hwnd))

    def cover(self,mask,token,bounds):
        """Place just above the selected source, below unrelated foreground apps."""
        if not self.valid(token):return False
        hwnd=self.u.GetAncestor(mask.winfo_id(),2) or mask.winfo_id()
        x,y,w,h=bounds
        flags=0x0010|0x0200  # NOACTIVATE | NOOWNERZORDER: never raise the app root.
        source_topmost=bool(self.u.GetWindowLongPtrW(token.hwnd,-20)&8)
        # Remove the generic floating helper's TOPMOST flag before normal z-order.
        # Even an already-adjacent mask may still be topmost from its creation.
        if not source_topmost and self.u.GetWindowLongPtrW(hwnd,-20)&8:
            self.u.SetWindowPos(hwnd,ctypes.c_void_p(-2),0,0,0,0,flags|0x0001|0x0002)
        previous=self.u.GetWindow(token.hwnd,3)  # GW_HWNDPREV
        mask._cover_predecessor=int(previous or 0)
        if previous==hwnd:
            rect=W.RECT()
            if self.u.GetWindowRect(hwnd,ctypes.byref(rect)):
                actual=(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)
                if actual==tuple(bounds):return True
            return bool(self.u.SetWindowPos(hwnd,None,x,y,w,h,flags|0x0004))
        # When the target leads the normal window group, its predecessor can
        # be a privileged always-on-top window. Referencing that HWND can fail
        # with access denied. HWND_TOP positions within the normal group,
        # still below every always-on-top window, without touching that HWND.
        if previous and not source_topmost and self.u.GetWindowLongPtrW(previous,-20)&8:
            previous=None
        return bool(self.u.SetWindowPos(hwnd,previous or None,x,y,w,h,flags))

    def verify_cover(self,mask,token,bounds):
        """Check native geometry, visibility and ordering instead of trusting Tk."""
        if not self.valid(token):return False
        hwnd=self.u.GetAncestor(mask.winfo_id(),2) or mask.winfo_id()
        if not self.u.IsWindowVisible(hwnd) or self.u.GetWindowLongPtrW(hwnd,-20)&0x20:return False
        rect=W.RECT()
        if not self.u.GetWindowRect(hwnd,ctypes.byref(rect)):return False
        actual=(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)
        if any(abs(a-b)>1 for a,b in zip(actual,bounds)):return False
        current=self.u.GetWindow(token.hwnd,3)
        for _ in range(512):
            if current==hwnd:return True
            if not current:return False
            current=self.u.GetWindow(current,3)
        return False

    def thumbnail(self,window,token):
        if not self.valid(token):return None
        window.update_idletasks()
        destination=self.u.GetAncestor(window.winfo_id(),2) or window.winfo_id()
        thumb=W.HANDLE()
        if self.d.DwmRegisterThumbnail(destination,token.hwnd,ctypes.byref(thumb))!=0:return None
        self.thumbnails.add(thumb.value)
        return thumb

    def place_thumbnail(self,thumb,window,widget):
        destination=self.u.GetAncestor(window.winfo_id(),2) or window.winfo_id()
        origin=W.POINT(0,0);self.u.ClientToScreen(destination,ctypes.byref(origin))
        x,y=widget.winfo_rootx()-origin.x,widget.winfo_rooty()-origin.y
        w,h=widget.winfo_width(),widget.winfo_height()
        size=W.SIZE()
        if self.d.DwmQueryThumbnailSourceSize(thumb,ctypes.byref(size))==0 and min(size.cx,size.cy)>0:
            scale=min(w/size.cx,h/size.cy);dw,dh=round(size.cx*scale),round(size.cy*scale)
            x+=(w-dw)//2;y+=(h-dh)//2;w,h=dw,dh
        props=ThumbnailProperties(1|4|8|16,W.RECT(x,y,x+w,y+h),W.RECT(),255,True,False)
        return self.d.DwmUpdateThumbnailProperties(thumb,ctypes.byref(props))==0

    def close_thumbnail(self,thumb):
        key=thumb.value if isinstance(thumb,W.HANDLE) else thumb
        if key in self.thumbnails:
            self.d.DwmUnregisterThumbnail(thumb);self.thumbnails.discard(key)

    def close(self):
        for thumb in tuple(self.thumbnails):self.close_thumbnail(thumb)
        if self.hook:self.u.UnhookWinEvent(self.hook);self.hook=None
