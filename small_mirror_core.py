"""Local YuNet camera core. No camera opens until CameraWorker.start().

Detection runs on the complete frame; ROIs only filter detections afterwards.
Frames remain at their captured resolution. This module never saves images,
downloads models, uploads data, or identifies a person.
"""

from __future__ import annotations

import ctypes
import importlib
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field


DETECTION_SECONDS = 0.8
COOLDOWN_SECONDS = 10.0
MAX_FRAME_AGE = 0.8
MIN_FACE_PIXELS = 12
MIN_ROI_OVERLAP = 0.60
MAX_DETECTION_WIDTH = 1280
PREVIEW_SCORE = 0.35
SENSITIVITY_SCORES = {"高": 0.55, "标准": 0.75}
RESOLUTIONS = {"1280×720": (1280, 720), "1920×1080": (1920, 1080),
               "1280×960": (1280, 960)}
MODEL_NAME = "face_detection_yunet_2023mar.onnx"
cv2 = None
Image = None
ImageTk = None


class PresenceGate:
    def __init__(self, threshold=DETECTION_SECONDS):
        self.threshold = threshold
        self.since = None

    def reset(self):
        self.since = None

    def update(self, now, present):
        if not present:
            self.reset()
            return False
        if self.since is None:
            self.since = now
        return now - self.since >= self.threshold


def normalize_rois(rois):
    """Accept a legacy single normalized tuple or a list; discard invalid areas."""
    if rois is None:
        return []
    try:
        values = list(rois)
    except TypeError:
        return []
    if len(values) == 4 and all(isinstance(value, (int, float)) for value in values):
        values = [values]
    result = []
    for candidate in values:
        try:
            if len(candidate) != 4:
                continue
            x1, y1, x2, y2 = (float(value) for value in candidate)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            continue
        left, right = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
        top, bottom = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
        roi = (left, top, right, bottom)
        if right > left and bottom > top and roi not in result:
            result.append(roi)
    return result


def pixel_roi(roi, width, height):
    if width <= 0 or height <= 0:
        return None
    normalized = normalize_rois(roi)
    if not normalized:
        return None
    left, top, right, bottom = normalized[0]
    # Normalized ROI borders use floor on the left/top and ceil on the far edge.
    return (max(0, min(width - 1, math.floor(left * width))),
            max(0, min(height - 1, math.floor(top * height))),
            max(1, min(width, math.ceil(right * width))),
            max(1, min(height, math.ceil(bottom * height))))


def matching_region(face, width, height, rois, min_overlap=MIN_ROI_OVERLAP):
    """Return one matching ROI index; overlapping areas never duplicate a face."""
    if face is None or width <= 0 or height <= 0:
        return None
    x, y, w, h = (float(value) for value in face)
    if not all(math.isfinite(value) for value in (x, y, w, h)) or min(w, h) <= 0:
        return None
    center_x, center_y = x + w / 2, y + h / 2
    best_index, best_fraction = None, -1.0
    for index, roi in enumerate(normalize_rois(rois)):
        left, top, right, bottom = pixel_roi(roi, width, height)
        if not (left <= center_x < right and top <= center_y < bottom):
            continue
        intersection = max(0.0, min(x + w, right) - max(x, left)) * max(
            0.0, min(y + h, bottom) - max(y, top))
        fraction = intersection / (w * h)
        if fraction >= min_overlap and fraction > best_fraction:
            best_index, best_fraction = index, fraction
    return best_index


def excluded_face(face, width, height, exclusions):
    """Exclude a face by location; never claim this is identity recognition."""
    if face is None:
        return False
    x, y, w, h = face
    if min(w, h) <= 0:
        return True
    cx, cy = x+w/2, y+h/2
    for roi in normalize_rois(exclusions):
        left, top, right, bottom = pixel_roi(roi, width, height)
        overlap = max(0, min(x+w, right)-max(x, left))*max(0, min(y+h, bottom)-max(y, top))
        if left <= cx < right and top <= cy < bottom or overlap/(w*h) >= .20:
            return True
    return False


def redact_exclusions(frame, bounds, exclusions):
    """Return a crop with all exclusion-area pixels blacked out, in memory only."""
    left, top, right, bottom = bounds
    result = frame[top:bottom, left:right].copy()
    height, width = frame.shape[:2]
    for roi in normalize_rois(exclusions):
        el, et, er, eb = pixel_roi(roi, width, height)
        xl, yt, xr, yb = max(left, el), max(top, et), min(right, er), min(bottom, eb)
        if xr > xl and yb > yt:
            result[yt-top:yb-top, xl-left:xr-left] = 0
    return result


def redact_owner(frame, bounds, boxes):
    """Redact only currently verified owner heads within an observer crop."""
    height,width=frame.shape[:2]
    areas=[((x-.15*w)/width,(y-.4*h)/height,(x+1.15*w)/width,(y+1.15*h)/height)
           for x,y,w,h in boxes]
    return redact_exclusions(frame,bounds,areas)


def observer_crop(face, width, height, roi, eye_mode=False):
    """Expanded head/eye crop, strictly clipped to the already matched rear ROI."""
    bounds = pixel_roi(roi, width, height)
    if face is None or bounds is None or matching_region(face, width, height, [roi]) is None:
        return None
    x, y, w, h = face
    if eye_mode:
        left, top = math.floor(x + w * 0.05), math.floor(y + h * 0.12)
        right, bottom = math.ceil(x + w * 0.95), math.ceil(y + h * 0.58)
    else:
        left, top = math.floor(x - w * 0.30), math.floor(y - h * 0.60)
        right, bottom = math.ceil(x + w * 1.30), math.ceil(y + h * 1.20)
    rl, rt, rr, rb = bounds
    result = max(rl, left), max(rt, top), min(rr, right), min(rb, bottom)
    return result if result[2] > result[0] and result[3] > result[1] else None


def fit_size(source_width, source_height, target_width, target_height):
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("画面尺寸必须大于零。")
    scale = min(target_width / source_width, target_height / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def model_path(explicit=None):
    if explicit:
        path = os.path.abspath(os.fspath(explicit))
        if os.path.isfile(path):
            return path
        raise RuntimeError(f"YuNet 本地模型不存在：{path}。程序不会联网下载或回退旧检测器。")
    directories = []
    if getattr(sys, "_MEIPASS", None):
        directories.append(os.path.join(sys._MEIPASS, "models"))
    directories.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
    for directory in directories:
        path = os.path.join(directory, MODEL_NAME)
        if os.path.isfile(path):
            return path
    raise RuntimeError(f"缺少 YuNet 本地模型：models/{MODEL_NAME}。请重新完整解压程序；不会回退旧检测器。")


def load_dependencies():
    global cv2, Image, ImageTk
    cv2 = importlib.import_module("cv2")
    Image = importlib.import_module("PIL.Image")
    ImageTk = importlib.import_module("PIL.ImageTk")
    if not hasattr(cv2, "FaceDetectorYN"):
        raise RuntimeError("当前 OpenCV 缺少 YuNet 接口，请使用附带的 OpenCV 4.12 运行环境。")


def sensitivity_threshold(sensitivity):
    if sensitivity not in SENSITIVITY_SCORES:
        raise ValueError("灵敏度请选择“高”或“标准”。")
    return SENSITIVITY_SCORES[sensitivity]


def resolution_size(resolution):
    key = str(resolution).strip().replace("x", "×").replace("X", "×")
    if key not in RESOLUTIONS:
        raise ValueError("相机分辨率请选择 1280×720、1920×1080 或 1280×960。")
    return RESOLUTIONS[key]


@dataclass
class DetectionResult:
    face: tuple | None = None
    roi: tuple | None = None
    region_index: int | None = None
    faces: list = field(default_factory=list)


@dataclass
class FacePacket:
    serial: int = 0
    timestamp: float = 0.0
    frame: object = None
    face: tuple | None = None
    roi: tuple | None = None
    region_index: int | None = None
    faces: list = field(default_factory=list)
    error: str = ""
    ready: bool = False
    capture_info: str = ""
    identity_ready: bool = False
    identity_version: int = 0
    owner_boxes: list = field(default_factory=list)
    private_boxes: list = field(default_factory=list)
    enrollment_status: dict = field(default_factory=dict)
    enrollment_samples: list = field(default_factory=list)


FramePacket = FacePacket


def _interpret_faces(rows, width, height, rois, sensitivity="高", scale_x=1.0, scale_y=1.0, exclusions=None):
    threshold = sensitivity_threshold(sensitivity)
    normalized = normalize_rois(rois)
    result = DetectionResult()
    accepted = []
    if rows is None:
        return result
    for row in rows:
        x, y, w, h, score = (float(row[0]), float(row[1]), float(row[2]),
                              float(row[3]), float(row[-1]))
        if not all(math.isfinite(value) for value in (x, y, w, h, score)) or min(w, h) <= 0:
            continue
        left, top = max(0, math.floor(x * scale_x)), max(0, math.floor(y * scale_y))
        right, bottom = min(width, math.ceil((x + w) * scale_x)), min(height, math.ceil((y + h) * scale_y))
        if right <= left or bottom <= top:
            continue
        bbox = (left, top, right - left, bottom - top)
        region = matching_region(bbox, width, height, normalized)
        excluded = excluded_face(bbox, width, height, exclusions)
        good = (not excluded and region is not None and score >= threshold and min(bbox[2:]) >= MIN_FACE_PIXELS)
        mapped = list(float(value) for value in row)
        mapped[:4] = [x*scale_x,y*scale_y,w*scale_x,h*scale_y]
        for coordinate in range(4,14):
            mapped[coordinate] *= scale_x if coordinate%2==0 else scale_y
        candidate = {"bbox": bbox, "confidence": score, "accepted": good, "row": mapped,
                     "region_index": region, "excluded": excluded}
        result.faces.append(candidate)
        if good:
            accepted.append(candidate)
    if accepted:
        selected = max(accepted, key=lambda item: (item["bbox"][2] * item["bbox"][3], item["confidence"]))
        result.face = selected["bbox"]
        result.region_index = selected["region_index"]
        result.roi = normalized[result.region_index]
    return result


class YuNetDetector:
    """One complete-frame YuNet inference, followed by ROI/confidence filtering."""

    def __init__(self, model_path=None):
        if cv2 is None:
            load_dependencies()
        path = globals()["model_path"](model_path)
        try:
            self.detector = cv2.FaceDetectorYN.create(path, "", (320, 320),
                                                     PREVIEW_SCORE, 0.3, 5000)
        except Exception as exc:
            raise RuntimeError(f"YuNet 本地模型无法加载：{exc}。程序不会回退旧检测器。") from exc
        if self.detector is None:
            raise RuntimeError("YuNet 本地检测器创建失败。程序不会回退旧检测器。")

    def detect(self, frame, rois, sensitivity="高", exclusions=None):
        sensitivity_threshold(sensitivity)
        height, width = frame.shape[:2]
        if min(height, width) <= 0:
            raise ValueError("相机画面为空。")
        target_width = min(width, MAX_DETECTION_WIDTH)
        target_height = max(1, round(height * target_width / width))
        detection_frame = frame if target_width == width else cv2.resize(
            frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        try:
            self.detector.setInputSize((target_width, target_height))
            _status, rows = self.detector.detect(detection_frame)
        except Exception as exc:
            raise RuntimeError(f"YuNet 本地检测失败：{exc}。请检查模型及运行环境。") from exc
        return _interpret_faces(rows, width, height, rois, sensitivity,
                                width / target_width, height / target_height, exclusions)


class CameraWorker(threading.Thread):
    def __init__(self, index, owner_samples=()):
        super().__init__(daemon=True, name="LocalYuNetCamera")
        self.index = int(index)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.packet = FacePacket()
        self.rois = []
        self.exclusions = []
        self.include_profile = True
        self.sensitivity = "高"
        self.resolution = "1280×720"
        self.capture_info = ""
        self.owner_samples = list(owner_samples)
        self.identity_version = 0
        self.commands = []

    def identity_command(self, action, value=None):
        with self.lock:
            if action == 'templates':
                self.identity_version += 1
                self.owner_samples = list(value)
                self.packet = FacePacket(serial=self.packet.serial+1)
            self.commands.append((action,value,self.identity_version))

    def configure(self, rois, include_profile=True, sensitivity="高", resolution="1280×720", exclusions=None):
        normalized = normalize_rois(rois)
        sensitivity_threshold(sensitivity)
        resolution_size(resolution)
        with self.lock:
            self.rois = normalized
            self.exclusions = normalize_rois(exclusions)
            # YuNet handles pose in one model; no Haar/profile-only pipeline.
            self.include_profile = bool(include_profile)
            self.sensitivity = sensitivity
            self.resolution = str(resolution).replace("x", "×").replace("X", "×")

    def latest(self):
        with self.lock:
            return self.packet

    def publish(self, frame=None, detection=None, error="", ready=False, identity_ready=False,
                identity_version=0, owner_boxes=(), private_boxes=(), enrollment_status=None, enrollment_samples=()):
        detection = detection or DetectionResult()
        with self.lock:
            self.packet = FacePacket(serial=self.packet.serial + 1, timestamp=time.monotonic(),
                                     frame=frame, face=detection.face, roi=detection.roi,
                                     region_index=detection.region_index, faces=detection.faces,
                                     error=error, ready=ready, capture_info=self.capture_info,
                                     identity_ready=identity_ready,identity_version=identity_version,
                                     owner_boxes=list(owner_boxes),private_boxes=list(private_boxes),enrollment_status=enrollment_status or {},
                                     enrollment_samples=list(enrollment_samples))

    def stop(self):
        self.stop_event.set()
        self.publish()  # Drop the live frame immediately; the driver releases in finally.

    @staticmethod
    def _request_capture(capture, resolution):
        width, height = resolution_size(resolution)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FPS, 15)
        try:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        try:
            accepted = bool(capture.set(cv2.CAP_PROP_ZOOM, 0))
            actual_zoom = capture.get(cv2.CAP_PROP_ZOOM)
            actual = f"，设备回报 {actual_zoom:g}" if math.isfinite(actual_zoom) else ""
            zoom = f"变焦 0 请求{'已接受' if accepted else '未接受/未支持'}{actual}"
        except Exception:
            zoom = "变焦 0 请求未支持"
        return zoom

    def run(self):
        capture = None
        try:
            detector = YuNetDetector()
            from small_mirror_identity import SFace, OwnerTracker, Enrollment
            recognizer = SFace(cv2)
            tracker = OwnerTracker(recognizer,self.owner_samples)
            enrollment = None
            identity_version = 0
            if self.stop_event.is_set():
                return
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            capture = cv2.VideoCapture(self.index, backend)
            if not capture.isOpened() and not self.stop_event.is_set():
                capture.release()
                capture = cv2.VideoCapture(self.index)
            if not capture.isOpened():
                raise RuntimeError("无法开启摄像头。请检查相机编号、系统权限，或关闭占用相机的应用。")
            applied_resolution = None
            zoom_info = ""
            failures = 0
            while not self.stop_event.is_set():
                started = time.monotonic()
                with self.lock:
                    rois = list(self.rois)
                    exclusions = list(self.exclusions)
                    sensitivity, resolution = self.sensitivity, self.resolution
                    commands,self.commands = self.commands,[]
                for action,value,version in commands:
                    if action=='templates':
                        tracker.set_samples(value);enrollment=None;identity_version=version
                    elif action=='begin':
                        enrollment=Enrollment(recognizer,value['track_id'],automatic=value.get('auto',False)) if isinstance(value,dict) else Enrollment(recognizer,value)
                    elif action=='capture' and enrollment:enrollment.capture()
                    elif action=='retry' and enrollment:enrollment.retry()
                    elif action=='cancel':enrollment=None
                if resolution != applied_resolution:
                    zoom_info = self._request_capture(capture, resolution)
                    applied_resolution = resolution
                ok, frame = capture.read()
                if self.stop_event.is_set():
                    break
                if not ok or frame is None:
                    failures += 1
                    self.publish(error="暂时无法读取相机画面，正在重试。")
                    if failures >= 5:
                        raise RuntimeError("相机画面已中断，请关闭后重新开启摄像头。")
                    self.stop_event.wait(0.1)
                    continue
                failures = 0
                height, width = frame.shape[:2]
                self.capture_info = (f"实际 {width}×{height}（请求 {resolution}） · 软件无裁切 · "
                                     f"{zoom_info} · 镜头视角由硬件决定")
                # Identity replaces the legacy spatial owner exclusion.
                detection = detector.detect(frame, rois, sensitivity, [])
                tracker.update(frame,detection.faces,time.monotonic())
                owner_boxes = tracker.owner_boxes(time.monotonic())
                private_boxes = owner_boxes+[item['bbox'] for item in detection.faces if item['identity']!='other']
                eligible = []
                for item in detection.faces:
                    item['accepted'] = bool(item['accepted'] and item['identity']=='other'
                                            and tracker.samples and enrollment is None)
                    if item['accepted']:eligible.append(item)
                selected = max(eligible,key=lambda item:item['bbox'][2]*item['bbox'][3],default=None)
                detection.face = selected['bbox'] if selected else None
                detection.region_index = selected['region_index'] if selected else None
                detection.roi = rois[detection.region_index] if selected else None
                if enrollment:enrollment.update(frame,detection.faces,time.monotonic())
                if self.stop_event.is_set():
                    break
                self.publish(frame=frame, detection=detection, ready=True, identity_ready=bool(tracker.samples),
                             identity_version=identity_version,owner_boxes=owner_boxes,
                             private_boxes=private_boxes,
                             enrollment_status=enrollment.status() if enrollment else {},
                             enrollment_samples=enrollment.samples if enrollment and enrollment.status()['done'] else [])
                self.stop_event.wait(max(0.0, 1 / 6 - (time.monotonic() - started)))
        except Exception as exc:
            self.publish(error=str(exc) or "相机发生未知错误。")
        finally:
            if capture is not None:
                capture.release()


def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


enable_dpi = enable_dpi_awareness


def desktop_bounds(root):
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                user32.GetSystemMetrics(78), user32.GetSystemMetrics(79),
                user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    width, height = root.winfo_screenwidth(), root.winfo_screenheight()
    return 0, 0, width, height, width, height
