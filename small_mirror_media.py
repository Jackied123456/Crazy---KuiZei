"""Optional local GIF packs. No network, camera, template or Git operations."""
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
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


def media_dir(data_dir,builtin):
    """Resolve an opaque pack ID, never a user-supplied absolute path."""
    base=Path(data_dir);builtin=Path(builtin)
    try:
        pointer=base/'active-media.json'
        if _linked(pointer):return builtin
        if pointer.stat().st_size>256:return builtin
        token=json.loads(pointer.read_text(encoding='utf-8')).get('pack')
        if not isinstance(token,str) or not TOKEN.fullmatch(token):return builtin
        storage=base/'media-packs'
        candidate=storage/token
        if _linked(storage) or _linked(candidate):return builtin
        if candidate.resolve().parent!=storage.resolve():return builtin
        config=candidate/'programs.json'
        if _linked(config) or config.read_bytes()!=(builtin/'programs.json').read_bytes():return builtin
        return candidate
    except (OSError,ValueError,TypeError,AttributeError):
        return builtin


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


def clear_packs(data_dir):
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
    return len(targets)
