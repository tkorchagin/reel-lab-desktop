import json
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from reel_lab.db.models import Base, MusicTrack
from reel_lab.services import music


def test_library_entries_use_catalog_and_discover_audio(tmp_path, monkeypatch) -> None:
    (tmp_path / "catalogued.m4a").write_bytes(b"audio")
    (tmp_path / "new_track.mp3").write_bytes(b"audio")
    collection = tmp_path / "collection"
    collection.mkdir()
    (collection / "chart_track.mp3").write_bytes(b"audio")
    (collection / "discovered.wav").write_bytes(b"audio")
    (tmp_path / "ignored.txt").write_text("not audio")
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "file": "collection/chart_track.mp3",
                        "title": "Catalog track",
                        "artist": "Artist",
                        "tags": ["sports"],
                        "chart": "Shazam Russia Top 200",
                        "chart_rank": 7,
                        "order": 20,
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(music, "AUDIO_LIBRARY_DIR", tmp_path)

    entries = music._library_entries()

    assert [entry["title"] for entry in entries] == [
        "Catalog track",
        "Catalogued",
        "Discovered",
        "New Track",
    ]
    assert entries[0]["metadata"] == {
        "artist": "Artist",
        "tags": ["sports"],
        "chart": "Shazam Russia Top 200",
        "chart_rank": 7,
        "filename": "chart_track.mp3",
        "library_path": "collection/chart_track.mp3",
    }
    assert entries[1]["sort_order"] == 30
    assert entries[2]["metadata"]["library_path"] == "collection/discovered.wav"


def test_seed_local_presets_keeps_tracks_with_the_same_title(tmp_path, monkeypatch) -> None:
    library = tmp_path / "library"
    collection = library / "chart"
    collection.mkdir(parents=True)
    (collection / "001-alone.mp3").write_bytes(b"audio")
    (collection / "002-alone.mp3").write_bytes(b"audio")
    (library / "catalog.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "chart-alone-1",
                        "file": "chart/001-alone.mp3",
                        "title": "Alone",
                        "order": 10,
                    },
                    {
                        "id": "chart-alone-2",
                        "file": "chart/002-alone.mp3",
                        "title": "Alone",
                        "order": 20,
                    },
                ]
            }
        )
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

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

    monkeypatch.setattr(music, "AUDIO_LIBRARY_DIR", library)
    monkeypatch.setattr(music, "session_scope", local_scope)
    monkeypatch.setattr(music, "MUSIC_EXECUTOR", Executor())

    music.seed_local_presets()

    with factory() as session:
        tracks = list(session.scalars(select(MusicTrack).order_by(MusicTrack.sort_order)).all())
    assert [track.title for track in tracks] == ["Alone", "Alone"]
    assert [track.metadata_json["library_path"] for track in tracks] == [
        "chart/001-alone.mp3",
        "chart/002-alone.mp3",
    ]
    assert len(submitted) == 2


def test_seed_local_presets_updates_path_without_changing_database_id(
    tmp_path, monkeypatch
) -> None:
    library = tmp_path / "library"
    collection = library / "chart"
    collection.mkdir(parents=True)
    original = collection / "original.mp3"
    moved = collection / "moved.mp3"
    original.write_bytes(b"audio")
    catalog_path = collection / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "stable-track-id",
                        "file": "original.mp3",
                        "title": "Moving Track",
                    }
                ]
            }
        )
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'moves.sqlite3'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

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

    class Executor:
        def submit(self, _function, *_args):
            return None

    monkeypatch.setattr(music, "AUDIO_LIBRARY_DIR", library)
    monkeypatch.setattr(music, "session_scope", local_scope)
    monkeypatch.setattr(music, "MUSIC_EXECUTOR", Executor())

    music.seed_local_presets()
    with factory() as session:
        original_track = session.scalar(select(MusicTrack))
        original_id = original_track.id

    original.rename(moved)
    catalog_path.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "stable-track-id",
                        "file": "moved.mp3",
                        "title": "Moving Track",
                    }
                ]
            }
        )
    )
    music.seed_local_presets()

    with factory() as session:
        tracks = list(session.scalars(select(MusicTrack)).all())
    assert len(tracks) == 1
    assert tracks[0].id == original_id
    assert tracks[0].source_path == str(moved.resolve())
