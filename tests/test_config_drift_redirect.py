"""51-F1: the consumer read token must not follow a redirect to another host."""

import http.server
import threading
import urllib.error

import pytest

from py_ci_shared import config_drift_check


def _serve(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_authorization_header_never_reaches_a_redirect_target(monkeypatch):
    seen = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[tool.ruff]\n")

        def log_message(self, *a):
            pass

    target = _serve(Target)

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{target.server_port}/x")
            self.end_headers()

        def log_message(self, *a):
            pass

    origin = _serve(Origin)
    monkeypatch.setenv(config_drift_check.TOKEN_ENV, "secret-token")
    url = f"http://127.0.0.1:{origin.server_port}/"
    monkeypatch.setattr(config_drift_check, "_API_PREFIX", url)
    try:
        with pytest.raises(urllib.error.HTTPError):
            config_drift_check._fetch_pyproject(url)
    finally:
        origin.shutdown()
        target.shutdown()
    assert seen == []
