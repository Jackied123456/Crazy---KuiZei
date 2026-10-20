"""Build the Windows app with explicit data inputs only.

This command starts no app or camera. Profiles are never part of the data list.
The default user build requires all twelve approved media slots; it must not
silently ship the fallback character. Build logs stay under ignored build/.
"""
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from small_mirror_brand import logo_image
from small_mirror_performer import CLIP_FILES
MODELS=('face_detection_yunet_2023mar.onnx','face_recognition_sface_2021dec.onnx',
        'LICENSE-YuNet.txt','LICENSE-SFace.txt')


def main():
    if sys.platform!='win32':raise SystemExit('The app build requires Windows.')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean',action='store_true',help='Discard cached PyInstaller analysis before building')
    parser.add_argument('--media-dir',type=Path,default=ROOT/'assets/yaoshui',help='Explicit folder containing all 12 GIF slots')
    parser.add_argument('--original-sentry',action='store_true',help='Explicit developer build without third-party character media')
    args=parser.parse_args()
    build=ROOT/'build';build.mkdir(exist_ok=True)
    # Original code-drawn brand mark, matching the app's own title-bar icon.
    image=logo_image(256)
    icon=build/'Crazy.ico'
    image.save(icon,sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    data=[(ROOT/'models'/name,'models') for name in MODELS]
    data.append((ROOT/'assets'/'yaoshui'/'programs.json','assets/yaoshui'))
    if not args.original_sentry:
        data.extend((args.media_dir/name,'assets/yaoshui') for name in CLIP_FILES.values())
    for name in ('THIRD_PARTY_NOTICES.txt','PRIVACY.md'):
        data.append((ROOT/name,'licenses'))
    if (ROOT/'LICENSE').is_file():data.append((ROOT/'LICENSE','licenses'))
    for source,_destination in data:
        if source.is_symlink() or not source.is_file():raise SystemExit('Missing or linked build input: '+source.name)
    arguments=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile',
               '--noconsole','--noupx','--name','Crazy-v13.7.1-Beta',
               '--specpath',str(ROOT/'build'),'--workpath',str(ROOT/'build'/'pyinstaller'),
               '--distpath',str(ROOT/'dist'),'--paths',str(ROOT),
               '--icon',str(icon),
               '--hidden-import','PIL.ImageTk','--hidden-import','cv2']
    if args.clean:arguments.append('--clean')
    for source,destination in data:
        arguments+=['--add-data',str(source)+os.pathsep+destination]
    arguments.append(str(ROOT/'small_mirror.py'))
    subprocess.run(arguments,cwd=ROOT,check=True)
    print('LOCAL BUILD COMPLETE: dist/Crazy-v13.7.1-Beta.exe. Public distribution requires the source publication gate, media provenance review and binary privacy review.')


if __name__=='__main__':
    main()
