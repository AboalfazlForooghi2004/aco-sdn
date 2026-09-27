from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from controller.state import SnapshotStore


class SnapshotApiServer:
    """Dependency-free read-only JSON API for controller state."""

    def __init__(
        self,
        store: SnapshotStore,
        host: str,
        port: int,
    ) -> None:
        self.store = store
        self._server = ThreadingHTTPServer(
            (host, port),
            self._handler_type(),
        )
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def _handler_type(self):
        store = self.store

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                snapshot = asdict(store.get())
                path = urlparse(self.path).path
                routes = {
                    "/api/v1/snapshot": snapshot,
                    "/api/v1/health": {
                        "generated_at": snapshot["generated_at"],
                        "mode": snapshot["mode"],
                        "health_score": snapshot["health_score"],
                    },
                    "/api/v1/topology": {
                        "switches": snapshot["switches"],
                        "links": snapshot["links"],
                    },
                    "/api/v1/metrics": snapshot["metrics"],
                    "/api/v1/forecasts": snapshot["forecasts"],
                    "/api/v1/recommendations": snapshot[
                        "recommendations"
                    ],
                    "/api/v1/flows": snapshot["flows"],
                    "/api/v1/migrations": snapshot[
                        "migration_proposals"
                    ],
                    "/api/v1/events": snapshot["events"],
                }
                if path not in routes:
                    self._respond(
                        404, {"error": "not_found"}
                    )
                    return
                self._respond(200, routes[path])

            def _respond(self, status: int, payload) -> None:
                body = json.dumps(
                    payload,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    "application/json; charset=utf-8",
                )
                self.send_header(
                    "Cache-Control", "no-store"
                )
                self.send_header(
                    "Content-Length", str(len(body))
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args) -> None:
                return

        return Handler

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="aco-sdn-read-api",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)