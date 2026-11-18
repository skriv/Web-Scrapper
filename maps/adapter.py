"""A small, private HTTP boundary around a pinned Gosom CLI.

Only one specific business may be requested. No shell commands or search queries
are accepted, and every invocation gets its own temporary directory/process group.
"""

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request as HTTPRequest
from pydantic import BaseModel, Field

app = FastAPI(title="Private Gosom adapter", docs_url=None, redoc_url=None)
_slot = asyncio.Semaphore(1)
MAX_OUTPUT_BYTES = 2_000_000
TIMEOUT_SECONDS = min(max(float(os.getenv("MAPS_TIMEOUT_SECONDS", "100")), 10), 180)
GOOGLE_DOMAINS = {
    "google.com", "google.co.uk", "google.de", "google.fr", "google.es",
    "google.it", "google.nl", "google.ca", "google.com.au", "google.co.nz",
    "google.co.jp", "google.co.in", "google.com.br", "google.com.mx",
    "google.ru", "google.kg", "google.kz", "google.ae", "google.ch",
    "google.at", "google.be", "google.pl", "google.pt", "google.ie",
    "google.cz", "google.dk", "google.se", "google.no", "google.fi",
    "google.com.tr", "google.com.cy", "google.gr", "google.co.za",
}


class Request(BaseModel):
    url: str = Field(min_length=8, max_length=4096)


def validate_place_url(raw: str) -> str:
    if any(ord(c) < 32 for c in raw):
        raise HTTPException(422, "Could not process this source. Verify the URL and try again.")
    try:
        parsed = urlsplit(raw.strip())
        host = (parsed.hostname or "").lower()
        domain = host.removeprefix("www.").removeprefix("maps.")
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(422, "Could not process this source. Verify the URL and try again.") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or port not in (None, 443) or domain not in GOOGLE_DOMAINS):
        raise HTTPException(422, "Could not process this source. Check the URL and try again.")
    query = parse_qs(parsed.query)
    direct_place = parsed.path.startswith("/maps/place/") and bool(parsed.path[12:].strip("/"))
    identified = parsed.path in ("/maps", "/maps/", "/maps/search/", "/maps/search") and (
        bool(query.get("cid")) or bool(query.get("query_place_id"))
    )
    if not direct_place and not identified:
        raise HTTPException(422, "Could not process this source. Check the URL and try again.")
    if query.get("cid") and not re.fullmatch(r"[0-9]{1,30}", query["cid"][0]):
        raise HTTPException(422, "Could not process this source. Verify the URL and try again.")
    return raw.strip()


def verify_identity(entry: dict, url: str) -> None:
    """Never silently replace an explicitly identified place with another one."""
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    for key, raw_key in (("query_place_id", "place_id"), ("place_id", "place_id"), ("cid", "cid")):
        expected = query.get(key, [""])[0]
        if expected and str(entry.get(raw_key, "")) != expected:
            raise HTTPException(502, "Could not process this source. Check the URL and try again.")
    match = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", unquote(parsed.path), re.I)
    if match and str(entry.get("data_id", "")).lower() != match.group(1).lower():
        raise HTTPException(502, "Source data could not be verified. Review the result.")


def parse_results(data: str, url: str) -> dict:
    try:
        if not data.strip():
            raise HTTPException(404, "Could not process this source. Check the URL and try again.")
        try:
            decoded = json.loads(data)
            rows = decoded if isinstance(decoded, list) else [decoded]
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(502, "Could not process this source. Check the URL and try again.") from exc
    if len(rows) != 1:
        raise HTTPException(422, "Could not process this source. Check the URL and try again.")
    entry = rows[0]
    if not isinstance(entry, dict) or not isinstance(entry.get("title"), str) or not entry["title"].strip():
        raise HTTPException(502, "Source data could not be verified. Review the result.")
    verify_identity(entry, url)
    return entry


def kill_group(proc) -> None:
    # Killing the group also reaps Chromium children on timeout/cancellation.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def run_scraper(url: str, *, search: bool = False):
    binary = os.getenv("GOSOM_BINARY", "google-maps-scraper")
    if not shutil.which(binary):
        raise HTTPException(503, "Could not process this source. Check the URL and try again.")
    proc = None
    with tempfile.TemporaryDirectory(prefix="profile-maps-") as temp:
        folder = Path(temp)
        source, result = folder / "input.txt", folder / "results.jsonl"
        source.write_text(url + "\n", encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "-input", str(source), "-results", str(result), "-json",
                "-c", "1", "-depth", "1", "-lang", "en",
                cwd=temp, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                env={**os.environ, "DISABLE_TELEMETRY": "1"},
            )
            async def wait_bounded():
                while proc.returncode is None:
                    if result.exists() and result.stat().st_size > MAX_OUTPUT_BYTES:
                        raise HTTPException(502, "Could not process this source. Verify the URL and try again.")
                    await asyncio.sleep(0.1)
                await proc.wait()

            try:
                await asyncio.wait_for(wait_bounded(), TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise HTTPException(504, "Could not process this source. Check the URL and try again.") from exc
            if proc.returncode != 0:
                raise HTTPException(502, "Could not process this source. Check the URL and try again.")
            if not result.exists():
                raise HTTPException(404, "Could not process this source. Verify the URL and try again.")
            if result.stat().st_size > MAX_OUTPUT_BYTES:
                raise HTTPException(502, "Could not process this source. Verify the URL and try again.")
            data = result.read_text(encoding="utf-8")
            if search:
                try:
                    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
                    return [row for row in rows if isinstance(row,dict) and row.get('title')][:10]
                except ValueError as exc:
                    raise HTTPException(502, 'Invalid Maps search output') from exc
            return parse_results(data, url)
        finally:
            if proc is not None:
                kill_group(proc)
                await proc.wait()


async def cancellable_scrape(request, value, search=False):
    task=asyncio.create_task(run_scraper(value,search=search))
    try:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                raise HTTPException(499,'Caller disconnected; scraper stopped.')
            await asyncio.wait({task},timeout=0.2)
        return await task
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@app.get("/health")
async def health():
    installed = bool(shutil.which(os.getenv("GOSOM_BINARY", "google-maps-scraper")))
    if not installed:
        raise HTTPException(503, "Gosom binary unavailable")
    return {"status": "ok", "gosomVersion": "v1.18.2"}


@app.post("/extract")
async def extract(body: Request, request: HTTPRequest):
    url = validate_place_url(body.url)
    try:
        await asyncio.wait_for(_slot.acquire(), timeout=0.05)
    except asyncio.TimeoutError as exc:
        raise HTTPException(429, "Could not process this source. Check the URL and try again.") from exc
    try:
        return {"entry": await cancellable_scrape(request,url)}
    finally:
        _slot.release()


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=300)


@app.post('/search')
async def search(body: SearchRequest, request: HTTPRequest):
    if any(ord(c)<32 for c in body.query) or '://' in body.query:
        raise HTTPException(422,'Provide a business name and address, not a URL.')
    try:
        await asyncio.wait_for(_slot.acquire(), timeout=0.05)
    except TimeoutError as exc:
        raise HTTPException(429,'Google Maps is busy; try again later.') from exc
    try:
        return {'entries': await cancellable_scrape(request,body.query,search=True)}
    finally:
        _slot.release()
