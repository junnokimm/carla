from __future__ import annotations

import hmac
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from src.p6 import tablet_http
from src.p6.tablet import TabletProtocolError, TabletSession
from src.p6.tablet_models import JsonValue, MediaMetadata


class TabletHttpServer:
    def __init__(
        self,
        tablet: TabletSession,
        *,
        static_dir: Path | None,
        media_file: Path | None,
        bind: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._tablet = tablet
        self._static_dir = None if static_dir is None else static_dir.resolve()
        self._media_file = None if media_file is None else media_file.resolve()
        self._server = ThreadingHTTPServer((bind, port), self._handler_type())
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="p6-tablet-http", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        tablet = self._tablet
        static_dir = self._static_dir
        media_file = self._media_file

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format_string: str, *args: str) -> None:
                return

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/" and static_dir is not None:
                    self._file(static_dir / "index.html", allow_range=False)
                    return
                if path.startswith("/assets/") and static_dir is not None:
                    candidate = (
                        static_dir / unquote(path.removeprefix("/assets/"))
                    ).resolve()
                    if static_dir not in candidate.parents:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                        return
                    self._file(candidate, allow_range=False)
                    return
                if path == "/media" and media_file is not None:
                    if not self._authorized(allow_query=True):
                        self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                        return
                    self._file(media_file, allow_range=True)
                    return
                if path == "/api/state":
                    if not self._authorized():
                        self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                        return
                    self._json(HTTPStatus.OK, tablet.state_json())
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def do_POST(self) -> None:
                if not self._authorized():
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                    return
                routes = {
                    "/api/ack": self._ack,
                    "/api/touch": self._touch,
                    "/api/pause": self._pause,
                    "/api/resume": self._resume,
                    "/api/video-metadata": self._video_metadata,
                    "/api/media-ended": self._media_ended,
                    "/api/error": self._error,
                    "/api/sync-mark": self._sync_mark,
                    "/api/reset": self._reset,
                }
                action = routes.get(urlsplit(self.path).path)
                if action is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                try:
                    action(self._payload())
                except (
                    UnicodeDecodeError,
                    TabletProtocolError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                self._json(HTTPStatus.OK, tablet.state_json())

            def _authorized(self, *, allow_query: bool = False) -> bool:
                expected = tablet.token.encode()
                authorization = self.headers.get("Authorization", "")
                if authorization.startswith("Bearer ") and hmac.compare_digest(
                    authorization.removeprefix("Bearer ").encode(), expected
                ):
                    return True
                query_token = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
                return allow_query and hmac.compare_digest(
                    query_token.encode(), expected
                )

            def _payload(self) -> dict[str, tablet_http.PayloadValue]:
                if self.headers.get_content_type() != "application/json":
                    raise TabletProtocolError("Content-Type must be application/json")
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > tablet_http.MAX_JSON_BYTES:
                    raise TabletProtocolError("JSON body exceeds length limit")
                return tablet_http.parse_json_object(self.rfile.read(length))

            def _ack(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.acknowledge(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "event_type"),
                    tablet_http.text(payload, "trial_uuid"),
                    tablet_http.text(payload, "event_id"),
                    tablet_http.number(payload, "performance_ms"),
                    tablet_http.number(payload, "time_origin_ms"),
                )

            def _touch(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.touch(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "trial_uuid"),
                    tablet_http.text(payload, "event_id"),
                    tablet_http.text(payload, "circle_id"),
                    tablet_http.number(payload, "performance_ms"),
                    tablet_http.number(payload, "time_origin_ms"),
                )

            def _pause(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.pause(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "event_id"),
                    tablet_http.text(payload, "reason"),
                )

            def _resume(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.resume(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "event_id"),
                )

            def _video_metadata(
                self, payload: dict[str, tablet_http.PayloadValue]
            ) -> None:
                tablet.record_video_metadata(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "trial_uuid"),
                    tablet_http.text(payload, "event_id"),
                    MediaMetadata(
                        tablet_http.number(payload, "duration_seconds"),
                        tablet_http.boolean(payload, "muted"),
                        tablet_http.boolean(payload, "loop"),
                        tablet_http.number(payload, "playback_rate"),
                    ),
                )

            def _media_ended(
                self, payload: dict[str, tablet_http.PayloadValue]
            ) -> None:
                tablet.record_media_ended(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "trial_uuid"),
                    tablet_http.text(payload, "event_id"),
                    tablet_http.number(payload, "current_time_seconds"),
                )

            def _error(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.record_error(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.optional_text(payload, "trial_uuid"),
                    tablet_http.text(payload, "event_id"),
                    tablet_http.text(payload, "message"),
                )

            def _sync_mark(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.sync_mark(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "event_id"),
                    tablet_http.number(payload, "performance_ms"),
                    tablet_http.number(payload, "time_origin_ms"),
                )

            def _reset(self, payload: dict[str, tablet_http.PayloadValue]) -> None:
                tablet.reset_new_run(
                    tablet_http.text(payload, "run_epoch"),
                    tablet_http.text(payload, "event_id"),
                )

            def _file(self, path: Path, *, allow_range: bool) -> None:
                if not path.is_file():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                size = path.stat().st_size
                try:
                    byte_range = tablet_http.parse_byte_range(
                        self.headers.get("Range") if allow_range else None, size
                    )
                except TabletProtocolError:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                partial = allow_range and self.headers.get("Range") is not None
                self.send_response(
                    HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK
                )
                self.send_header(
                    "Content-Type",
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
                self.send_header("Accept-Ranges", "bytes")
                self.send_header(
                    "Content-Length", str(byte_range.end - byte_range.start + 1)
                )
                if partial:
                    self.send_header(
                        "Content-Range",
                        f"bytes {byte_range.start}-{byte_range.end}/{size}",
                    )
                self.end_headers()
                remaining = byte_range.end - byte_range.start + 1
                with path.open("rb") as source:
                    source.seek(byte_range.start)
                    while remaining:
                        chunk = source.read(
                            min(tablet_http.STREAM_CHUNK_BYTES, remaining)
                        )
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

            def _json(self, status: HTTPStatus, payload: dict[str, JsonValue]) -> None:
                body = tablet_http.encode_json(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler
