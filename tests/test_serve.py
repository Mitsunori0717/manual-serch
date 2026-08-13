"""ポート競合まわりの動き（``serve`` の起動前チェック）。

前回のウィンドウを閉じ忘れたまま start.bat をもう一度実行すると、以前は
「winerror 10048」で落ちていた。今は起動前にポートを確かめて、自分自身が
動いていれば案内だけして終わり、他のアプリなら空きポートへずらす。
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from manualsearch.cli import _find_free_port, _port_in_use, _running_manualsearch


def _listen_on_free_port() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_port_in_use_detects_a_listening_socket():
    sock, port = _listen_on_free_port()
    try:
        assert _port_in_use("127.0.0.1", port)
    finally:
        sock.close()


def test_port_in_use_is_false_for_a_free_port():
    sock, port = _listen_on_free_port()
    sock.close()
    assert not _port_in_use("127.0.0.1", port)


def test_find_free_port_skips_the_busy_one():
    sock, port = _listen_on_free_port()
    try:
        found = _find_free_port("127.0.0.1", port)
        assert found is not None
        assert found != port
    finally:
        sock.close()


# ----------------------------------------------------------- 自分自身の見分け
def _fake_server(payload: dict) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandlerの流儀
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_recognizes_our_own_server():
    server = _fake_server({"status": "ok", "app": "manualsearch"})
    try:
        assert _running_manualsearch(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()


def test_other_apps_are_not_mistaken_for_ours():
    server = _fake_server({"hello": "world"})
    try:
        assert not _running_manualsearch(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()


def test_dead_port_is_not_ours():
    sock, port = _listen_on_free_port()
    sock.close()
    assert not _running_manualsearch(f"http://127.0.0.1:{port}/")
