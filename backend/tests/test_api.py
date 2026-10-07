from collections.abc import Generator
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pillow_heif import register_heif_opener
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from reel_lab.api.dependencies import database_session
from reel_lab.api.router import _music_payload, router
from reel_lab.db import Base
from reel_lab.db.models import MusicTrack
from reel_lab.models import AudioMap, AudioSection, EnergyPoint
from reel_lab.services import derivatives, music, storage

register_heif_opener()


@pytest.fixture
def client(tmp_path, monkeypatch) -> Generator[TestClient]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    uploads = tmp_path / "uploads"
    objects = tmp_path / "objects"
    previews = tmp_path / "derivatives"
    uploads.mkdir()
    objects.mkdir()
    previews.mkdir()
    monkeypatch.setattr(storage, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(storage, "OBJECTS_DIR", objects)
    monkeypatch.setattr(derivatives, "DERIVATIVES_DIR", previews)

    def override_session() -> Generator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[database_session] = override_session
    with TestClient(application) as test_client:
        yield test_client


def test_anonymous_session_is_stable_and_projects_are_private(client: TestClient) -> None:
    first_session = client.get("/api/session")
    second_session = client.get("/api/session")
    project = client.post("/api/projects", json={"name": "Trip"})

    assert first_session.status_code == 200
    assert first_session.json()["user_id"] == second_session.json()["user_id"]
    assert project.status_code == 201
    assert project.json()["processing_profile"] == "maximum"
    assert project.json()["media_filter"] == "all"
    assert project.json()["random_seed"] == 20260725
    assert project.json()["music_start"] is None

    application = client.app
    with TestClient(application) as stranger:
        response = stranger.get(f"/api/projects/{project.json()['id']}")
        delete_response = stranger.delete(f"/api/projects/{project.json()['id']}")
    assert response.status_code == 404
    assert delete_response.status_code == 404
    assert client.get(f"/api/projects/{project.json()['id']}").status_code == 200


def test_one_time_link_opens_same_user_in_another_browser(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "Chrome"}).json()
    next_path = f"/api/projects/{project['id']}"
    transfer = client.post("/api/session/transfers", json={"next_path": next_path})

    assert transfer.status_code == 201
    assert transfer.json()["expires_in"] == "600"

    application = client.app
    with TestClient(application) as chrome:
        claimed = chrome.get(transfer.json()["url"], follow_redirects=False)
        opened = chrome.get(claimed.headers["location"])

        assert claimed.status_code == 303
        assert "reel_lab_session=" in claimed.headers["set-cookie"]
        assert opened.status_code == 200
        assert opened.json()["project"]["id"] == project["id"]

    with TestClient(application) as replay:
        repeated = replay.get(transfer.json()["url"], follow_redirects=False)

    assert repeated.status_code == 404
    assert client.get(next_path).status_code == 200


def test_session_transfer_rejects_external_redirect(client: TestClient) -> None:
    response = client.post(
        "/api/session/transfers",
        json={"next_path": "//example.com/stolen"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Only local paths are allowed"


def test_project_can_be_renamed_and_deleted_without_removing_library_asset(
    client: TestClient,
) -> None:
    source = BytesIO()
    Image.new("RGB", (20, 12), "#4d8c72").save(source, format="JPEG")
    project_id = client.post("/api/projects", json={"name": "Old name"}).json()["id"]
    asset = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("photo.jpg", source.getvalue(), "image/jpeg")},
    ).json()

    renamed = client.patch(f"/api/projects/{project_id}", json={"name": "  New name  "})
    deleted = client.delete(f"/api/projects/{project_id}")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "New name"
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert all(project["id"] != project_id for project in client.get("/api/projects").json())
    assert asset["id"] in {item["id"] for item in client.get("/api/library").json()}


def test_project_resets_manual_music_start_when_duration_changes(client: TestClient) -> None:
    project_id = client.post("/api/projects", json={"name": "Music"}).json()["id"]

    manual = client.patch(f"/api/projects/{project_id}", json={"music_start": 4.5})
    changed_duration = client.patch(f"/api/projects/{project_id}", json={"duration": 15})

    assert manual.status_code == 200
    assert manual.json()["music_start"] == 4.5
    assert changed_duration.status_code == 200
    assert changed_duration.json()["music_start"] is None


def test_music_highlight_cache_refreshes_once_and_payload_uses_cache() -> None:
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
    track = MusicTrack(
        title="Cached",
        source_path="track.mp3",
        status="ready",
        analysis_json=audio_map.to_dict(),
        highlights_json={"15": {"start": 30.0, "duration": 15.0}},
    )

    music._ensure_highlight_cache(track)
    payload = _music_payload(track)

    assert payload["highlights"]["15"]["start"] == 12.0
    assert "_version" not in payload["highlights"]


def test_uploads_keep_project_order(client: TestClient) -> None:
    project_id = client.post("/api/projects", json={"name": "Sports"}).json()["id"]

    first = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("01.jpg", b"first-image", "image/jpeg")},
    )
    second = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("02.jpg", b"second-image", "image/jpeg")},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    detail = client.get(f"/api/projects/{project_id}")
    assert [asset["name"] for asset in detail.json()["assets"]] == ["01.jpg", "02.jpg"]
    assert [asset["sort_order"] for asset in detail.json()["assets"]] == [0, 1]

    reordered = client.put(
        f"/api/projects/{project_id}/assets/order",
        json={"asset_ids": [second.json()["id"], first.json()["id"]]},
    )
    detail = client.get(f"/api/projects/{project_id}")

    assert reordered.status_code == 200
    assert reordered.json() == {"ordered": 2}
    assert [asset["name"] for asset in detail.json()["assets"]] == ["02.jpg", "01.jpg"]
    assert [asset["sort_order"] for asset in detail.json()["assets"]] == [0, 1]

    invalid = client.put(
        f"/api/projects/{project_id}/assets/order",
        json={"asset_ids": [first.json()["id"], first.json()["id"]]},
    )
    assert invalid.status_code == 400


def test_heic_upload_has_browser_preview(client: TestClient) -> None:
    source = BytesIO()
    Image.new("RGB", (24, 16), "#45a97a").save(source, format="HEIF")
    project_id = client.post("/api/projects", json={"name": "HEIC"}).json()["id"]

    uploaded = client.post(
        f"/api/projects/{project_id}/media",
        files={"file": ("photo.heic", source.getvalue(), "image/heic")},
    )

    assert uploaded.status_code == 201
    assert uploaded.json()["source_url"]
    assert uploaded.json()["proxy_url"]
    assert uploaded.json()["thumbnail_url"]
    source_response = client.get(uploaded.json()["source_url"])
    assert source_response.status_code == 200
    assert source_response.headers["content-type"] == "image/heic"
    assert source_response.content == source.getvalue()
    preview = client.get(uploaded.json()["proxy_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(preview.content)) as image:
        assert image.size == (24, 16)
