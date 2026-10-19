"""Build the original-sentry Windows app with explicit data inputs only.

This command starts no app or camera. Private profiles and optional local GIFs
are never part of the data list. Build logs stay under ignored build/.
"""
from pathlib import Path
import argparse
import os
import subprocess
import sys
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
MODELS=('face_detection_yunet_2023mar.onnx','face_recognition_sface_2021dec.onnx',
        'LICENSE-YuNet.txt','LICENSE-SFace.txt')


def main():
    if sys.platform!='win32':raise SystemExit('The app build requires Windows.')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean',action='store_true',help='Discard cached PyInstaller analysis before building')
    args=parser.parse_args()
    build=ROOT/'build';build.mkdir(exist_ok=True)
    # Original code-drawn brand mark, matching the app's own title-bar icon.
    image=Image.new('RGBA',(256,256),(0,0,0,0));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,255,255),56,fill='#55715D')
    draw.ellipse((44,76,212,180),fill='white')
    draw.ellipse((96,96,160,160),fill='#55715D')
    icon=build/'Crazy.ico'
    image.save(icon,sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    data=[(ROOT/'models'/name,'models') for name in MODELS]
    data.append((ROOT/'assets'/'yaoshui'/'programs.json','assets/yaoshui'))
    for name in ('THIRD_PARTY_NOTICES.txt','PRIVACY.md'):
        data.append((ROOT/name,'licenses'))
    if (ROOT/'LICENSE').is_file():data.append((ROOT/'LICENSE','licenses'))
    for source,_destination in data:
        if source.is_symlink() or not source.is_file():raise SystemExit('Missing or linked build input: '+source.name)
    arguments=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile',
               '--noconsole','--noupx','--name','Crazy-v11-Beta',
               '--specpath',str(ROOT/'build'),'--workpath',str(ROOT/'build'/'pyinstaller'),
               '--distpath',str(ROOT/'dist'),'--paths',str(ROOT),
               '--icon',str(icon),
               '--hidden-import','PIL.ImageTk','--hidden-import','cv2']
    if args.clean:arguments.append('--clean')
    for source,destination in data:
        arguments+=['--add-data',str(source)+os.pathsep+destination]
    arguments.append(str(ROOT/'small_mirror.py'))
    subprocess.run(arguments,cwd=ROOT,check=True)
    print('LOCAL BUILD COMPLETE: dist/Crazy-v11-Beta.exe. Public distribution requires the source publication gate and binary privacy review.')


if __name__=='__main__':
    main()
