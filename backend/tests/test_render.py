from reel_lab import render
from reel_lab.models import TimelineItem
from reel_lab.render import _video_filter


def test_video_filter_uses_focus_crop_and_photo_motion() -> None:
    item = TimelineItem(
        timeline_start=0.0,
        timeline_end=1.0,
        candidate_id="photo",
        path="photo.jpg",
        kind="photo",
        source_start=0.0,
        source_end=1.0,
        width=1600,
        height=900,
        score=1.0,
        focus_x=0.8,
        focus_y=0.4,
        motion="zoom-out",
    )

    graph = _video_filter(item, 720, 1280, 1.0)

    assert "gblur" not in graph
    assert "iw*0.80000" in graph
    assert "ih*0.40000" in graph
    assert "zoompan" in graph
    assert "1.10" in graph
    assert "fade=" not in graph
    assert "setparams=range=limited:color_primaries=bt709" in graph


def test_hdr_sources_are_tone_mapped_to_bt709(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        render,
        "probe_media",
        lambda _source: {
            "streams": [
                {
                    "codec_type": "video",
                    "color_transfer": "arib-std-b67",
                    "color_primaries": "bt2020",
                    "color_space": "bt2020nc",
                }
            ]
        },
    )

    color_filter = render.color_filter_for_source(tmp_path / "hdr.mov")

    assert color_filter is not None
    assert "tonemap=tonemap=hable" in color_filter
    assert "zscale=t=bt709:m=bt709:r=tv" in color_filter


def test_bt709_sources_do_not_get_an_extra_conversion(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        render,
        "probe_media",
        lambda _source: {
            "streams": [
                {
                    "codec_type": "video",
                    "color_transfer": "bt709",
                    "color_primaries": "bt709",
                    "color_space": "bt709",
                }
            ]
        },
    )

    assert render.color_filter_for_source(tmp_path / "sdr.mp4") is None
