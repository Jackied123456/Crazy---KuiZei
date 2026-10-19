"""Opt-in event close-up recording. No audio, screen capture or network.

Only already-redacted, unmodified observer crops reach this module. Encoding is
off the UI thread. Space limits stop new clips; existing files are never pruned.
"""
from datetime import datetime
from pathlib import Path
import queue
import threading
import uuid
import numpy as np

FPS=6
SIZE=(320,240)
MAX_CLIP_SECONDS=60
SPACE_LIMIT=1024*1024*1024

def letterbox(frame,cv):
    height,width=frame.shape[:2]
    scale=min(SIZE[0]/width,SIZE[1]/height)
    w,h=max(1,round(width*scale)),max(1,round(height*scale))
    result=np.zeros((SIZE[1],SIZE[0],3),dtype=np.uint8)
    result[(SIZE[1]-h)//2:(SIZE[1]-h)//2+h,(SIZE[0]-w)//2:(SIZE[0]-w)//2+w]=cv.resize(frame,(w,h))
    return result

class EventRecorder:
    def __init__(self,directory,cv):
        self.directory=Path(directory);self.cv=cv
        self.queue=queue.Queue(32)
        self.epoch=0;self.target=None;self.started=None;self.missing=None;self.last_serial=None
        self.last_feed=-1e9;self.recording=False;self.error='';self.last_saved=None
        self.closed=False;self.thread=None

    def _put(self,item,control=False):
        try:self.queue.put_nowait(item)
        except queue.Full:
            if control:
                try:self.queue.get_nowait()
                except queue.Empty:pass
                self.queue.put_nowait(item)

    def feed(self,crop,now,serial,target):
        if self.closed or self.error:return
        if crop is None:
            if self.target is None:return
            if self.missing is None:self.missing=now
            if now-self.missing>=2:self.finish();return
            if now-self.last_feed>=1/FPS:
                self.last_feed=now;self._put(('frame',self.epoch,now,np.zeros((SIZE[1],SIZE[0],3),dtype=np.uint8)))
            return
        if serial==self.last_serial:return
        self.last_serial=serial;self.missing=None
        if self.target!=target or self.started is None or now-self.started>=MAX_CLIP_SECONDS:
            self.finish();self.target=target;self.started=now
        self.last_feed=now
        if self.thread is None:
            self.thread=threading.Thread(target=self._run,name='LocalCloseupRecorder',daemon=True);self.thread.start()
        self._put(('frame',self.epoch,now,letterbox(crop,self.cv)))

    def finish(self):
        if self.target is None and self.started is None:return
        self.epoch+=1;self.target=None;self.started=None;self.missing=None;self.last_serial=None
        self.recording=False
        if self.thread:self._put(('finish',self.epoch,None,None),control=True)

    def _run(self):
        writer=None;writer_epoch=None;path=None;start=None;written=0
        black=np.zeros((SIZE[1],SIZE[0],3),dtype=np.uint8)
        def finalize():
            nonlocal writer,path,written
            if writer is not None:writer.release();writer=None
            if path is not None:
                if written and path.exists():
                    final=path.with_name(path.name.replace('.partial.avi','.avi'))
                    path.replace(final);self.last_saved=final
                else:path.unlink(missing_ok=True)
            path=None;written=0;self.recording=False
        try:
            while True:
                kind,epoch,now,frame=self.queue.get()
                if kind=='quit':break
                if kind=='finish':
                    if writer_epoch!=epoch:finalize();writer_epoch=None
                    continue
                if writer_epoch!=epoch:
                    finalize()
                    self.directory.mkdir(parents=True,exist_ok=True)
                    used=sum(item.stat().st_size for item in self.directory.glob('*.avi') if item.is_file())
                    if used>=SPACE_LIMIT:raise OSError('录像已到 1 GB，请先清理一些片段再开启录像')
                    name=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
                    path=self.directory/(name+'.partial.avi')
                    writer=self.cv.VideoWriter(str(path),self.cv.VideoWriter_fourcc(*'MJPG'),FPS,SIZE)
                    if not writer.isOpened():raise OSError('录像文件没能创建，请检查保存位置和可用空间')
                    writer_epoch=epoch;start=now;written=0
                index=min(FPS*MAX_CLIP_SECONDS,round((now-start)*FPS))
                if index<written:continue
                while written<index:writer.write(black);written+=1
                writer.write(frame);written+=1;self.recording=True
        except Exception as exc:
            self.error='录像没保存好：'+str(exc)
        finally:
            try:finalize()
            except Exception as exc:self.error='录像收尾失败：'+str(exc)

    def close(self):
        if self.closed:return
        self.finish();self.closed=True
        if self.thread:
            self._put(('quit',self.epoch,None,None),control=True)
            self.thread.join(timeout=3)

def recordings(directory):
    root=Path(directory).resolve()
    if not root.exists():return []
    return sorted((item for item in root.glob('*.avi') if '.partial.' not in item.name
                   and item.is_file() and item.resolve().parent==root),key=lambda item:item.name,reverse=True)

def safe_clip(path,directory):
    item=Path(path).resolve();root=Path(directory).resolve()
    if item.parent!=root or item.suffix.lower()!='.avi' or '.partial.' in item.name:
        raise ValueError('这不是录像列表中的片段')
    return item
