import functools
import http.client
import json
import threading
import time

import pytest

import reel_lab.server as server_module
from reel_lab.models import AudioMap, Variant
from reel_lab.server import RangeHTTPServer, RangeRequestHandler, _parse_range
from reel_lab.system import write_json


def test_parse_range_supports_seek_and_suffix() -> None:
    assert _parse_range("bytes=100-199", 1000) == (100, 199)
    assert _parse_range("bytes=900-", 1000) == (900, 999)
    assert _parse_range("bytes=-100", 1000) == (900, 999)


def test_parse_range_rejects_invalid_start() -> None:
    with pytest.raises(ValueError):
        _parse_range("bytes=1000-", 1000)


def test_server_resets_range_between_keep_alive_requests(tmp_path) -> None:
    payload = b"0123456789"
    (tmp_path / "media.bin").write_bytes(payload)
    handler = functools.partial(RangeRequestHandler, directory=str(tmp_path))
    server = RangeHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
    try:
        connection.request("HEAD", "/media.bin", headers={"Range": "bytes=2-5"})
        response = connection.getresponse()
        assert response.status == 206
        assert response.getheader("Content-Range") == "bytes 2-5/10"
        response.read()

        connection.request("GET", "/media.bin")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == payload

        connection.request("GET", "/media.bin", headers={"Range": "bytes=4-7"})
        response = connection.getresponse()
        assert response.status == 206
        assert response.read() == b"4567"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_server_exports_and_reuses_cached_variant(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    write_json(
        run_dir / "audio-map.json",
        AudioMap(
            source_path="track.wav",
            normalized_path="track.wav",
            analyzer="test",
            duration=30.0,
            bpm=120.0,
            beats=[],
            downbeats=[],
            onsets=[],
            sections=[],
            energy=[],
            edit_points=[],
        ).to_dict(),
    )
    write_json(
        run_dir / "variants.json",
        [Variant(id=3, name="Test", description="Test", seed=1, score=0.8, items=[]).to_dict()],
    )
    rendered_outputs = []
    release_render = threading.Event()

    def fake_render(run_dir, variant, audio_map, output, progress=None):
        rendered_outputs.append(output)
        if progress:
            progress(1, 2)
        release_render.wait(timeout=1)
        if progress:
            progress(2, 2)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered-mp4")
        return output

    monkeypatch.setattr(server_module, "render_variant", fake_render)
    handler = functools.partial(RangeRequestHandler, directory=str(tmp_path))
    server = RangeHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
    request_body = json.dumps({"run": "run", "variant": 3})
    try:
        connection.request(
            "POST",
            "/api/export",
            body=request_body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 202
        started = json.loads(response.read())
        assert started["status"] in {"queued", "rendering"}

        connection.request("GET", f"/api/export/{started['job_id']}")
        response = connection.getresponse()
        progress = json.loads(response.read())
        assert response.status == 200
        assert progress["status"] == "rendering"
        assert 1 <= progress["progress"] <= 90

        release_render.set()
        for _ in range(100):
            connection.request("GET", f"/api/export/{started['job_id']}")
            response = connection.getresponse()
            ready = json.loads(response.read())
            if ready["status"] == "ready":
                break
            time.sleep(0.01)
        assert ready["progress"] == 100
        assert ready["url"] == "/run/outputs/variant-03.mp4"
        assert ready["filename"] == "run-variant-03.mp4"

        connection.request(
            "POST",
            "/api/export",
            body=request_body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        cached = json.loads(response.read())
        assert response.status == 200
        assert cached["job_id"] == started["job_id"]
        assert cached["status"] == "ready"

        assert len(rendered_outputs) == 1
        connection.request("GET", "/run/outputs/variant-03.mp4")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"rendered-mp4"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
