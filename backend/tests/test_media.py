import numpy as np

from reel_lab.media import (
    _aggregate_video_range,
    _apply_stability_scores,
    _attention_center,
    _FrameSignal,
    _motion_metrics,
)
from reel_lab.services import derivatives


def test_attention_center_follows_salient_edge_subject() -> None:
    frame = np.full((240, 400, 3), 70, dtype=np.uint8)
    frame[55:205, 300:390] = (20, 40, 245)
    frame[80:180, 315:375] = (220, 220, 220)

    focus_x, focus_y = _attention_center(frame)

    assert focus_x > 0.58
    assert 0.25 < focus_y < 0.75


def test_dense_range_finds_exact_action_peak() -> None:
    signals = [
        _FrameSignal(
            time=index * 0.35,
            sharpness=0.8,
            brightness=0.52,
            saturation=0.5,
            action=0.92 if index == 3 else 0.18,
            stability=0.9,
            focus_x=0.5,
            focus_y=0.5,
            face_score=0.2,
        )
        for index in range(7)
    ]

    metrics = _aggregate_video_range(signals, 0.0, 2.1)

    assert round(metrics["peak_time"], 2) == 1.05
    assert metrics["action_score"] > 0.8


def test_stability_penalizes_camera_direction_changes() -> None:
    steady = [
        _FrameSignal(
            time=index,
            sharpness=0.8,
            brightness=0.5,
            saturation=0.5,
            action=0.4,
            stability=1.0,
            focus_x=0.5,
            focus_y=0.5,
            face_score=0.0,
            camera_x=0.01,
            camera_y=0.0,
        )
        for index in range(4)
    ]
    shaky = [
        _FrameSignal(
            time=index,
            sharpness=0.8,
            brightness=0.5,
            saturation=0.5,
            action=0.4,
            stability=1.0,
            focus_x=0.5,
            focus_y=0.5,
            face_score=0.0,
            camera_x=0.12 if index % 2 else -0.12,
            camera_y=0.0,
        )
        for index in range(4)
    ]

    _apply_stability_scores(steady)
    _apply_stability_scores(shaky)

    assert np.mean([signal.stability for signal in steady]) > 0.95
    assert np.mean([signal.stability for signal in shaky]) < 0.7


def test_motion_compensation_keeps_smooth_camera_shift_stable() -> None:
    rng = np.random.default_rng(7)
    previous = rng.integers(0, 256, size=(120, 160), dtype=np.uint8)
    shifted = np.roll(previous, 5, axis=1)

    action, camera_x, _camera_y, continuity = _motion_metrics(previous, shifted)

    assert continuity > 0.8
    assert camera_x > 0.01
    assert action < 0.15


def test_photo_capture_time_uses_exif_timezone(monkeypatch, tmp_path) -> None:
    class FakeExif(dict):
        def get_ifd(self, _ifd):
            return {36867: "2026:07:25 19:01:31", 36881: "+03:00"}

    class FakeImage:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getexif(self):
            return FakeExif()

    monkeypatch.setattr(derivatives.Image, "open", lambda _path: FakeImage())

    captured_at = derivatives.media_capture_time(tmp_path / "photo.heic", "photo")

    assert captured_at == "2026-07-25T16:01:31+00:00"


def test_video_capture_time_uses_quicktime_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        derivatives,
        "probe_media",
        lambda _path: {
            "format": {"tags": {"creation_time": "2026-07-25T15:50:04.000000Z"}},
            "streams": [{"codec_type": "video"}],
        },
    )

    captured_at = derivatives.media_capture_time(tmp_path / "clip.mov", "video")

    assert captured_at == "2026-07-25T15:50:04+00:00"
