"""Display-only mild deformation; never used for verification or recording."""
import math
import numpy as np

EFFECTS=('轻轻鼓起来','稍微拉长','轻轻挤一下','普通特写')

def deform(frame,mode,cv):
    if mode=='普通特写':return frame.copy()
    h,w=frame.shape[:2]
    yy,xx=np.mgrid[:h,:w].astype(np.float32)
    nx=(xx-w*.5)/max(1,w*.5);ny=(yy-h*.48)/max(1,h*.5)
    weight=np.maximum(0,1-nx*nx-ny*ny)**2
    # Small, bounded displacement preserves the eyes, nose and overall identity.
    if mode=='稍微拉长':mapped_y=yy-(yy-h*.48)*.12*weight;mapped_x=xx
    elif mode=='轻轻挤一下':mapped_x=xx+(xx-w*.5)*.12*weight;mapped_y=yy
    else:
        mapped_x=xx-(xx-w*.5)*.15*weight;mapped_y=yy-(yy-h*.48)*.10*weight
    return cv.remap(frame,mapped_x.astype(np.float32),mapped_y.astype(np.float32),cv.INTER_LINEAR,
                    borderMode=cv.BORDER_REPLICATE)

def draw_bugs(canvas,width,height,now,scale=1):
    """Canvas-only animation; does not enter camera crops or saved clips."""
    canvas.delete('bugs')
    for index in range(5):
        x=(now*(12+index*3)+index*83)%max(1,width)
        y=height*(.18+.15*index)+math.sin(now*1.8+index)*height*.035
        size=4*scale
        canvas.create_oval(x-size,y-size*1.6,x+size,y+size*1.6,fill='#b78a58',outline='#39271a',tags='bugs')
        for side in (-1,1):
            for offset in (-1,0,1):
                canvas.create_line(x+side*size*.7,y+offset*size,
                    x+side*size*2,y+offset*size+math.sin(now*9+index+offset)*size,fill='#b78a58',tags='bugs')
