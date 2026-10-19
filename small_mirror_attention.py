"""Approximate facing-screen filter, NOT eye gaze or proof of intent."""
import math

def face_pose(row):
    if row is None or len(row)<15:return None
    lx,ly,rx,ry,nx,ny,ml,mly,mr,mry=(float(value) for value in row[4:14])
    if not all(math.isfinite(v) for v in (lx,ly,rx,ry,nx,ny,ml,mly,mr,mry)):return None
    if rx<lx:lx,ly,rx,ry=rx,ry,lx,ly
    eye_distance=math.hypot(rx-lx,ry-ly)
    if eye_distance<4:return None
    ex,ey=(lx+rx)/2,(ly+ry)/2
    ux,uy=(rx-lx)/eye_distance,(ry-ly)/eye_distance
    depth=-(ml/2+mr/2-ex)*uy+(mly/2+mry/2-ey)*ux
    if depth<4:return None
    yaw=((nx-ex)*ux+(ny-ey)*uy)/eye_distance
    pitch=(-(nx-ex)*uy+(ny-ey)*ux)/depth
    return yaw,pitch

def facing_screen(candidate):
    pose=face_pose(candidate.get('row') if candidate else None)
    return pose is not None and abs(pose[0])<=.30 and .20<=pose[1]<=.90


class FacingTimer:
    """Require fresh positive frames; tolerate one ambiguous/missing frame.

    A definite turn away resets immediately. During a miss nothing is displayed;
    only acquisition progress is retained for at most .45 seconds.
    """
    def __init__(self):self.reset()

    def reset(self):
        self.target=None;self.since=None;self.last_positive=None
        self.serial=None;self.hits=0;self.missing_since=None

    def update(self,now,serial,candidate):
        pose=face_pose(candidate.get('row')) if candidate else None
        target=candidate.get('track_id') if candidate else None
        if pose is not None and (abs(pose[0])>.45 or pose[1]<.12 or pose[1]>1.05):
            self.reset();return False
        if candidate is None or not facing_screen(candidate):
            if self.missing_since is None:self.missing_since=now
            if self.last_positive is not None and now-self.last_positive>.45:self.reset()
            return False
        if (self.since is None or target!=self.target or self.last_positive is None
                or now-self.last_positive>(.45 if self.missing_since is not None else .8)):
            self.target=target;self.since=now;self.hits=0;self.serial=None
        self.missing_since=None
        if serial!=self.serial:
            self.hits+=1;self.serial=serial;self.last_positive=now
        return now-self.since>=1. and self.hits>=3
