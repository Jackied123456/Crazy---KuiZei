"""Optional local GIF packs. No network, camera, template or Git operations."""
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

from PIL import Image
from small_mirror_performer import CLIP_FILES

MAX_FILE_BYTES=20*1024*1024
MAX_PACK_BYTES=60*1024*1024
MAX_FRAME_PIXELS=2_000_000
MAX_FRAMES=300
MAX_SCALED_PIXELS=96_000_000
TOKEN=re.compile(r'[a-f0-9]{32}')


def _linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


def validate_gif(data):
    if len(data)>MAX_FILE_BYTES:raise ValueError('单个动图不能超过 20 MB。')
    pixels=0
    with Image.open(BytesIO(data)) as image:
        if image.format!='GIF':raise ValueError('这里只接收 GIF 动图。')
        if image.width*image.height>MAX_FRAME_PIXELS:raise ValueError('动图画面太大，请先缩小。')
        if image.n_frames>MAX_FRAMES:raise ValueError('单个动图最多 300 帧。')
        for index in range(image.n_frames):
            image.seek(index)
            if image.width*image.height>MAX_FRAME_PIXELS:raise ValueError('动图画面太大，请先缩小。')
            image.load()
            ratio=min(1,384/image.width,384/image.height)
            pixels+=round(image.width*ratio)*round(image.height*ratio)
    return pixels


def portable_media_dir(builtin,executable=None):
    """Optional sidecar belonging to this executable, not a profile search.

    Never execute a sidecar manifest: only the built-in programme bytes are
    accepted. GIF decoding keeps the renderer's existing bounds and fallbacks.
    """
    builtin=Path(builtin)
    if executable is None:
        if not getattr(sys,'frozen',False):return builtin
        executable=sys.executable
    candidate=Path(executable).resolve().parent/'Crazy-local-media'
    try:
        manifest=candidate/'programs.json'
        if _linked(candidate) or _linked(manifest):return builtin
        if manifest.stat().st_size>128*1024:return builtin
        if manifest.read_bytes()!=(builtin/'programs.json').read_bytes():return builtin
        if not any((candidate/name).is_file() and not _linked(candidate/name) for name in CLIP_FILES.values()):return builtin
        return candidate
    except OSError:return builtin


def media_dir(data_dir,builtin):
    """Resolve an opaque pack ID, never a user-supplied absolute path."""
    base=Path(data_dir);builtin=Path(builtin)
    fallback=portable_media_dir(builtin)
    try:
        pointer=base/'active-media.json'
        if _linked(pointer):return fallback
        if pointer.stat().st_size>256:return fallback
        choice=json.loads(pointer.read_text(encoding='utf-8'))
        if choice.get('disabled') is True:return builtin
        token=choice.get('pack')
        if not isinstance(token,str) or not TOKEN.fullmatch(token):return fallback
        storage=base/'media-packs'
        candidate=storage/token
        if _linked(storage) or _linked(candidate):return fallback
        if candidate.resolve().parent!=storage.resolve():return fallback
        config=candidate/'programs.json'
        if _linked(config) or config.read_bytes()!=(builtin/'programs.json').read_bytes():return fallback
        return candidate
    except (OSError,ValueError,TypeError,AttributeError):
        return fallback


def import_pack(source,data_dir,builtin):
    """Stage and validate each known slot; only commit after the whole pack passes.

    Unrelated files and source-folder JSON are not imported. Existing settings,
    identity templates and prior active pack remain untouched on failure.
    """
    source=Path(source).resolve();base=Path(data_dir);builtin=Path(builtin)
    if not source.is_dir():raise ValueError('请选择装有 GIF 的文件夹。')
    storage=base/'media-packs'
    if _linked(storage):raise ValueError('素材目录不能是链接。')
    storage.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex;stage=storage/token
    stage.mkdir()
    count=total=pixels=0
    try:
        for filename in CLIP_FILES.values():
            path=source/filename
            if not path.exists():continue
            if _linked(path) or path.resolve().parent!=source:raise ValueError('动图文件不能是链接。')
            if path.stat().st_size>MAX_FILE_BYTES:raise ValueError('单个动图不能超过 20 MB。')
            data=path.read_bytes()
            total+=len(data)
            if total>MAX_PACK_BYTES:raise ValueError('整组素材不能超过 60 MB。')
            pixels+=validate_gif(data)
            if pixels>MAX_SCALED_PIXELS:raise ValueError('整组动图帧数太多，请精简后再导入。')
            (stage/filename).write_bytes(data)
            count+=1
        if not count:raise ValueError('没有找到可用动图。请按素材说明中的文件名准备 GIF。')
        (stage/'programs.json').write_bytes((builtin/'programs.json').read_bytes())
        pointer=base/'active-media.tmp'
        if _linked(pointer):raise ValueError('素材状态文件不能是链接。')
        pointer.write_text(json.dumps({'pack':token},separators=(',',':')),encoding='utf-8')
        pointer.replace(base/'active-media.json')
        return count
    except Exception:
        if stage.is_dir() and not stage.is_symlink() and stage.resolve().parent==storage.resolve():
            shutil.rmtree(stage)
        raise


def clear_packs(data_dir,disable_portable=False):
    """Remove only this app's known imported files; refuse unexpected contents."""
    base=Path(data_dir);storage=base/'media-packs'
    if _linked(storage):raise ValueError('素材目录不能是链接。')
    allowed=set(CLIP_FILES.values())|{'programs.json'}
    targets=[]
    if storage.exists():
        for folder in storage.iterdir():
            if not TOKEN.fullmatch(folder.name) or _linked(folder) or not folder.is_dir():
                raise ValueError('素材目录里有未知文件，未执行清理。')
            if folder.resolve().parent!=storage.resolve():raise ValueError('素材路径异常。')
            if any(_linked(path) or not path.is_file() or path.name not in allowed for path in folder.iterdir()):
                raise ValueError('素材目录里有未知文件，未执行清理。')
            targets.append(folder)
    for folder in targets:shutil.rmtree(folder)
    (base/'active-media.json').unlink(missing_ok=True)
    (base/'active-media.tmp').unlink(missing_ok=True)
    if disable_portable:
        base.mkdir(parents=True,exist_ok=True)
        temporary=base/'active-media.tmp'
        temporary.write_text('{"disabled": true}',encoding='utf-8')
        temporary.replace(base/'active-media.json')
    return len(targets)
