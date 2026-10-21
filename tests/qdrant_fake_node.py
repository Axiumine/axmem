"""An in-process HTTP server that speaks the snapshot endpoints of a Qdrant node, for the failures a real node will not produce on cue.

A real node takes a snapshot of a small collection in milliseconds and answers; the failures this one produces are the
ones of a snapshot that takes longer than the client waits, an answer that is lost or malformed, and a node that stops
listing or deleting. Nothing here stores points: only the names of the snapshots of each collection.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class FakeQdrantNode:
    """The far end of ``NodePool`` for snapshots.

    ``create_delay`` is how long a snapshot takes: the node adds it to the collection's list when that time is over, and
    answers only then, whatever the client did meanwhile. ``create_status`` other than 200 answers with that error after
    the snapshot was made (``made_before_error``) or without it. ``malformed`` answers 200 without a name.
    ``list_fails_after`` makes the listings after that many succeed fail with 500; ``delete_fails`` makes every deletion fail.
    """

    def __init__(self) -> None:
        self.snapshots: dict[str, list[str]] = {}
        self.create_delay = 0.0
        self.create_status = 200
        self.made_before_error = True
        self.malformed = False
        self.list_fails_after: int | None = None
        self.delete_fails = False
        self.listings = 0
        self.deleted: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str]] = []
        self._made = 0
        self._release = threading.Event()
        node = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, status: int, body: dict) -> None:
                payload = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # the client gave up and closed the connection: what this node is for

            def do_GET(self):  # noqa: N802 - the name http.server calls
                path = urlsplit(self.path).path
                node.requests.append(("GET", path))
                collection = path.split("/")[2]
                node.listings += 1
                if node.list_fails_after is not None and node.listings > node.list_fails_after:
                    self._answer(500, {"status": {"error": "listing failed"}})
                    return
                names = node.snapshots.get(collection, [])
                self._answer(200, {"status": "ok", "time": 0.0, "result": [
                    {"name": name, "creation_time": "2026-01-01T00:00:00", "size": 1} for name in names
                ]})

            def do_POST(self):  # noqa: N802
                path = urlsplit(self.path).path
                node.requests.append(("POST", path))
                collection = path.split("/")[2]
                name = node.next_name(collection)
                if node.create_delay and node._release.wait(node.create_delay):
                    return  # the test is over
                if node.create_status == 200 or node.made_before_error:
                    node.snapshots.setdefault(collection, []).append(name)
                if node.create_status != 200:
                    self._answer(node.create_status, {"status": {"error": "snapshot failed"}})
                elif node.malformed:
                    self._answer(200, {"status": "ok", "time": 0.0, "result": {}})
                else:
                    self._answer(200, {"status": "ok", "time": 0.0, "result": {"name": name, "size": 1}})

            def do_DELETE(self):  # noqa: N802
                path = urlsplit(self.path).path
                node.requests.append(("DELETE", path))
                _, _, collection, _, name = path.split("/")
                if node.delete_fails:
                    self._answer(500, {"status": {"error": "delete failed"}})
                    return
                if name in node.snapshots.get(collection, []):
                    node.snapshots[collection].remove(name)
                node.deleted.append((collection, name))
                self._answer(200, {"status": "ok", "time": 0.0, "result": True})

            def log_message(self, *args):
                """Keep the test output free of one line per request."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def next_name(self, collection: str) -> str:
        self._made += 1
        return f"{collection}-{self._made}.snapshot"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> FakeQdrantNode:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._release.set()  # a snapshot still being "made" gives up
        self._server.shutdown()
        self._server.server_close()
