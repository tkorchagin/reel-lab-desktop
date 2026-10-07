import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from reel_lab.db import Base
from reel_lab.db.models import (
    Export,
    MediaAsset,
    MusicTrack,
    Project,
    ProjectAsset,
    ReelVariant,
    StoredFile,
    User,
)
from reel_lab.models import AudioMap, AudioSection, EnergyPoint, MediaCandidate
from reel_lab.services import projects


def test_filter_media_rows_supports_all_video_and_photo() -> None:
    video = (SimpleNamespace(), SimpleNamespace(media_type="video"), SimpleNamespace())
    photo = (SimpleNamespace(), SimpleNamespace(media_type="photo"), SimpleNamespace())
    rows = [video, photo]

    assert projects._filter_media_rows(rows, "all") == rows
    assert projects._filter_media_rows(rows, "video") == [video]
    assert projects._filter_media_rows(rows, "photo") == [photo]


def test_project_audio_map_uses_project_start_or_recomputed_highlight() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=60.0,
        bpm=120.0,
        beats=[float(value) for value in range(61)],
        downbeats=[0.0, 12.0, 24.0, 36.0, 48.0],
        onsets=[],
        sections=[
            AudioSection(0.0, 12.0, "intro", 0.15),
            AudioSection(12.0, 48.0, "high_energy", 0.85),
            AudioSection(48.0, 60.0, "outro", 0.25),
        ],
        energy=[
            EnergyPoint(float(value), 0.85 if 12 <= value < 48 else 0.15)
            for value in range(61)
        ],
        edit_points=[],
    )
    track = SimpleNamespace(
        analysis_json=audio_map.to_dict(),
        highlights_json={"15": {"start": 30.0, "duration": 15.0}},
    )

    automatic = projects._project_audio_map(
        SimpleNamespace(duration=15, music_start=None),
        track,
    )
    manual = projects._project_audio_map(
        SimpleNamespace(duration=15, music_start=10.5),
        track,
    )
    clamped = projects._project_audio_map(
        SimpleNamespace(duration=15, music_start=99.0),
        track,
    )

    assert automatic.source_start == 12.0
    assert manual.source_start == 10.5
    assert clamped.source_start == 45.0


def test_project_audio_map_supports_maximum_duration_override() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=80.0,
        bpm=120.0,
        beats=[float(value) for value in range(81)],
        downbeats=[0.0, 12.0, 24.0, 36.0, 48.0, 60.0],
        onsets=[],
        sections=[
            AudioSection(0.0, 24.0, "intro", 0.2),
            AudioSection(24.0, 60.0, "high_energy", 0.85),
            AudioSection(60.0, 80.0, "outro", 0.35),
        ],
        energy=[
            EnergyPoint(float(value), 0.85 if 24 <= value < 60 else 0.2)
            for value in range(81)
        ],
        edit_points=[0.0, 24.0, 60.0, 80.0],
    )
    track = SimpleNamespace(analysis_json=audio_map.to_dict(), highlights_json={})

    automatic = projects._project_audio_map(
        SimpleNamespace(duration=0, music_start=None),
        track,
        duration_override=18.0,
    )

    assert automatic.duration == 18.0
    assert automatic.source_start == 24.0


def test_maximum_reel_duration_uses_best_material_pool() -> None:
    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=120.0,
        bpm=122.0,
        beats=[0.25, 0.75],
        downbeats=[0.25],
        onsets=[],
        sections=[],
        energy=[],
        edit_points=[],
    )
    paths = [Path(f"/tmp/source-{index}.mp4") for index in range(12)]
    candidates = [
        MediaCandidate(
            id=f"candidate-{index}",
            path=str(path),
            kind="video",
            start=0.0,
            end=4.0,
            width=720,
            height=1280,
            quality=0.45 + index * 0.04,
            intensity=0.5,
            sharpness=0.8,
            brightness=0.5,
        )
        for index, path in enumerate(paths)
    ]
    track = SimpleNamespace(analysis_json=audio_map.to_dict())

    duration = projects._maximum_reel_duration(track, candidates, paths)

    assert 12.0 < duration < 90.0


def test_capture_date_sort_uses_embedded_time_and_manual_fallback(monkeypatch) -> None:
    monkeypatch.setattr(projects, "media_capture_time", lambda _path, _media_type: None)
    late = (
        SimpleNamespace(sort_order=0),
        SimpleNamespace(
            metadata_json={"captured_at": "2026-07-25T12:00:00+00:00"},
            media_type="video",
        ),
        SimpleNamespace(path="late.mov"),
    )
    early = (
        SimpleNamespace(sort_order=2),
        SimpleNamespace(
            metadata_json={"captured_at": "2026-07-25T10:00:00+00:00"},
            media_type="video",
        ),
        SimpleNamespace(path="early.mov"),
    )
    unknown = (
        SimpleNamespace(sort_order=1),
        SimpleNamespace(metadata_json={}, media_type="video"),
        SimpleNamespace(path="missing.mov"),
    )

    ordered = sorted([late, unknown, early], key=projects._capture_sort_key)

    assert [row[2].path for row in ordered] == ["early.mov", "late.mov", "missing.mov"]


def test_start_export_flushes_export_before_worker_submission(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        user = User(token_hash="test-token")
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Test")
        session.add(project)
        session.flush()
        variant = ReelVariant(
            project_id=project.id,
            variant_index=1,
            name="Beat",
            description="Test",
            score=1.0,
            seed=1,
            timeline_json=[],
        )
        session.add(variant)
        session.flush()
        user_id = user.id
        variant_id = variant.id

    @contextmanager
    def local_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    submitted = []

    class Executor:
        def submit(self, function, *args):
            submitted.append((function, args))

    monkeypatch.setattr(projects, "session_scope", local_scope)
    monkeypatch.setattr(projects, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(projects, "EXPORT_EXECUTOR", Executor())

    job_id = projects.start_export(user_id, variant_id)

    with factory() as session:
        export = session.scalar(select(Export).where(Export.job_id == job_id))
    assert export is not None
    assert submitted[0][1] == (job_id, export.id)


def test_project_preview_links_timeline_items_to_media_assets(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    source = tmp_path / "clip.mov"
    with factory.begin() as session:
        user = User(token_hash="preview-token")
        session.add(user)
        session.flush()
        stored = StoredFile(sha256="a" * 64, path=str(source), size=100)
        session.add(stored)
        session.flush()
        asset = MediaAsset(
            user_id=user.id,
            stored_file_id=stored.id,
            original_name="clip.mov",
            mime_type="video/quicktime",
            media_type="video",
        )
        track = MusicTrack(
            user_id=user.id,
            title="Track",
            source_path=str(tmp_path / "track.mp3"),
            status="ready",
        )
        session.add_all([asset, track])
        session.flush()
        project = Project(
            user_id=user.id,
            name="Preview",
            status="preview",
            music_track_id=track.id,
        )
        session.add(project)
        session.flush()
        session.add(ProjectAsset(project_id=project.id, asset_id=asset.id, selected=True))
        session.add(
            ReelVariant(
                project_id=project.id,
                variant_index=1,
                name="Beat",
                description="Test",
                score=1.0,
                seed=1,
                timeline_json=[{"path": str(source)}],
            )
        )
        user_id = user.id
        project_id = project.id
        asset_id = asset.id

    @contextmanager
    def local_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    audio_map = AudioMap(
        source_path="track.mp3",
        normalized_path="track.wav",
        analyzer="test",
        duration=30.0,
        bpm=120.0,
        beats=[0.25, 0.75],
        downbeats=[0.25],
        onsets=[],
        sections=[],
        energy=[],
        edit_points=[],
    )
    monkeypatch.setattr(projects, "session_scope", local_scope)
    monkeypatch.setattr(projects, "_project_audio_map", lambda project, track, **_kwargs: audio_map)

    preview = projects.project_preview(user_id, project_id)
    item = preview["variants"][0]["items"][0]
    debug_log = projects.project_debug_log(user_id, project_id)

    assert item["asset_id"] == asset_id
    assert item["source_name"] == "clip.mov"
    assert preview["beats"] == [0.25, 0.75]
    assert preview["downbeats"] == [0.25]
    assert preview["beat_confidence"] == 1.0
    assert debug_log["assets"][0]["path"] == "clip.mov"
    assert debug_log["music"]["source_path"] == "track.mp3"
    assert debug_log["variants"][0]["items"][0]["path"] == "clip.mov"
    assert str(tmp_path) not in json.dumps(debug_log)
