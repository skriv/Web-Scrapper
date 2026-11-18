"""Progress contract tests use async gates rather than timing-dependent sleeps."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main, maps, service
from app.fetching import FetchError, Page
from app.models import Extraction, Profile

URL = "https://example.com/"
MAPS_URL = "https://www.google.com/maps/place/Clinic"


def html_schema(**fields):
    return '<script type="application/ld+json">' + json.dumps({"@type": "LocalBusiness", **fields}) + "</script>"


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.fixture(autouse=True)
def network_guards(monkeypatch):
    async def public_url(url):
        return url
    monkeypatch.setattr(service, "assert_public_url", public_url)
    monkeypatch.setattr(service, "robots_allowed", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(main, "slots", asyncio.Semaphore(2))


async def test_http_snapshot_arrives_before_browser_finishes_and_stays_immutable(monkeypatch, clock):
    first_seen, browser_gate = asyncio.Event(), asyncio.Event()
    snapshots = []

    async def fetch_http(url, **kwargs):
        if url.endswith(("/llms.txt", "/llm.txt")):
            return Page("", url, 404)
        clock.now = 101
        return Page("<title>Loading clinic</title><div id='root'></div>", url, 200)

    async def fetch_browser(url):
        await browser_gate.wait()
        return Page(html_schema(name="Actual Clinic", telephone="+14155550126"), url, 200, "browser")

    async def progress(snapshot):
        snapshots.append(snapshot)
        first_seen.set()

    monkeypatch.setattr(service, "fetch_http", fetch_http)
    monkeypatch.setattr(service, "fetch_browser", fetch_browser)
    task = asyncio.create_task(service.import_profile(URL, True, False, on_progress=progress))
    try:
        await asyncio.wait_for(first_seen.wait(), 1)
        assert not task.done()
        assert snapshots[0]["profile"]["businessName"] == "Loading clinic"
        assert snapshots[0]["profile"]["phone"] == ""
        clock.now = 103
        browser_gate.set()
        result = await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert result["profile"]["businessName"] == "Actual Clinic"
    assert result["profile"]["phone"] == "+14155550126"
    assert snapshots[0]["profile"]["businessName"] == "Loading clinic"
    assert snapshots[0]["profile"]["phone"] == ""
    assert snapshots[0]["provenance"]["businessName"][0]["method"] == "title"
    assert result["firstFoundMs"] == 1000
    assert result["lastFoundMs"] == 3000
    assert result["discoverySpanMs"] == 2000


async def test_warning_and_completion_do_not_advance_discovery_clock(monkeypatch, clock):
    snapshots = []

    async def fetch_http(url, **kwargs):
        if url.endswith(("/llms.txt", "/llm.txt")):
            return Page("", url, 404)
        clock.now = 101
        return Page("<title>Clinic</title>", url, 200)

    async def fail_browser(url):
        clock.now = 109
        raise FetchError("browser unavailable")

    async def progress(snapshot):
        snapshots.append(snapshot)

    monkeypatch.setattr(service, "fetch_http", fetch_http)
    monkeypatch.setattr(service, "fetch_browser", fail_browser)
    result = await service.import_profile(URL, True, False, on_progress=progress)
    assert "browser unavailable" in result["warnings"]
    assert result["elapsedMs"] == 9000
    assert result["firstFoundMs"] == result["lastFoundMs"] == 1000
    assert result["discoverySpanMs"] == 0
    assert all(snapshot["lastFoundMs"] == 1000 for snapshot in snapshots)
    assert snapshots[0]["warnings"] != result["warnings"]


async def test_input_website_alone_is_not_reported_as_data_discovery(monkeypatch, clock):
    async def fetch_http(url, **kwargs):
        if url.endswith(("/llms.txt", "/llm.txt")):
            return Page("", url, 404)
        clock.now = 104
        return Page("<html><body></body></html>", url, 200)
    monkeypatch.setattr(service, "fetch_http", fetch_http)
    result = await service.import_profile(URL, False, False)
    assert result["profile"]["website"] == URL
    assert result["firstFoundMs"] is None
    assert result["lastFoundMs"] is None
    assert result["discoverySpanMs"] is None


async def test_completed_fast_contact_is_published_without_waiting_for_slow_page(monkeypatch, clock):
    slow_started, fast_started = asyncio.Event(), asyncio.Event()
    slow_gate, fast_gate, fast_found = asyncio.Event(), asyncio.Event(), asyncio.Event()
    snapshots = []
    initial = html_schema(name="Clinic") + '<a href="/contact-a-slow">Contact</a><a href="/contact-z-fast">Contact</a>'

    async def fetch_http(url, **kwargs):
        if url.endswith(("/llms.txt", "/llm.txt")):
            return Page("", url, 404)
        if url == URL:
            clock.now = 101
            return Page(initial, url, 200)
        if url.endswith("contact-a-slow"):
            slow_started.set()
            await slow_gate.wait()
            return Page(html_schema(name="Clinic", description="A family clinic."), url, 200)
        fast_started.set()
        await fast_gate.wait()
        return Page(html_schema(name="Clinic", telephone="+14155550126"), url, 200)

    async def progress(snapshot):
        snapshots.append(snapshot)
        if snapshot["profile"]["phone"]:
            fast_found.set()

    monkeypatch.setattr(service, "fetch_http", fetch_http)
    task = asyncio.create_task(service.import_profile(URL, False, True, on_progress=progress))
    try:
        await asyncio.wait_for(slow_started.wait(), 1)
        await asyncio.wait_for(fast_started.wait(), 1)
        clock.now = 102
        fast_gate.set()
        await asyncio.wait_for(fast_found.wait(), 1)
        assert not task.done()
        assert snapshots[-1]["profile"]["phone"] == "+14155550126"
        assert snapshots[-1]["profile"]["description"] == ""
        assert snapshots[-1]["pagesFetched"] == 2
        clock.now = 105
        slow_gate.set()
        result = await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert result["profile"]["description"] == "A family clinic."
    assert result["pagesFetched"] == 3
    assert result["firstFoundMs"] == 1000
    assert result["lastFoundMs"] == 5000
    assert result["discoverySpanMs"] == 4000


async def test_maps_profile_is_published_before_website_enrichment(monkeypatch, clock):
    website_started, website_gate, maps_seen = asyncio.Event(), asyncio.Event(), asyncio.Event()
    snapshots = []

    async def extract_maps(url):
        clock.now = 101
        return Extraction(profile=Profile(businessName="Clinic", address="12 Lane", website=URL), provenance={
            "businessName": [{"url": MAPS_URL, "method": "gosom"}],
            "address": [{"url": MAPS_URL, "method": "gosom"}],
            "website": [{"url": MAPS_URL, "method": "gosom"}],
        })

    async def fetch_http(url, **kwargs):
        if url.endswith(("/llms.txt", "/llm.txt")):
            return Page("", url, 404)
        website_started.set()
        await website_gate.wait()
        return Page(html_schema(name="Clinic", description="Website description."), url, 200)

    async def progress(snapshot):
        snapshots.append(snapshot)
        if snapshot["profile"]["address"] == "12 Lane":
            maps_seen.set()

    monkeypatch.setattr(maps, "extract_maps", extract_maps)
    monkeypatch.setattr(service, "fetch_http", fetch_http)
    task = asyncio.create_task(service.import_profile(MAPS_URL, False, False, on_progress=progress))
    try:
        await asyncio.wait_for(maps_seen.wait(), 1)
        await asyncio.wait_for(website_started.wait(), 1)
        assert not task.done()
        assert snapshots[0]["profile"]["businessName"] == "Clinic"
        assert snapshots[0]["profile"]["description"] == ""
        clock.now = 104
        website_gate.set()
        result = await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert result["profile"]["address"] == "12 Lane"
    assert result["profile"]["description"] == "Website description."
    assert result["firstFoundMs"] == 1000
    assert result["lastFoundMs"] == 4000


@pytest.mark.parametrize("failure", [service.ImportError("contact page failed"), TimeoutError()])
async def test_maps_final_result_retains_website_fields_already_published_before_failure(monkeypatch, clock, failure):
    snapshots = []

    async def extract_maps(url):
        clock.now = 101
        return Extraction(profile=Profile(businessName="Clinic", address="12 Lane", website=URL), provenance={
            "businessName": [{"url": MAPS_URL, "method": "gosom"}],
            "address": [{"url": MAPS_URL, "method": "gosom"}],
        })

    async def partial_website(url, render_js, follow_links, on_progress=None):
        clock.now = 103
        partial = Extraction(profile=Profile(businessName="Clinic", phone="+14155550126"), provenance={
            "businessName": [{"url": URL, "method": "jsonld"}],
            "phone": [{"url": URL, "method": "jsonld"}],
        })
        await on_progress(partial, 1)
        clock.now = 109
        raise failure

    async def progress(snapshot):
        snapshots.append(snapshot)

    monkeypatch.setattr(maps, "extract_maps", extract_maps)
    monkeypatch.setattr(service, "import_website", partial_website)
    result = await service.import_profile(MAPS_URL, False, True, on_progress=progress)
    assert snapshots[-1]["profile"]["phone"] == "+14155550126"
    assert result["profile"]["phone"] == "+14155550126"
    assert result["profile"]["address"] == "12 Lane"
    assert result["pagesFetched"] == 2
    assert result["firstFoundMs"] == 1000
    assert result["lastFoundMs"] == 3000
    assert result["discoverySpanMs"] == 2000
    assert result["elapsedMs"] == 9000
    assert result["warnings"]


def stream_snapshot(name="Partial clinic"):
    profile = Profile(businessName=name).model_dump()
    return {
        "profile": profile, "provenance": {"businessName": [{"url": URL, "method": "title"}]},
        "warnings": [], "missingFields": [key for key, value in profile.items() if not value],
        "elapsedMs": 100, "pagesFetched": 1,
        "firstFoundMs": 100, "lastFoundMs": 100, "discoverySpanMs": 0,
    }


async def test_stream_keeps_partial_event_then_emits_recoverable_error(monkeypatch):
    async def failing_import(url, render_js=True, follow_links=True, on_progress=None):
        await on_progress(stream_snapshot())
        raise service.ImportError("site unavailable")
    monkeypatch.setattr(main, "import_profile", failing_import)
    response = await main.extract_stream(main.ImportRequest(url=URL))
    assert response.media_type == "application/x-ndjson"
    events = [json.loads(line) async for line in response.body_iterator]
    assert [event["type"] for event in events] == ["progress", "error"]
    assert events[0]["profile"]["businessName"] == "Partial clinic"
    assert events[1]["detail"] == "site unavailable"
    assert main.slots._value == 2


async def test_stream_completion_contains_final_profile_and_timing(monkeypatch):
    async def complete_import(url, render_js=True, follow_links=True, on_progress=None):
        snapshot = stream_snapshot()
        await on_progress(snapshot)
        return {**snapshot, "elapsedMs": 300}
    monkeypatch.setattr(main, "import_profile", complete_import)
    response = await main.extract_stream(main.ImportRequest(url=URL))
    events = [json.loads(line) async for line in response.body_iterator]
    assert [event["type"] for event in events] == ["progress", "complete"]
    assert events[-1]["firstFoundMs"] == events[-1]["lastFoundMs"] == 100
    assert events[-1]["discoverySpanMs"] == 0
    assert events[-1]["elapsedMs"] == 300


async def test_closing_stream_cancels_import_and_releases_slot(monkeypatch):
    cancelled, wait_forever = asyncio.Event(), asyncio.Event()

    async def importing(url, render_js=True, follow_links=True, on_progress=None):
        try:
            await on_progress(stream_snapshot())
            await wait_forever.wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "import_profile", importing)
    response = await main.extract_stream(main.ImportRequest(url=URL))
    # Constructing a response without iterating must not acquire a slot.
    assert main.slots._value == 2
    iterator = response.body_iterator
    event = json.loads(await asyncio.wait_for(anext(iterator), 1))
    assert event["type"] == "progress"
    assert main.slots._value == 1
    await asyncio.wait_for(iterator.aclose(), 1)
    await asyncio.wait_for(cancelled.wait(), 1)
    assert main.slots._value == 2


async def test_busy_stream_uses_shared_import_limit_and_emits_error(monkeypatch):
    await main.slots.acquire()
    await main.slots.acquire()
    importer = AsyncMock()
    monkeypatch.setattr(main, "import_profile", importer)
    try:
        response = await main.extract_stream(main.ImportRequest(url=URL))
        events = [json.loads(line) async for line in response.body_iterator]
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["detail"]
        importer.assert_not_awaited()
        assert main.slots._value == 0
    finally:
        main.slots.release()
        main.slots.release()


async def test_unexpected_stream_exception_does_not_expose_diagnostic(monkeypatch):
    async def importing(*args, **kwargs):
        raise RuntimeError("secret diagnostic")
    monkeypatch.setattr(main, "import_profile", importing)
    response = await main.extract_stream(main.ImportRequest(url=URL))
    events = [json.loads(line) async for line in response.body_iterator]
    assert events[-1]["type"] == "error"
    assert "secret diagnostic" not in events[-1]["detail"]
