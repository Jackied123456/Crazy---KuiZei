"""Approximate facing-screen filter, NOT eye gaze or proof of intent."""
import math

def face_pose(row):
    if row is None or len(row)<15:return None
    lx,ly,rx,ry,nx,ny,ml,mly,mr,mry=(float(value) for value in row[4:14])
    eye_distance=math.hypot(rx-lx,ry-ly)
    mouth_y=(mly+mry)/2;eye_y=(ly+ry)/2
    if eye_distance<4 or mouth_y-eye_y<4:return None
    yaw=(nx-(lx+rx)/2)/eye_distance
    pitch=(ny-eye_y)/(mouth_y-eye_y)
    return yaw,pitch

def facing_screen(candidate):
    pose=face_pose(candidate.get('row') if candidate else None)
    return pose is not None and abs(pose[0])<=.24 and .28<=pose[1]<=.82
