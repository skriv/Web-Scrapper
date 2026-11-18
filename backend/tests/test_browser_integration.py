"""Opt-in real browser check. No Internet, no weakening of production URL guards.

RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser_integration.py -q
Requires requirements-browser.txt and installed Playwright Chromium.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import fetching
from app.extraction import extract_html
from app.security import UnsafeURL

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in Chromium integration test")


async def test_real_js_rendering_and_final_redirect_url(monkeypatch):
    pytest.importorskip("crawl4ai")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/js":
                self.send_response(302)
                self.send_header("Location", "/rendered")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            text = "Our neighborhood dental studio welcomes families for preventive dental care and consultations. " * 12
            html = """<html><head><title>JS shell</title></head><body><main><h1>Dental studio</h1><p>TEXT</p><div id="app"></div></main>
            <script>setTimeout(()=>{let s=document.createElement('script');s.type='application/ld+json';s.textContent=JSON.stringify({'@type':'Dentist',name:'Rendered Clinic',telephone:'+14155550126'});document.head.appendChild(s);document.getElementById('app').textContent='Our studio is now ready';},100);</script></body></html>""".replace("TEXT", text)
            self.wfile.write(html.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}/"

    async def fixture_only(url):
        if not url.startswith(origin):
            raise UnsafeURL("Only this test fixture may be loaded")
        return url

    # Local fixtures are intentionally permitted only inside this test's process.
    monkeypatch.setattr(fetching, "assert_public_url", fixture_only)
    try:
        static = await fetching.fetch_http(origin + "js")
        assert extract_html(static.html, static.url).profile.businessName == "JS shell"
        rendered = await fetching.fetch_browser(origin + "js")
        extracted = extract_html(rendered.html, rendered.url)
        assert extracted.profile.businessName == "Rendered Clinic"
        assert extracted.profile.phone == "+14155550126"
        assert rendered.url == origin + "rendered"
        assert rendered.method == "browser"
        assert extracted.provenance["businessName"][0]["url"] == origin + "rendered"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
