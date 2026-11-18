"""Opt-in local HTTP integration: real Scrapling, Markdown, JSON-LD and robots.

RUN_HTTP_INTEGRATION=1 python -m pytest tests/test_fast_sources_integration.py -q
"""
import json
import os
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import fetching, service
from app.security import UnsafeURL

pytestmark = pytest.mark.skipif(os.getenv("RUN_HTTP_INTEGRATION") != "1", reason="Opt-in local HTTP integration")


async def test_real_parallel_metadata_llms_linked_jsonld_and_shared_robots(monkeypatch):
    requests = Counter()
    schema_gate = threading.Event()
    origin = ""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests[self.path] += 1
            content_type = "text/plain; charset=utf-8"
            status = 200
            if self.path == "/robots.txt":
                body = "User-agent: *\nAllow: /\n"
            elif self.path == "/":
                content_type = "text/html; charset=utf-8"
                body = '<meta property="og:site_name" content="Fixture Clinic"><meta name="description" content="Local family clinic"><link rel="alternate" type="application/ld+json" href="/business.json"><main>' + "Welcome to our neighborhood family clinic. " * 10 + "</main>"
            elif self.path == "/llms.txt":
                body = "# Fixture Clinic\n> A family clinic.\n\n## Contact\nPhone: +14155550126\n\n## Services\n- Evening appointments\n"
            elif self.path == "/business.json":
                if not schema_gate.wait(5):
                    self.send_error(504)
                    return
                content_type = "application/ld+json"
                body = json.dumps({
                    "@type": "MedicalClinic", "name": "Fixture Clinic", "description": "Structured clinic description",
                    "telephone": "+996312555123", "url": origin, "address": "18 Test Lane",
                    "timeZone": "Asia/Bishkek", "openingHours": ["Mo-Fr 09:00-18:00"],
                    "serviceType": "Consultations", "paymentAccepted": ["Cash", "Card"],
                })
            else:
                status, body = 404, "Not Found"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def fixture_only(url):
        if not url.startswith(origin):
            raise UnsafeURL("Only this fixture origin is permitted")
        return url

    snapshots = []

    async def progress(snapshot):
        snapshots.append(snapshot)
        if snapshot["profile"]["phone"] == "+14155550126":
            schema_gate.set()

    async def unexpected_browser(_):
        raise AssertionError("Fast structured sources must avoid the browser")

    monkeypatch.setattr(service, "assert_public_url", fixture_only)
    monkeypatch.setattr(fetching, "assert_public_url", fixture_only)
    monkeypatch.setattr(service, "fetch_browser", unexpected_browser)
    try:
        result = await service.import_profile(origin, True, False, on_progress=progress)
        assert any(event["profile"]["phone"] == "+14155550126" for event in snapshots)
        assert result["profile"]["phone"] == "+996312555123"
        assert result["profile"]["description"] == "Structured clinic description"
        assert result["profile"]["timezone"] == "Asia/Bishkek"
        assert result["profile"]["paymentMethods"] == ["Cash", "Card"]
        assert result["provenance"]["phone"] == [{"url": origin + "business.json", "method": "jsonld"}]
        assert requests["/robots.txt"] == 1
        assert requests["/llms.txt"] == requests["/llm.txt"] == requests["/business.json"] == 1
        assert result["lastFoundMs"] >= result["firstFoundMs"]
    finally:
        schema_gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
