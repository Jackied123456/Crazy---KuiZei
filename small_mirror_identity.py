"""Local owner verification, short-lived face tracks and Windows DPAPI storage.

No image persistence, network requests, other-person enrollment or automatic
template learning. Track IDs are ephemeral and never stored on disk.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import sys

import numpy as np

MODEL = 'face_recognition_sface_2021dec.onnx'
OWNER_SCORE = .50
# OpenCV's SFace example uses .363 on LFW. Keep a separate, stricter owner
# threshold: ambiguous matches can cover content, but are never projected.
OTHER_SCORE = .363
VERIFY_INTERVAL = .5
TRACK_TTL = .8
POSES = ('正面看镜头', '轻轻向左转头', '轻轻向右转头', '稍微低头')
SAMPLES_PER_POSE = 3


def unit_feature(value):
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    if vector.size != 128 or not np.isfinite(vector).all():
        raise ValueError('本人特征格式无效')
    norm = float(np.linalg.norm(vector))
    if norm < 1e-6:
        raise ValueError('本人特征为空')
    return vector/norm


def similarity(feature, samples):
    vector = unit_feature(feature)
    return max((float(vector @ unit_feature(sample)) for sample in samples), default=-1.)


class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value, decrypt=False):
    if sys.platform != 'win32':
        raise RuntimeError('持久保存本人模板需要 Windows 加密服务')
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    args = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    api.CryptProtectData.argtypes = args
    api.CryptUnprotectData.argtypes = args
    api.CryptProtectData.restype = api.CryptUnprotectData.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    memory = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = Blob(len(value), memory)
    result = Blob()
    method = api.CryptUnprotectData if decrypt else api.CryptProtectData
    # No LOCAL_MACHINE flag: only the current Windows user can decrypt.
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), '本人模板解密失败' if decrypt else '本人模板加密失败')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class OwnerStore:
    MAGIC = b'SMOWNER1\x00'

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            encrypted = self.path.read_bytes()
        except FileNotFoundError:
            return []
        if not encrypted.startswith(self.MAGIC):
            raise ValueError('本人模板格式无效，请重新录入；原文件已保留')
        data = json.loads(dpapi(encrypted[len(self.MAGIC):], decrypt=True))
        if data.get('version') != 1 or data.get('model') != MODEL:
            raise ValueError('本人模板版本不兼容，请重新录入')
        samples = data.get('samples')
        if not isinstance(samples, list) or not 3 <= len(samples) <= 24:
            raise ValueError('本人模板样本无效，请重新录入')
        return [unit_feature(item) for item in samples]

    def save(self, samples):
        normalized = [unit_feature(item).tolist() for item in samples]
        if not 3 <= len(normalized) <= 24:
            raise ValueError('本人样本数量无效')
        data = json.dumps(dict(version=1, model=MODEL, samples=normalized), separators=(',', ':')).encode()
        payload = self.MAGIC + dpapi(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self):
        self.path.unlink(missing_ok=True)
        self.path.with_suffix('.tmp').unlink(missing_ok=True)


def overlap(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    area = max(0, min(ax+aw, bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
    return area/max(1,aw*ah+bw*bh-area)


def nearby(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    distance = math.hypot(ax+aw/2-bx-bw/2, ay+ah/2-by-bh/2)
    return distance/max(1,math.hypot(aw,ah)) < .55 and .5 < (bw*bh)/max(1,aw*ah) < 2


class SFace:
    def __init__(self, cv2):
        self.cv2 = cv2
        folder = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))/'models'
        self.engine = cv2.FaceRecognizerSF.create(str(folder/MODEL), '')

    def feature(self, frame, candidate):
        row = candidate.get('row')
        candidate['identity_hint']=''
        if row is None or len(row)<15 or not np.isfinite(row).all():
            candidate['identity_hint']='脸部关键点没看清';return None
        if min(candidate['bbox'][2:]) < 24:
            candidate['identity_hint']='脸太小，靠近一点或提高相机分辨率';return None
        # Do not silently undo the high-sensitivity detector's .55 threshold.
        if candidate.get('confidence',0)<.55:
            candidate['identity_hint']='人脸还没看清';return None
        x,y,w,h=candidate['bbox']
        crop=frame[max(0,y):min(frame.shape[0],y+h),max(0,x):min(frame.shape[1],x+w)]
        if not crop.size:
            candidate['identity_hint']='整张脸还没进入画面';return None
        gray=self.cv2.cvtColor(crop,self.cv2.COLOR_BGR2GRAY)
        if not 15<float(gray.mean())<245:
            candidate['identity_hint']='脸上太暗或太亮，调整一下光线';return None
        if self.cv2.Laplacian(gray,self.cv2.CV_64F).var()<15:
            candidate['identity_hint']='画面有点糊，稍微稳一下';return None
        try:
            aligned = self.engine.alignCrop(frame, np.asarray(row, dtype=np.float32))
            return unit_feature(self.engine.feature(aligned))
        except (self.cv2.error,ValueError):
            candidate['identity_hint']='这张脸暂时没核对好';return None

    def enrollment_feature(self, frame, candidate):
        x,y,w,h = candidate['bbox']
        if min(w,h) < 64 or candidate.get('confidence',0) < .8:
            return None, '离镜头近一点，让整张脸露出来'
        crop = frame[y:y+h,x:x+w]
        gray = self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2GRAY)
        if not 30 < float(gray.mean()) < 235 or self.cv2.Laplacian(gray,self.cv2.CV_64F).var() < 35:
            return None, '画面有点糊或太暗，稳住一下，找个亮一点的位置'
        return self.feature(frame,candidate), ''


class OwnerTracker:
    """Detection-based tracks; identity is rechecked, never inherited by overlap alone."""
    def __init__(self, recognizer, samples=()):
        self.recognizer = recognizer
        self.set_samples(samples)

    def set_samples(self, samples):
        self.samples = [unit_feature(item) for item in samples]
        self.tracks = {}
        self.next_id = 1
        self.previous_gray = None

    def predict_owner(self, frame, now):
        cv = getattr(self.recognizer,'cv2',None)
        if cv is None:return None
        gray = cv.cvtColor(frame,cv.COLOR_BGR2GRAY)
        if self.previous_gray is not None and self.previous_gray.shape==gray.shape:
            for track in self.tracks.values():
                points = track.get('points')
                if track['role']!='owner' or points is None or now-track['seen']>TRACK_TTL:
                    continue
                moved,status,_ = cv.calcOpticalFlowPyrLK(self.previous_gray,gray,points,None)
                if moved is None:continue
                back,back_status,_ = cv.calcOpticalFlowPyrLK(gray,self.previous_gray,moved,None)
                if back is None:continue
                good = ((status.reshape(-1)>0)&(back_status.reshape(-1)>0)
                        &(np.linalg.norm(back-points,axis=2).reshape(-1)<1.5))
                if np.count_nonzero(good)<6:continue
                delta = np.median((moved-points).reshape(-1,2)[good],axis=0)
                x,y,w,h = track['bbox']
                if np.linalg.norm(delta)>.5*math.hypot(w,h):continue
                track['bbox']=(int(np.clip(x+delta[0],0,max(0,frame.shape[1]-w))),
                               int(np.clip(y+delta[1],0,max(0,frame.shape[0]-h))),w,h)
        return gray

    def owner_boxes(self, now):
        return [track['bbox'] for track in self.tracks.values() if track['role']=='owner'
                and now-track.get('owner_verified',-1e9)<=TRACK_TTL and now-track['seen']<=TRACK_TTL]

    def update(self, frame, faces, now):
        self.tracks = {key:t for key,t in self.tracks.items() if now-t['seen'] <= TRACK_TTL}
        gray = self.predict_owner(frame,now)
        used = set()
        for item in faces:
            bbox = item['bbox']
            choices = [(overlap(bbox,t['bbox']),key) for key,t in self.tracks.items()
                       if key not in used and nearby(t['bbox'],bbox)]
            choices.sort(reverse=True)
            ambiguous = len(choices)>1 and choices[0][0]-choices[1][0]<.15
            key = choices[0][1] if choices and choices[0][0]>.10 and not ambiguous else None
            if key is None:
                key = self.next_id; self.next_id += 1
                self.tracks[key] = dict(bbox=bbox,seen=now,checked=-1e9,role='uncertain',hits=0,feature=None)
            track = self.tracks[key]
            descriptor = None
            if gray is not None:
                x,y,w,h=bbox
                patch=gray[y:y+h,x:x+w]
                descriptor=self.recognizer.cv2.resize(patch,(12,12)).astype(np.float32).reshape(-1)
                descriptor-=descriptor.mean()
                descriptor/=max(1e-6,float(np.linalg.norm(descriptor)))
            previous=track.get('appearance')
            changed=(descriptor is not None and previous is not None and float(descriptor@previous)<.75)
            # A head crossing another face forces a fresh identity check.
            crossing = any(other is not item and overlap(bbox,other['bbox'])>.12 for other in faces)
            needs_check = (not self.samples or ambiguous or crossing or changed or track['role']=='uncertain'
                           or track['role']=='owner' and now-track.get('owner_verified',-1e9)>=TRACK_TTL
                           or now-track['checked']>=VERIFY_INTERVAL)
            if needs_check:
                feature = self.recognizer.feature(frame,item)
                track['feature'] = feature
                score = similarity(feature,self.samples) if feature is not None and self.samples else None
                proposed = ('owner' if score is not None and score>=OWNER_SCORE else
                            'other' if score is not None and score<=OTHER_SCORE else 'uncertain')
                if proposed == track.get('proposal') and proposed!='uncertain':
                    track['hits'] += 1
                else:
                    track['hits'] = 1; track['proposal'] = proposed
                # Ambiguous/low quality must not become an excluded owner by geometry.
                if (proposed=='uncertain' and track['role']=='owner' and not crossing and not ambiguous
                        and now-track.get('owner_verified',-1e9)<=TRACK_TTL):
                    pass  # Brief turn/blur bridges only a recently verified owner.
                else:
                    track['role'] = proposed if track['hits']>=2 else 'uncertain'
                if proposed=='owner' and track['role']=='owner':track['owner_verified']=now
                track['score'] = score
                track['checked'] = now
            track['bbox'],track['seen'] = bbox,now
            track['appearance']=descriptor
            used.add(key)
            item.update(track_id=key,identity=track['role'] if self.samples else 'unregistered',
                        owner_score=track.get('score'),feature=track.get('feature'))
        # Never trust two simultaneous owner matches. Request clearer evidence.
        owners = [item for item in faces if item['identity']=='owner']
        if len(owners)>1:
            for item in owners:
                item['identity']='uncertain';self.tracks[item['track_id']]['role']='uncertain'
        if gray is not None:
            cv = self.recognizer.cv2
            for track in self.tracks.values():
                if track['role']!='owner':continue
                x,y,w,h = track['bbox']
                mask = np.zeros(gray.shape,dtype=np.uint8)
                mask[max(0,y):min(gray.shape[0],y+h),max(0,x):min(gray.shape[1],x+w)] = 255
                track['points'] = cv.goodFeaturesToTrack(gray,40,.02,4,mask=mask)
            self.previous_gray=gray
        return faces


class Enrollment:
    """Selected owner, quality checks and optional landmark-guided auto capture."""
    def __init__(self, recognizer, track_id, automatic=False):
        self.recognizer, self.track_id = recognizer, track_id
        self.samples = []
        self.stage = 0
        self.capturing = True
        self.last_sample = -1e9
        self.message = ''
        self.automatic=automatic
        self.baseline=None
        self.front_poses=[]
        self.last_seen=None
        self.last_bbox=None

    def capture(self):
        self.capturing = True

    def retry(self):
        self.samples=self.samples[:self.stage*SAMPLES_PER_POSE]
        self.last_sample=-1e9;self.capturing=True
        if self.stage==0:self.front_poses=[];self.baseline=None

    def pose_ready(self,item):
        from small_mirror_attention import face_pose
        pose=face_pose(item.get('row'))
        if pose is None:self.message='眼睛和鼻子没看清，正对镜头试试';return False
        yaw,pitch=pose
        if self.stage==0:
            ready=abs(yaw)<.28 and .25<pitch<.85
            self.message='' if ready else '先看镜头，头稍微摆正一点'
        elif self.stage==1:
            ready=yaw-self.baseline[0]>=.10
            self.message='' if ready else '跟着箭头，往左边转一点 ←'
        elif self.stage==2:
            ready=yaw-self.baseline[0]<=-.10
            self.message='' if ready else '再往右边转一点 →'
        else:
            ready=pitch-self.baseline[1]>=.055
            self.message='' if ready else '下巴稍微往下收一点 ↓'
        return ready

    def update(self, frame, faces, now):
        item = next((item for item in faces if item.get('track_id')==self.track_id),None)
        # A brief YuNet miss may assign the same person a new track number.
        # Reattach only with unique biometric evidence, never by position alone.
        if (item is None and self.samples and self.last_seen is not None
                and now-self.last_seen<=2. and self.last_bbox is not None):
            matches=[]
            for candidate in faces:
                if 'bbox' not in candidate:continue
                feature,error=self.recognizer.enrollment_feature(frame,candidate)
                if feature is not None:
                    score=similarity(feature,self.samples)
                    if score>=OWNER_SCORE:matches.append((score,candidate))
            matches.sort(key=lambda pair:pair[0],reverse=True)
            if matches and (len(matches)==1 or matches[0][0]-matches[1][0]>=.10):
                item=matches[0][1];self.track_id=item['track_id']
        if item is None:
            self.message = '没跟上你，先回到圆环里；仍没认出时点“重新选我”'
            return
        self.last_seen=now;self.last_bbox=item.get('bbox')
        if not self.capturing or self.stage>=len(POSES) or now-self.last_sample<.3:
            return
        feature, error = self.recognizer.enrollment_feature(frame,item)
        if feature is None:
            self.message = error;return
        if self.automatic and not self.pose_ready(item):return
        if self.samples and similarity(feature,self.samples)<.363:
            self.message='看着不像刚才那张脸，点“重新选我”确认一下';return
        if self.automatic and self.stage==0:
            from small_mirror_attention import face_pose
            self.front_poses.append(face_pose(item.get('row')))
        self.samples.append(feature.copy());self.last_sample=now;self.message=''
        if len(self.samples)%SAMPLES_PER_POSE==0:
            if self.automatic and self.stage==0:self.baseline=tuple(np.mean(self.front_poses,axis=0))
            self.stage += 1;self.capturing=self.automatic

    def status(self):
        done = self.stage>=len(POSES)
        return dict(stage=self.stage,count=len(self.samples),capturing=self.capturing,done=done,
                    message=self.message or ('拍好了，下一步验证一下' if done else
                        ('看着镜头，稳住一下','往左转一点 ←','往右转一点 →','下巴往下收一点 ↓')[self.stage]))
