from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path

import pytest

from src.experiment.context import Module1Condition
from src.p6.clock import SessionOrigin
from src.p6.server import TabletHttpServer
from src.p6.tablet import TabletSession


@pytest.fixture
def served_tablet(tmp_path: Path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("index", encoding="utf-8")
    (static / "app.js").write_text("app", encoding="utf-8")
    media = tmp_path / "video.mp4"
    media.write_bytes(b"0123456789")
    tablet = TabletSession("run", SessionOrigin(0, 0), token="secret")
    tablet.configure_module_one(Module1Condition.SURT)
    server = TabletHttpServer(tablet, static_dir=static, media_file=media)
    server.start()
    try:
        yield tablet, server
    finally:
        server.close()


def _request(server: TabletHttpServer, method: str, path: str, body: bytes = b""):
    connection = HTTPConnection("127.0.0.1", server.port, timeout=2)
    headers = {"Authorization": "Bearer secret"}
    if body:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), payload


def test_index_and_assets_are_public_but_api_requires_bearer(served_tablet) -> None:
    _, server = served_tablet
    connection = HTTPConnection("127.0.0.1", server.port, timeout=2)

    connection.request("GET", "/")
    index = connection.getresponse()
    assert index.status == 200
    index.read()
    connection.request("GET", "/assets/app.js")
    asset = connection.getresponse()
    assert asset.status == 200
    asset.read()
    connection.request("GET", "/api/state")
    unauthorized = connection.getresponse()
    assert unauthorized.status == 401
    unauthorized.read()
    connection.close()


@pytest.mark.parametrize(
    "payload",
    (
        b'{"run_epoch":"epoch","event_id":"sync","performance_ms":NaN,"time_origin_ms":1}',
        b'{"run_epoch":"epoch","event_id":"sync","performance_ms":true,"time_origin_ms":1}',
    ),
)
def test_invalid_json_numbers_return_400(served_tablet, payload: bytes) -> None:
    _, server = served_tablet

    status, _, _ = _request(server, "POST", "/api/sync-mark", payload)

    assert status == 400


@pytest.mark.parametrize("range_value", ("bytes=oops-3", "bytes=9-2", "bytes=100-101"))
def test_invalid_ranges_return_416_with_content_range(
    served_tablet, range_value: str
) -> None:
    _, server = served_tablet
    connection = HTTPConnection("127.0.0.1", server.port, timeout=2)

    connection.request(
        "GET",
        "/media?token=secret",
        headers={"Range": range_value},
    )
    response = connection.getresponse()

    assert response.status == 416
    assert response.getheader("Content-Range") == "bytes */10"
    response.read()
    connection.close()


def test_every_post_requires_matching_run_epoch(served_tablet) -> None:
    tablet, server = served_tablet
    body = json.dumps(
        {
            "run_epoch": "stale",
            "event_id": "sync",
            "performance_ms": 1,
            "time_origin_ms": 2,
        }
    ).encode()

    status, _, payload = _request(server, "POST", "/api/sync-mark", body)

    assert status == 200
    event = tablet.events()[-1]
    assert event.disposition == "stale_epoch"
    assert json.loads(payload)["run_epoch"] == tablet.state().run_epoch
