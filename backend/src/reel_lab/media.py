"""Media discovery, scene detection, and lightweight quality scoring."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from scenedetect import ContentDetector, SceneManager, open_video

from reel_lab.models import MediaCandidate
from reel_lab.system import clamp, probe_media

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".3gp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}

register_heif_opener()
cv2.setNumThreads(1)
FACE_MODEL = Path(__file__).parent / "assets" / "face_detection_yunet_2026may.onnx"
_FACE_DETECTOR: object | None = None
_FACE_DETECTOR_UNAVAILABLE = False
_FACE_DETECTOR_LOCK = Lock()
ProgressCallback = Callable[[str, int, int], None]
ANALYSIS_PROFILES = {"fast", "smart", "maximum"}


@dataclass
class _FrameSignal:
    """Visual measurements at one source-video timestamp."""

    time: float
    sharpness: float
    brightness: float
    saturation: float
    action: float
    stability: float
    focus_x: float
    focus_y: float
    face_score: float
    camera_x: float = 0.0
    camera_y: float = 0.0
    continuity: float = 1.0


def discover_media(inputs: list[Path]) -> list[Path]:
    """Expand files and folders into a stable list of supported media."""
    discovered: list[Path] = []
    for raw_path in inputs:
        path = raw_path.expanduser().resolve()
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            files = [path]
        else:
            raise FileNotFoundError(path)
        for item in files:
            if item.suffix.lower() in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS:
                discovered.append(item)
    return list(dict.fromkeys(discovered))


def analyze_media(
    paths: list[Path],
    *,
    profile: str = "maximum",
    progress: ProgressCallback | None = None,
) -> list[MediaCandidate]:
    """Build scored source candidates from photos and video scenes."""
    if profile not in ANALYSIS_PROFILES:
        raise ValueError(f"Unknown analysis profile: {profile}")
    candidates: list[MediaCandidate] = []
    for index, path in enumerate(paths):
        if progress:
            progress("metadata", index, len(paths))
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            candidates.append(_analyze_image(path))
        else:
            candidates.extend(_analyze_video(path, profile=profile, progress=progress))
        if progress:
            progress("complete", index + 1, len(paths))
    if not candidates:
        raise RuntimeError("No supported photos or videos found")
    return candidates


def detect_scene_boundaries(path: Path, *, threshold: float = 25.0) -> list[float]:
    """Return hard visual cut timestamps."""
    try:
        video = open_video(str(path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=6))
        manager.detect_scenes(video, show_progress=False)
        scenes = manager.get_scene_list(start_in_scene=True)
        return [round(start.get_seconds(), 4) for start, _ in scenes[1:]]
    except (OSError, ValueError, RuntimeError):
        return []


def _analyze_video(
    path: Path,
    *,
    profile: str = "maximum",
    progress: ProgressCallback | None = None,
) -> list[MediaCandidate]:
    metadata = probe_media(path)
    stream = next(
        (item for item in metadata.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if stream is None:
        return []
    duration = float(metadata.get("format", {}).get("duration", 0.0) or 0.0)
    width = int(stream.get("width", 0) or 0)
    height = int(stream.get("height", 0) or 0)
    if duration < 0.6 or width <= 0 or height <= 0:
        return []

    scene_boundaries = detect_scene_boundaries(path) if profile != "fast" else []
    boundaries = [0.0, *scene_boundaries, duration]
    ranges: list[tuple[float, float]] = []
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        ranges.extend(_split_range(start, end))

    # Coverage windows keep enough choices for slower montage slots even when
    # the source itself is already cut very quickly.
    coverage_length = min(5.0, max(1.5, duration / 4))
    if profile == "fast":
        ranges = []
        coverage_length = min(5.0, max(2.0, duration / 3))
        stride = coverage_length
    else:
        density = 0.65 if len(ranges) < 16 else 1.6
        stride = max(1.0, coverage_length * (density if profile == "maximum" else density * 1.45))
    position = 0.0
    while position + 0.65 < duration:
        ranges.append((position, min(duration, position + coverage_length)))
        position += stride

    unique_ranges = {
        (round(start, 3), round(end, 3)) for start, end in ranges if end - start >= 0.6
    }
    cap = cv2.VideoCapture(str(path))
    candidates: list[MediaCandidate] = []
    sorted_ranges = sorted(unique_ranges)
    signals = _sample_video_signals(
        cap,
        duration,
        profile=profile,
        progress=progress,
    )
    for start, end in sorted_ranges:
        metrics = _aggregate_video_range(signals, start, end)
        candidates.append(
            MediaCandidate(
                id=_candidate_id(path, start, end),
                path=str(path),
                kind="video",
                start=start,
                end=end,
                width=width,
                height=height,
                quality=round(metrics["quality"], 4),
                intensity=round(metrics["intensity"], 4),
                sharpness=round(metrics["sharpness"], 4),
                brightness=round(metrics["brightness"], 4),
                focus_x=round(metrics["focus_x"], 4),
                focus_y=round(metrics["focus_y"], 4),
                peak_time=round(metrics["peak_time"], 4),
                stability=round(metrics["stability"], 4),
                face_score=round(metrics["face_score"], 4),
                action_score=round(metrics["action_score"], 4),
            )
        )
    cap.release()
    return candidates


def _split_range(start: float, end: float) -> list[tuple[float, float]]:
    duration = end - start
    if duration < 0.6:
        return []
    if duration <= 6.0:
        return [(start, end)]
    target = 4.5
    stride = 3.0
    result = []
    position = start
    while position + 0.6 < end:
        result.append((position, min(end, position + target)))
        position += stride
    return result


def _sample_video_signals(
    cap: cv2.VideoCapture,
    duration: float,
    *,
    profile: str,
    progress: ProgressCallback | None = None,
) -> list[_FrameSignal]:
    """Build a dense, reusable visual signal map for one video."""
    base_step = {"maximum": 0.35, "smart": 0.55, "fast": 0.9}[profile]
    step = max(base_step, duration / 900.0)
    last_time = max(0.0, duration - 0.03)
    timestamps = list(np.arange(0.0, last_time + 0.001, step))
    if not timestamps or last_time - timestamps[-1] > step * 0.55:
        timestamps.append(last_time)

    signals: list[_FrameSignal] = []
    previous_gray: np.ndarray | None = None
    progress_step = max(1, len(timestamps) // 40)
    for index, timestamp in enumerate(timestamps):
        frame = _read_frame(cap, float(timestamp))
        if frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        if profile == "fast":
            focus_x, focus_y, face_score = 0.5, 0.5, 0.0
        else:
            focus_x, focus_y, face_score = _attention_metrics(frame)
        action, camera_x, camera_y, continuity = _motion_metrics(previous_gray, gray)
        signals.append(
            _FrameSignal(
                time=float(timestamp),
                sharpness=clamp(cv2.Laplacian(gray, cv2.CV_64F).var() / 750.0),
                brightness=float(np.mean(gray) / 255.0),
                saturation=float(np.mean(hsv[:, :, 1]) / 255.0),
                action=action,
                stability=1.0,
                focus_x=focus_x,
                focus_y=focus_y,
                face_score=face_score,
                camera_x=camera_x,
                camera_y=camera_y,
                continuity=continuity,
            )
        )
        previous_gray = gray
        if progress and ((index + 1) % progress_step == 0 or index + 1 == len(timestamps)):
            progress("frames", index + 1, len(timestamps))

    if len(signals) > 1:
        signals[0].action = signals[1].action
        signals[0].camera_x = signals[1].camera_x
        signals[0].camera_y = signals[1].camera_y
        signals[0].continuity = signals[1].continuity
    _apply_stability_scores(signals)
    return signals


def _motion_metrics(
    previous_gray: np.ndarray | None,
    gray: np.ndarray,
) -> tuple[float, float, float, float]:
    """Separate residual subject motion from robust global camera motion."""
    if previous_gray is None or previous_gray.shape != gray.shape:
        return 0.0, 0.0, 0.0, 1.0
    points = cv2.goodFeaturesToTrack(
        previous_gray,
        maxCorners=120,
        qualityLevel=0.01,
        minDistance=7,
        blockSize=7,
    )
    if points is None or len(points) < 8:
        return _uncompensated_motion(previous_gray, gray)
    next_points, status, errors = cv2.calcOpticalFlowPyrLK(
        previous_gray,
        gray,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, 0.01),
    )
    if next_points is None or status is None:
        return _uncompensated_motion(previous_gray, gray)
    valid = status.reshape(-1).astype(bool)
    if errors is not None:
        valid &= errors.reshape(-1) < 35.0
    previous_points = points.reshape(-1, 2)[valid]
    current_points = next_points.reshape(-1, 2)[valid]
    if len(previous_points) < 6:
        return _uncompensated_motion(previous_gray, gray)
    transform, inliers = cv2.estimateAffinePartial2D(
        previous_points,
        current_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=500,
        confidence=0.97,
        refineIters=5,
    )
    if transform is None or inliers is None or not np.isfinite(transform).all():
        return _uncompensated_motion(previous_gray, gray)
    inlier_ratio = float(np.mean(inliers))
    feature_coverage = min(1.0, len(previous_points) / 42.0)
    continuity = clamp(0.62 * inlier_ratio + 0.38 * feature_coverage)
    aligned = cv2.warpAffine(
        previous_gray,
        transform,
        (gray.shape[1], gray.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
    residual = clamp(float(np.mean(cv2.absdiff(aligned, gray))) / 35.0)
    action = clamp(residual * (0.15 + 0.85 * continuity))
    diagonal = max(1.0, math.hypot(gray.shape[1], gray.shape[0]))
    shift_x = float(transform[0, 2])
    shift_y = float(transform[1, 2])
    return action, float(shift_x / diagonal), float(shift_y / diagonal), continuity


def _uncompensated_motion(
    previous_gray: np.ndarray,
    gray: np.ndarray,
) -> tuple[float, float, float, float]:
    residual = clamp(float(np.mean(cv2.absdiff(previous_gray, gray))) / 35.0)
    return residual * 0.15, 0.0, 0.0, 0.0


def _apply_stability_scores(signals: list[_FrameSignal]) -> None:
    """Penalize changing camera direction while preserving smooth pans."""
    if not signals:
        return
    previous_x = signals[0].camera_x
    previous_y = signals[0].camera_y
    for signal in signals:
        jitter = math.hypot(signal.camera_x - previous_x, signal.camera_y - previous_y)
        if signal.continuity < 0.12:
            signal.stability = 0.0
        else:
            continuity_score = 0.35 + 0.65 * signal.continuity
            jitter_score = 0.45 + 0.55 * math.exp(-jitter / 0.12)
            signal.stability = clamp(continuity_score * jitter_score)
        previous_x = signal.camera_x
        previous_y = signal.camera_y


def _aggregate_video_range(
    signals: list[_FrameSignal],
    start: float,
    end: float,
) -> dict[str, float]:
    """Summarize dense signals and locate the strongest exact moment in a range."""
    selected = [signal for signal in signals if start <= signal.time <= end]
    if not selected and signals:
        midpoint = (start + end) / 2
        selected = [min(signals, key=lambda signal: abs(signal.time - midpoint))]
    if not selected:
        return {
            "quality": 0.0,
            "intensity": 0.0,
            "sharpness": 0.0,
            "brightness": 0.5,
            "focus_x": 0.5,
            "focus_y": 0.5,
            "peak_time": (start + end) / 2,
            "stability": 0.0,
            "face_score": 0.0,
            "action_score": 0.0,
        }

    qualities = np.asarray([_frame_quality(signal) for signal in selected], dtype=float)
    actions = np.asarray([signal.action for signal in selected], dtype=float)
    action_peaks = _action_peaks(actions)
    stabilities = np.asarray([signal.stability for signal in selected], dtype=float)
    faces = np.asarray([signal.face_score for signal in selected], dtype=float)
    point_scores = 0.44 * qualities + 0.24 * action_peaks + 0.18 * stabilities + 0.14 * faces
    peak_index = int(np.argmax(point_scores))
    peak = selected[peak_index]
    focus_neighborhood = [signal for signal in selected if abs(signal.time - peak.time) <= 0.7]
    face_coverage = float(np.mean(faces > 0.05))
    return {
        "quality": clamp(0.72 * float(np.mean(qualities)) + 0.28 * float(qualities[peak_index])),
        "intensity": clamp(
            0.58 * float(np.mean(actions)) + 0.42 * float(np.quantile(actions, 0.8))
        ),
        "sharpness": float(np.mean([signal.sharpness for signal in selected])),
        "brightness": float(np.mean([signal.brightness for signal in selected])),
        "focus_x": float(np.median([signal.focus_x for signal in focus_neighborhood])),
        "focus_y": float(np.median([signal.focus_y for signal in focus_neighborhood])),
        "peak_time": clamp(peak.time, start, end),
        "stability": clamp(
            0.72 * float(np.mean(stabilities)) + 0.28 * float(np.quantile(stabilities, 0.25))
        ),
        "face_score": clamp(0.68 * float(np.mean(faces)) + 0.32 * face_coverage),
        "action_score": float(np.max(action_peaks)),
    }


def _frame_quality(signal: _FrameSignal) -> float:
    exposure = clamp(1.0 - abs(signal.brightness - 0.52) / 0.52)
    return clamp(0.48 * signal.sharpness + 0.32 * exposure + 0.20 * signal.saturation)


def _action_peaks(actions: np.ndarray) -> np.ndarray:
    """Reward motion that rises into a local visual event."""
    if len(actions) == 0:
        return actions
    previous = np.concatenate(([actions[0]], actions[:-1]))
    following = np.concatenate((actions[1:], [actions[-1]]))
    rise = np.maximum(0.0, actions - previous)
    prominence = np.maximum(0.0, actions - np.minimum(previous, following))
    return np.clip(0.64 * actions + 0.22 * rise + 0.14 * prominence, 0.0, 1.0)


def _read_frame(cap: cv2.VideoCapture, timestamp: float) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
    success, frame = cap.read()
    if not success:
        return None
    height, width = frame.shape[:2]
    scale = min(1.0, 420.0 / max(height, width))
    if scale < 1.0:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return frame


def _attention_center(frame: np.ndarray) -> tuple[float, float]:
    """Estimate a crop anchor from faces and lightweight visual saliency."""
    focus_x, focus_y, _face_score = _attention_metrics(frame)
    return focus_x, focus_y


def _attention_metrics(frame: np.ndarray) -> tuple[float, float, float]:
    """Return crop focus and the visual prominence of detected faces."""
    height, width = frame.shape[:2]
    scale = min(1.0, 320.0 / max(height, width))
    if scale < 1.0:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    saliency_x, saliency_y = _saliency_center(frame, gray)
    faces = _detect_faces(frame)
    if len(faces):
        weights = np.asarray(
            [face_width * face_height for _, _, face_width, face_height in faces],
            dtype=float,
        )
        face_x = float(
            np.average(
                [(x + face_width / 2) / width for x, _, face_width, _ in faces], weights=weights
            )
        )
        face_y = float(
            np.average(
                [(y + face_height / 2) / height for _, y, _, face_height in faces],
                weights=weights,
            )
        )
        focus_x = 0.84 * face_x + 0.16 * saliency_x
        focus_y = 0.78 * min(1.0, face_y + 0.10) + 0.22 * saliency_y
        visible_area = float(np.sum(weights)) / max(1.0, width * height)
        face_score = clamp(math.sqrt(visible_area / 0.12))
    else:
        focus_x, focus_y = saliency_x, saliency_y
        face_score = 0.0
    return clamp(focus_x, 0.08, 0.92), clamp(focus_y, 0.12, 0.88), face_score


def _detect_faces(frame: np.ndarray) -> list[tuple[float, float, float, float]]:
    global _FACE_DETECTOR, _FACE_DETECTOR_UNAVAILABLE
    if _FACE_DETECTOR_UNAVAILABLE or not hasattr(cv2, "FaceDetectorYN") or not FACE_MODEL.exists():
        return []
    try:
        with _FACE_DETECTOR_LOCK:
            if _FACE_DETECTOR is None:
                _FACE_DETECTOR = cv2.FaceDetectorYN.create(
                    str(FACE_MODEL),
                    "",
                    (320, 320),
                    0.62,
                    0.3,
                    5000,
                )
            height, width = frame.shape[:2]
            _FACE_DETECTOR.setInputSize((width, height))
            _, detections = _FACE_DETECTOR.detect(frame)
        if detections is None:
            return []
        return [tuple(float(value) for value in detection[:4]) for detection in detections]
    except (AttributeError, cv2.error):
        _FACE_DETECTOR_UNAVAILABLE = True
        return []


def _saliency_center(frame: np.ndarray, gray: np.ndarray) -> tuple[float, float]:
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)
    median_color = np.median(lab.reshape(-1, 3), axis=0)
    color_distance = np.linalg.norm(lab - median_color, axis=2)
    gray_float = gray.astype(np.float32)
    local_contrast = cv2.absdiff(gray_float, cv2.GaussianBlur(gray_float, (0, 0), 9.0))
    gradient_x = cv2.Sobel(gray_float, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray_float, cv2.CV_32F, 0, 1, ksize=3)
    edges = cv2.magnitude(gradient_x, gradient_y)
    score = (
        0.48 * _normalize_map(color_distance)
        + 0.32 * _normalize_map(local_contrast)
        + 0.20 * _normalize_map(edges)
    )
    score = cv2.GaussianBlur(score, (0, 0), 4.0)
    y_coordinates, x_coordinates = np.mgrid[0 : score.shape[0], 0 : score.shape[1]]
    x_normalized = x_coordinates / max(1, score.shape[1] - 1)
    y_normalized = y_coordinates / max(1, score.shape[0] - 1)
    center_prior = np.exp(
        -((x_normalized - 0.5) ** 2 / 0.32**2 + (y_normalized - 0.5) ** 2 / 0.40**2) / 2
    )
    score *= 0.68 + 0.32 * center_prior
    threshold = float(np.quantile(score, 0.72))
    weights = np.maximum(score - threshold, 0.0)
    weight_sum = float(np.sum(weights))
    if weight_sum <= 1e-6:
        return 0.5, 0.5
    return (
        float(np.sum(weights * x_normalized) / weight_sum),
        float(np.sum(weights * y_normalized) / weight_sum),
    )


def _normalize_map(values: np.ndarray) -> np.ndarray:
    minimum = float(np.min(values))
    spread = float(np.max(values) - minimum)
    if spread <= 1e-8:
        return np.zeros_like(values, dtype=np.float32)
    return (values - minimum) / spread


def _analyze_image(path: Path) -> MediaCandidate:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        image.thumbnail((1000, 1000))
        rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sharpness = clamp(cv2.Laplacian(gray, cv2.CV_64F).var() / 750.0)
    brightness = float(np.mean(gray) / 255.0)
    saturation = float(np.mean(hsv[:, :, 1]) / 255.0)
    exposure = clamp(1.0 - abs(brightness - 0.52) / 0.52)
    quality = clamp(0.55 * sharpness + 0.30 * exposure + 0.15 * saturation)
    focus_x, focus_y, face_score = _attention_metrics(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return MediaCandidate(
        id=_candidate_id(path, 0.0, math.inf),
        path=str(path),
        kind="photo",
        start=0.0,
        end=3600.0,
        width=width,
        height=height,
        quality=round(quality, 4),
        intensity=0.18,
        sharpness=round(sharpness, 4),
        brightness=round(brightness, 4),
        focus_x=round(focus_x, 4),
        focus_y=round(focus_y, 4),
        face_score=round(face_score, 4),
    )


def _candidate_id(path: Path, start: float, end: float) -> str:
    value = f"{path.resolve()}:{start:.3f}:{end:.3f}".encode()
    return hashlib.sha1(value, usedforsecurity=False).hexdigest()[:14]
