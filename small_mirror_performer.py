"""Local GIF programme compositor. It neither opens a camera nor hides content."""
from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading

from PIL import Image, ImageDraw, ImageFont, ImageOps

BG = '#1B2420'
PANEL = '#243029'
TEXT = '#ECF1E9'
MUTED = '#B4C3B7'
ACCENT = '#C1D8AB'
BORDER = '#3C4E40'
SOFT = '#2D3B31'

MOTIONS = (
    ('patrol', '后排巡逻', None, 'cheeky'),
    ('judge', '冷脸劝退', None, 'cheeky'),
    ('punch', '暴躁又客气', None, 'cheeky'),
    ('combo', '回敬小连招', None, 'cheeky'),
    ('barrage', '眼神劝退术', None, 'cheeky'),
    ('encore', '后排散场啦', None, 'cheeky'),
)
PHRASES = (
    ('cheeky', '欠欠的', '台词', ('轻轻提醒',)),
    ('guard', '屏幕哨兵', '台词', ('直接提醒',)),
    ('absurd', '抽象一点', '台词', ('节目效果',)),
)
DEFAULT_MOTIONS = [row[0] for row in MOTIONS]
DEFAULT_PHRASES = ['cheeky']
CLIP_FILES = {
    'patrol': 'patrol.gif', 'warning': 'close-warning.gif',
    'earlaugh': 'ear-laugh.gif', 'arms': 'arms-chin.gif',
    'frown': 'frown.gif', 'salute': 'salute-sleep.gif',
    'megaphone': 'megaphone.gif', 'straight': 'black-straight.gif',
    'heart': 'tiny-heart.gif', 'combo': 'tank-combo.gif',
    'barrage': 'red-barrage.gif', 'laugh': 'covered-giggle.gif',
}
MAX_GIF_BYTES=20*1024*1024
MAX_GIF_PIXELS=2_000_000
MAX_GIF_FRAMES=300
MAX_LIBRARY_PIXELS=96_000_000


def normalize_choices(values, valid_ids, defaults):
    """Accept known IDs once, preserving their chosen order; reject strings."""
    valid = set(valid_ids)
    result = []
    if isinstance(values, (list, tuple)):
        for value in values:
            if isinstance(value, str) and value in valid and value not in result:
                result.append(value)
    return result or [value for value in defaults if value in valid]


@dataclass(frozen=True)
class ProgrammeState:
    program: str
    clip: str
    label: str
    phrase: str
    style: str
    local_ms: float
    step_ms: float
    step_index: int
    duration_ms: int


class Programme:
    """An elapsed-time schedule, independent of render rate or camera rate."""
    def __init__(self, definitions, motions, phrases):
        self.definitions = definitions
        self.motions = motions
        self.phrases = phrases
        self.steps = []
        self.ends = []
        self._timeline = []
        total = 0
        for number, motion in enumerate(motions):
            start = total
            style = phrases[number % len(phrases)]
            for step in definitions[motion]['steps']:
                duration = max(20, int(step['duration_ms']))
                self.steps.append((motion, style, dict(step), total))
                total += duration
                self.ends.append(total)
            self._timeline.append((motion, start / 1000, (total - start) / 1000))
        if not self.steps:
            raise ValueError('至少需要一个可用节目。')
        self.duration_ms = total
        self.duration = total / 1000

    def timeline(self):
        return iter(self._timeline)

    def state(self, elapsed):
        try:
            seconds = float(elapsed)
        except (TypeError, ValueError):
            seconds = 0
        if not math.isfinite(seconds):
            seconds = 0
        tick = max(0, seconds) * 1000 % self.duration_ms
        # A tiny tolerance avoids a binary-float value just before an exact cut.
        index = bisect_right(self.ends, tick + 1e-7)
        index = min(index, len(self.steps) - 1)
        motion, style, step, start = self.steps[index]
        local = max(0, tick - start)
        return ProgrammeState(
            motion, step['clip'], step['label'], step['copies'][style], style,
            local + int(step.get('start_ms', 0)), local, index,
            int(step['duration_ms']),
        )


class _Clip:
    """Decode once, retaining every original frame and its actual duration."""
    def __init__(self, path):
        if Path(path).is_symlink() or Path(path).stat().st_size>MAX_GIF_BYTES:
            raise ValueError('Local GIF is linked or too large')
        self.frames = []
        self.ends = []
        total = 0
        with Image.open(path) as source:
            if source.format!='GIF' or source.n_frames>MAX_GIF_FRAMES or source.width*source.height>MAX_GIF_PIXELS:
                raise ValueError('Local GIF exceeds playback limits')
            for frame_index in range(source.n_frames):
                source.seek(frame_index)
                if source.width*source.height>MAX_GIF_PIXELS:raise ValueError('GIF frame exceeds playback limits')
                duration = max(20, int(source.info.get('duration', 100)))
                picture = source.convert('RGB')
                picture.thumbnail((384, 384), Image.Resampling.LANCZOS)
                # GIFs already use 256 colours. A private palette keeps decoded
                # memory bounded without dropping short gesture frames.
                picture = picture.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
                self.frames.append(picture)
                total += duration
                self.ends.append(total)
        self.duration_ms = total

    def index_at(self, tick):
        # A scheduled segment can intentionally hold the GIF's final frame.
        return min(bisect_right(self.ends, max(0, tick)), len(self.frames) - 1)


def builtin_asset_dir():
    # In a frozen build __file__ is inside the extracted application bundle.
    return Path(__file__).resolve().parent/'assets'/'yaoshui'


class _ClipLibrary:
    """Decode only static actor assets; never receives a camera frame or a Tk object."""
    def __init__(self, asset_dir, background=False):
        self.clips={}
        self.missing=[]
        self.ready=threading.Event()
        def decode():
            pixels=0
            try:
                for clip,filename in CLIP_FILES.items():
                    locations=[Path(asset_dir)/filename]
                    fallback=builtin_asset_dir()/filename
                    if fallback not in locations:locations.append(fallback)
                    for path in locations:
                        try:
                            # An incomplete/broken user pack can fall back to
                            # this edition's embedded clip before the sentry.
                            decoded=_Clip(path)
                            count=sum(frame.width*frame.height for frame in decoded.frames)
                            if pixels+count>MAX_LIBRARY_PIXELS:raise ValueError('Local animation pack exceeds playback limits')
                            self.clips[clip]=decoded;pixels+=count
                            break
                        except (OSError,ValueError,EOFError,Image.DecompressionBombError):
                            continue
                    if clip not in self.clips:self.missing.append(filename)
            finally:
                self.ready.set()
        if background:
            threading.Thread(target=decode,name='LocalActorAssets',daemon=True).start()
        else:
            decode()


_LOCAL_LIBRARIES={}


def forget_media_libraries(parent):
    for key in list(_LOCAL_LIBRARIES):
        if Path(key).is_relative_to(Path(parent).resolve()):
            _LOCAL_LIBRARIES.pop(key,None)


class PerformerFrameRenderer:
    """Draw a performer and a fresh observer crop, never storing observer data."""
    def __init__(self, asset_dir, motions=None, phrases=None, background=False):
        self.asset_dir = Path(asset_dir)
        if background:
            key=str(self.asset_dir.resolve())
            if key not in _LOCAL_LIBRARIES:
                _LOCAL_LIBRARIES[key]=_ClipLibrary(self.asset_dir,background=True)
            self._library=_LOCAL_LIBRARIES[key]
            while len(_LOCAL_LIBRARIES)>4:
                oldest=next(iter(_LOCAL_LIBRARIES))
                if oldest==key:break
                _LOCAL_LIBRARIES.pop(oldest)
        else:
            self._library=_ClipLibrary(self.asset_dir)
        self.missing_assets = self._library.missing
        self._clips = self._library.clips
        self._scaled = OrderedDict()
        self._scaled_bytes = 0
        self.cache_limit_bytes = 32 * 1024 * 1024
        self._warmed = set()
        self._fonts = {}
        self._backgrounds = OrderedDict()
        manifest = self.asset_dir / 'programs.json'
        self.definitions = json.loads(manifest.read_text(encoding='utf-8-sig'))
        self.configure(motions, phrases)
        # Fill the small programme's first-clip cache before animation begins.
        first = self.program.state(0)
        self._warm_clip(first.clip, self._layout((640, 400))['actor_size'])

    def wait_for_assets(self, timeout=10):
        """For command-line verification only; UI callers never wait for decoding."""
        return self._library.ready.wait(timeout)

    def media_status(self):
        count=len(self._clips);total=len(CLIP_FILES)
        if not self._library.ready.is_set():return f'人物动图：正在准备 {count}/{total}'
        if count==total:return f'人物动图：{count}/{total} 已加载'
        if not count:return f'人物动图：0/{total} · 当前显示原创哨兵，可导入人物素材'
        return f'人物动图：{count}/{total} · 缺少的动作会显示原创哨兵'

    def configure(self, motions=None, phrases=None):
        self.motions = normalize_choices(motions, [row[0] for row in MOTIONS], DEFAULT_MOTIONS)
        self.phrases = normalize_choices(phrases, [row[0] for row in PHRASES], DEFAULT_PHRASES)
        self.program = Programme(self.definitions, self.motions, self.phrases)

    @staticmethod
    def status_text(observer, protected):
        if protected:
            return '内容已暂时隐藏'
        if observer is None:
            return '效果预览 · 相机未开启'
        return '观察者画面 · 未开启内容遮挡'

    def _font(self, size, bold=False):
        size = max(8, int(size))
        key = (size, bold)
        if key not in self._fonts:
            candidates = (
                Path('C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc'),
                Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
            )
            for candidate in candidates:
                if candidate.is_file():
                    self._fonts[key] = ImageFont.truetype(str(candidate), size)
                    break
            else:
                self._fonts[key] = ImageFont.load_default()
        return self._fonts[key]

    @staticmethod
    def _layout(size):
        w, h = max(240, int(size[0])), max(160, int(size[1]))
        s = min(w / 640, h / 400)
        margin, gap = max(10, round(18*s)), max(8, round(14*s))
        top, bottom = max(37, round(55*s)), max(25, round(34*s))
        left_w = round((w - 2*margin - gap) * .38)
        card_top, card_bottom = top, h - bottom
        label_h = max(44, round(62*s))
        actor = (margin, card_top, margin + left_w, card_bottom - label_h)
        observer = (margin + left_w + gap, card_top, w-margin, card_bottom)
        return dict(size=(w,h), scale=s, margin=margin, gap=gap, top=top,
                    bottom=bottom, actor=actor, observer=observer,
                    actor_size=(actor[2]-actor[0], actor[3]-actor[1]),
                    label=(margin, actor[3]+max(5,round(9*s))))

    def _cache(self, key, value):
        old = self._scaled.pop(key, None)
        if old is not None:
            self._scaled_bytes -= old.width * old.height * 3
        self._scaled[key] = value
        self._scaled_bytes += value.width * value.height * 3
        while self._scaled_bytes > self.cache_limit_bytes and len(self._scaled) > 1:
            _, evicted = self._scaled.popitem(last=False)
            self._scaled_bytes -= evicted.width * evicted.height * 3

    def _scaled_frame(self, clip_id, index, size):
        key = (clip_id, index, tuple(size))
        cached = self._scaled.get(key)
        if cached is not None:
            self._scaled.move_to_end(key)
            return cached
        frame = self._clips[clip_id].frames[index].convert('RGB')
        frame = ImageOps.contain(frame, size, Image.Resampling.BILINEAR)
        self._cache(key, frame)
        return frame

    def _warm_clip(self, clip_id, size):
        key = (clip_id, tuple(size))
        if clip_id not in self._clips or key in self._warmed:
            return
        self._warmed.add(key)
        # Bound metadata too, when the floating window is resized frequently.
        if len(self._warmed) > 60:
            self._warmed = {key}
        # Scale the remaining frames on demand. Scaling a whole 91-frame clip
        # at a scene boundary caused a visible pause, particularly after resize.
        self._scaled_frame(clip_id, 0, size)

    def _round_image(self, target, picture, box, radius):
        x0,y0,x1,y1 = box
        image = Image.new('RGB', (x1-x0, y1-y0), SOFT)
        image.paste(picture, ((image.width-picture.width)//2, (image.height-picture.height)//2))
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).rounded_rectangle((0,0,image.width-1,image.height-1), radius, fill=255)
        target.paste(image, (x0,y0), mask)

    @staticmethod
    def _actor_canvas(picture, size):
        canvas = Image.new('RGB',size,SOFT)
        canvas.paste(picture,((size[0]-picture.width)//2,(size[1]-picture.height)//2))
        return canvas

    @staticmethod
    def _standby_actor(size, elapsed):
        """Original geometric sentry when an optional local clip is unavailable.

        Pure UI drawing: no photograph, external asset or observer pixels.
        """
        w,h=size
        image=Image.new('RGB',(w,h),SOFT)
        draw=ImageDraw.Draw(image)
        tick=float(elapsed) if isinstance(elapsed,(float,int)) and math.isfinite(elapsed) else 0
        unit=min(w,h)
        cx=w*.5+math.sin(tick*1.5)*unit*.035
        cy=h*.48+math.sin(tick*2)*unit*.018
        radius=unit*.28
        draw.rounded_rectangle((cx-radius*1.2,cy-radius,cx+radius*1.2,cy+radius),
                               radius=unit*.12,fill=PANEL,outline=BORDER,width=max(1,round(unit*.008)))
        blink=(tick%4.6)>4.42
        eye_y=cy-unit*.025
        for side in (-1,1):
            ex=cx+side*unit*.12
            if blink:
                draw.line((ex-unit*.045,eye_y,ex+unit*.045,eye_y),fill=ACCENT,width=max(2,round(unit*.018)))
            else:
                draw.ellipse((ex-unit*.042,eye_y-unit*.055,ex+unit*.042,eye_y+unit*.055),fill=ACCENT)
        draw.arc((cx-unit*.08,cy+unit*.07,cx+unit*.08,cy+unit*.18),0,180,fill=ACCENT,width=max(2,round(unit*.012)))
        draw.line((cx,cy-radius,cx,cy-radius-unit*.09),fill=ACCENT,width=max(2,round(unit*.012)))
        dot=unit*.025
        draw.ellipse((cx-dot,cy-radius-unit*.13-dot,cx+dot,cy-radius-unit*.13+dot),fill=ACCENT)
        return image

    def _base(self, layout, status, has_observer):
        key = (layout['size'],status,has_observer)
        if key in self._backgrounds:
            self._backgrounds.move_to_end(key)
            return self._backgrounds[key].copy()
        w,h = layout['size']; s = layout['scale']; m = layout['margin']
        image = Image.new('RGB',(w,h),BG); draw = ImageDraw.Draw(image)
        draw.text((m,max(9,round(15*s))), '窥贼 · 窥屏监测', font=self._font(16*s,True), fill=TEXT)
        status_font = self._font(10*s)
        status_width = draw.textbbox((0,0),status,font=status_font)[2]
        if status_width < w * .5:
            draw.text((w-m-status_width,max(12,round(19*s))),status,font=status_font,fill=MUTED)
        else:
            draw.text((m,h-layout['bottom']+max(5,round(11*s))),status,font=status_font,fill=MUTED)
        radius = max(8,round(12*s))
        for box in (layout['actor'],layout['observer']):
            draw.rounded_rectangle(box,radius,fill=SOFT,outline=BORDER,width=1)
        ox0,oy0,ox1,oy1 = layout['observer']
        if not has_observer:
            cx,cy = (ox0+ox1)/2,(oy0+oy1)/2
            # A geometric viewfinder conveys an empty scene without a fake face.
            length,arm = max(18,round(29*s)),max(6,round(9*s))
            for dx,dy in ((-1,-1),(1,-1),(-1,1),(1,1)):
                x,y = cx+dx*length,cy-18*s+dy*length
                draw.line((x,y,x-dx*arm,y),fill='#A9B4AA',width=max(1,round(2*s)))
                draw.line((x,y,x,y-dy*arm),fill='#A9B4AA',width=max(1,round(2*s)))
            text = '观察者画面'
            font = self._font(14*s,True)
            bounds = draw.textbbox((0,0),text,font=font)
            draw.text((cx-(bounds[2]-bounds[0])/2,cy+28*s),text,font=font,fill=MUTED)
            note = '预览不连接相机'
            font = self._font(10*s); bounds = draw.textbbox((0,0),note,font=font)
            draw.text((cx-(bounds[2]-bounds[0])/2,cy+50*s),note,font=font,fill=MUTED)
        footer = 'Beta · 仅供娱乐'
        draw.text((w-m-draw.textbbox((0,0),footer,font=status_font)[2],h-layout['bottom']+max(5,round(11*s))),footer,font=status_font,fill=MUTED)
        self._backgrounds[key] = image
        while len(self._backgrounds)>6:
            self._backgrounds.popitem(last=False)
        return image.copy()

    def render(self, observer, size, elapsed, protected=False):
        layout = self._layout(size)
        state = self.program.state(elapsed)
        has_observer = observer is not None
        status = self.status_text(observer, protected)
        image = self._base(layout,status,has_observer)
        s = layout['scale']; radius = max(8,round(12*s))
        has_clip=state.clip in self._clips
        if has_clip:
            self._warm_clip(state.clip,layout['actor_size'])
            clip = self._clips[state.clip]
            frame = self._scaled_frame(state.clip,clip.index_at(state.local_ms),layout['actor_size'])
            # A short dissolve softens programme cuts. It only blends the
            # performer assets; current observer pixels are never delayed.
            try:
                clock = max(0,float(elapsed)) * 1000
            except (ValueError,TypeError):
                clock = 0
            if state.step_ms < 100 and (state.step_index > 0 or clock >= self.program.duration_ms):
                prior = self.program.state((clock-state.step_ms-.001)/1000)
                if prior.clip in self._clips:
                    previous_clip = self._clips[prior.clip]
                    previous_frame = self._scaled_frame(prior.clip,previous_clip.index_at(prior.local_ms),layout['actor_size'])
                    frame = Image.blend(
                        self._actor_canvas(previous_frame,layout['actor_size']),
                        self._actor_canvas(frame,layout['actor_size']),
                        min(1,state.step_ms/100),
                    )
            self._round_image(image,frame,layout['actor'],radius)
        else:
            self._round_image(image,self._standby_actor(layout['actor_size'],elapsed),layout['actor'],radius)
        # The observer is converted and contained afresh. No cache key or stored
        # object refers to the observer, so a later empty frame cannot retain it.
        if has_observer:
            if isinstance(observer,Image.Image):
                portrait = observer.convert('RGB')
            else:
                portrait = Image.fromarray(observer).convert('RGB')
            ox0,oy0,ox1,oy1 = layout['observer']
            portrait = ImageOps.contain(portrait,(ox1-ox0,oy1-oy0),Image.Resampling.BILINEAR)
            self._round_image(image,portrait,layout['observer'],radius)
        draw = ImageDraw.Draw(image)
        x,y = layout['label']
        font = self._font(18*s,True)
        # At minimum size, fit the whole phrase rather than clipping its end.
        max_width = layout['actor_size'][0]
        while draw.textbbox((0,0),state.phrase,font=font)[2]>max_width and font.size>9:
            font = self._font(font.size-1,True)
        draw.text((x,y),state.phrase,font=font,fill=TEXT)
        label_font = self._font(10*s)
        actor_label=f'本地动图 · {state.label}' if has_clip else ('原创哨兵 · 未加载该人物动作' if self._library.ready.is_set() else '正在准备人物动图…')
        draw.text((x,y+max(22,round(29*s))),actor_label,font=label_font,fill=MUTED)
        return image
