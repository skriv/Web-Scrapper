"""Fast-source integration tests use explicit gates, never timing-dependent sleeps."""

import asyncio
import json
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

import pytest

from app import fetching, service
from app.fetching import FetchError, Page

URL = "https://example.com/"
AUXILIARY_URLS = {"https://example.com/llms.txt", "https://example.com/llm.txt"}


def schema(**fields):
    return '<script type="application/ld+json">' + json.dumps({"@type": "LocalBusiness", **fields}) + "</script>"


@pytest.fixture(autouse=True)
def public_network_guards(monkeypatch):
    async def public_url(url, **kwargs):
        return url
    monkeypatch.setattr(service, "assert_public_url", public_url)
    monkeypatch.setattr(service, "robots_allowed", AsyncMock(return_value=(True, None)))


def install_http(monkeypatch, implementation):
    # Cover both service's import and auxiliary helpers using fetching's name.
    monkeypatch.setattr(service, "fetch_http", implementation)
    monkeypatch.setattr(fetching, "fetch_http", implementation)


async def stop(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_main_http_data_is_published_without_waiting_for_slow_llms_files(monkeypatch):
    auxiliary_started, main_found, release_auxiliary = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requested_auxiliary = set()
    snapshots = []

    async def fetch(url, **kwargs):
        if url == URL:
            return Page(schema(name="Clinic", telephone="+14155550126"), url, 200)
        assert url in AUXILIARY_URLS
        requested_auxiliary.add(url)
        if requested_auxiliary == AUXILIARY_URLS:
            auxiliary_started.set()
        await release_auxiliary.wait()
        return Page("", url, 404)

    async def progress(snapshot):
        snapshots.append(snapshot)
        if snapshot["profile"]["phone"]:
            main_found.set()

    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, False, False, on_progress=progress))
    try:
        await asyncio.wait_for(auxiliary_started.wait(), 1)
        await asyncio.wait_for(main_found.wait(), 1)
        assert not release_auxiliary.is_set()
        assert snapshots[-1]["profile"]["businessName"] == "Clinic"
        assert snapshots[-1]["profile"]["phone"] == "+14155550126"
        release_auxiliary.set()
        result = await asyncio.wait_for(task, 1)
        assert result["profile"]["phone"] == "+14155550126"
    finally:
        await stop(task)


@pytest.mark.parametrize("auxiliary_failure", ["404", "network"])
async def test_optional_auxiliary_failures_are_quiet_and_do_not_erase_main_data(monkeypatch, auxiliary_failure):
    requested = []

    async def fetch(url, **kwargs):
        requested.append(url)
        if url == URL:
            return Page(schema(name="Clinic", telephone="+14155550126"), url, 200)
        assert url in AUXILIARY_URLS
        if auxiliary_failure == "network":
            raise FetchError("auxiliary-only failure")
        return Page("missing", url, 404)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert set(requested) == {URL, *AUXILIARY_URLS}
    assert result["profile"]["businessName"] == "Clinic"
    assert result["profile"]["phone"] == "+14155550126"
    assert not any("auxiliary-only failure" in warning or "llms.txt" in warning or "llm.txt" in warning for warning in result["warnings"])


async def test_cancelling_import_cleans_up_all_pending_initial_fetches(monkeypatch):
    all_started = asyncio.Event()
    never_release = asyncio.Event()
    started, cancelled = set(), set()

    async def fetch(url, **kwargs):
        started.add(url)
        if started == {URL, *AUXILIARY_URLS}:
            all_started.set()
        try:
            await never_release.wait()
        finally:
            cancelled.add(url)

    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, True, False))
    try:
        await asyncio.wait_for(all_started.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert cancelled == {URL, *AUXILIARY_URLS}
    finally:
        await stop(task)


async def test_auxiliary_paths_respect_robots_and_input_url_alone_is_not_a_discovery(monkeypatch):
    checked, fetched = [], []

    async def robots(url, **kwargs):
        checked.append(url)
        return (url not in AUXILIARY_URLS), None

    async def fetch(url, **kwargs):
        fetched.append(url)
        return Page("<html><body></body></html>", url, 200)

    monkeypatch.setattr(service, "robots_allowed", robots)
    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert AUXILIARY_URLS.issubset(checked)
    assert fetched == [URL]
    assert result["profile"]["website"] == URL
    assert result["firstFoundMs"] is None
    assert result["lastFoundMs"] is None
    assert result["discoverySpanMs"] is None


async def test_declared_jsonld_fetches_at_most_two_same_origin_documents(monkeypatch):
    requested = []
    linked = ["https://example.com/schema-one.json", "https://example.com/schema-two.json"]
    initial = (
        '<title>Clinic</title><script type="application/ld+json" src="/schema-one.json"></script>'
        '<link type="application/ld+json" href="https://other.example/foreign.json" rel="alternate">'
        '<link type="application/ld+json" href="/schema-two.json" rel="alternate">'
        '<script type="application/ld+json" src="/schema-three.json"></script>'
    )

    async def fetch(url, **kwargs):
        requested.append(url)
        if url == URL:
            return Page(initial, url, 200)
        if url in AUXILIARY_URLS:
            return Page("", url, 404)
        assert url in linked
        assert kwargs["allow_json"] is True
        assert kwargs["timeout"] == 2
        assert kwargs["max_bytes"] == 262144
        fields = {"telephone": "+14155550126"} if url == linked[0] else {"description": "A local clinic."}
        return Page(json.dumps({"@type": "LocalBusiness", "name": "Clinic", **fields}), url, 200)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert {url for url in requested if urlsplit(url).path.endswith(".json")} == set(linked)
    assert result["profile"]["phone"] == "+14155550126"
    assert result["profile"]["description"] == "A local clinic."
    assert result["provenance"]["phone"] == [{"url": linked[0], "method": "jsonld"}]


async def test_declared_jsonld_robots_denial_is_checked_before_fetch(monkeypatch):
    linked = "https://example.com/schema.json"
    checked, requested = [], []

    async def robots(url, **kwargs):
        checked.append(url)
        return url != linked, None

    async def fetch(url, **kwargs):
        requested.append(url)
        if url == URL:
            return Page('<title>Clinic</title><script type="application/ld+json" src="/schema.json"></script>', url, 200)
        assert url in AUXILIARY_URLS
        return Page("", url, 404)

    monkeypatch.setattr(service, "robots_allowed", robots)
    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert linked in checked
    assert linked not in requested
    assert result["profile"]["businessName"] == "Clinic"


async def test_llms_fields_are_published_before_slow_main_http_finishes(monkeypatch):
    main_started, release_main, llms_found = asyncio.Event(), asyncio.Event(), asyncio.Event()
    snapshots = []

    async def fetch(url, **kwargs):
        if url == URL:
            main_started.set()
            await release_main.wait()
            return Page("<title>Clinic</title>", url, 200)
        if url.endswith("/llms.txt"):
            return Page("Business Name: Clinic\nPhone: +14155550126\nDescription: A local clinic.", url, 200)
        return Page("", url, 404)

    async def progress(snapshot):
        snapshots.append(snapshot)
        if snapshot["profile"]["phone"]:
            llms_found.set()

    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, False, False, on_progress=progress))
    try:
        await asyncio.wait_for(main_started.wait(), 1)
        await asyncio.wait_for(llms_found.wait(), 1)
        assert not task.done()
        assert not release_main.is_set()
        assert snapshots[-1]["profile"]["businessName"] == "Clinic"
        assert snapshots[-1]["provenance"]["phone"] == [{"url": "https://example.com/llms.txt", "method": "llms_txt"}]
        release_main.set()
        result = await asyncio.wait_for(task, 1)
        assert result["profile"]["phone"] == "+14155550126"
    finally:
        await stop(task)


async def test_singular_llm_alias_is_used_when_standard_file_is_missing(monkeypatch):
    requested = []

    async def fetch(url, **kwargs):
        requested.append(url)
        if url == URL:
            return Page("<title>Clinic</title>", url, 200)
        if url.endswith("/llm.txt"):
            return Page("Business Name: Clinic\nPhone: +14155550126", url, 200)
        return Page("", url, 404)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert AUXILIARY_URLS.issubset(requested)
    assert result["profile"]["phone"] == "+14155550126"
    assert result["provenance"]["phone"] == [{"url": "https://example.com/llm.txt", "method": "llms_txt"}]


@pytest.mark.parametrize("first", ["main", "llms"])
async def test_strong_schema_wins_over_llms_hints_independent_of_arrival_order(monkeypatch, first):
    release = {"main": asyncio.Event(), "llms": asyncio.Event()}
    first_found = asyncio.Event()
    expected_phone = "+14155550126" if first == "main" else "+14155550127"

    async def fetch(url, **kwargs):
        if url == URL:
            await release["main"].wait()
            return Page(schema(name="Clinic", description="Authoritative schema.", telephone="+14155550126", address="12 Lane"), url, 200)
        if url.endswith("/llms.txt"):
            await release["llms"].wait()
            return Page("Business Name: Clinic\nDescription: Weaker hint.\nPhone: +14155550127\nAddress: 12 Lane", url, 200)
        return Page("", url, 404)

    async def progress(snapshot):
        if snapshot["profile"]["phone"] == expected_phone:
            first_found.set()

    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, False, False, on_progress=progress))
    try:
        release[first].set()
        await asyncio.wait_for(first_found.wait(), 1)
        release["llms" if first == "main" else "main"].set()
        result = await asyncio.wait_for(task, 1)
        assert result["profile"]["phone"] == "+14155550126"
        assert result["profile"]["description"] == "Authoritative schema."
        assert result["provenance"]["phone"] == [{"url": URL, "method": "jsonld"}]
    finally:
        await stop(task)


async def test_llms_for_another_branch_does_not_fill_local_contact_fields(monkeypatch):
    async def fetch(url, **kwargs):
        if url == URL:
            return Page(schema(name="Clinic", address="12 Lane"), url, 200)
        if url.endswith("/llms.txt"):
            return Page("Business Name: Clinic\nAddress: 99 Other Street\nPhone: +14155550127\nOpening Hours: Mo-Fr 09:00-17:00", url, 200)
        return Page("", url, 404)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert result["profile"]["address"] == "12 Lane"
    assert result["profile"]["phone"] == ""
    assert result["profile"]["openingHours"] == []


@pytest.mark.parametrize("source", ["jsonld", "llms", "visible_metadata"])
async def test_browser_skips_sufficient_fast_sources_even_with_short_schema_html(monkeypatch, source):
    browser = AsyncMock(side_effect=AssertionError("unnecessary browser fallback"))
    monkeypatch.setattr(service, "fetch_browser", browser)

    async def fetch(url, **kwargs):
        if url == URL:
            if source == "jsonld":
                return Page(schema(name="Clinic", telephone="+14155550126"), url, 200)
            if source == "visible_metadata":
                return Page('<meta property="og:site_name" content="Clinic"><p>' + "Our local clinic welcomes patients. " * 8 + "</p>", url, 200)
            return Page("<div id='root'></div>", url, 200)
        if source == "llms" and url.endswith("/llms.txt"):
            return Page("# Clinic\n> Local clinic.\n## Contact\nPhone: +14155550126", url, 200)
        return Page("", url, 404)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, True, False)
    assert result["profile"]["businessName"] == "Clinic"
    browser.assert_not_awaited()


async def test_sparse_js_fallback_waits_for_cheap_sources_and_llms_progress_is_not_delayed_by_browser(monkeypatch):
    llms_started, release_llms = asyncio.Event(), asyncio.Event()
    main_found, llms_found, browser_started, release_browser = (asyncio.Event() for _ in range(4))
    browser = AsyncMock()
    snapshots = []

    async def fetch(url, **kwargs):
        if url == URL:
            return Page("<title>Clinic</title><div id='root'></div>", url, 200)
        if url.endswith("/llms.txt"):
            llms_started.set()
            await release_llms.wait()
            return Page("Business Name: Clinic\nDescription: A local clinic.", url, 200)
        return Page("", url, 404)

    async def render(url, **kwargs):
        browser_started.set()
        await release_browser.wait()
        return Page(schema(name="Clinic", telephone="+14155550126"), url, 200, "browser")

    async def progress(snapshot):
        snapshots.append(snapshot)
        if snapshot["profile"]["businessName"]:
            main_found.set()
        if snapshot["profile"]["description"] == "A local clinic.":
            llms_found.set()

    browser.side_effect = render
    monkeypatch.setattr(service, "fetch_browser", browser)
    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, True, False, on_progress=progress))
    try:
        await asyncio.wait_for(llms_started.wait(), 1)
        await asyncio.wait_for(main_found.wait(), 1)
        browser.assert_not_awaited()
        release_llms.set()
        await asyncio.wait_for(llms_found.wait(), 1)
        await asyncio.wait_for(browser_started.wait(), 1)
        assert not task.done()
        assert snapshots[-1]["profile"]["description"] == "A local clinic."
        assert snapshots[-1]["profile"]["phone"] == ""
        release_browser.set()
        result = await asyncio.wait_for(task, 1)
        assert result["profile"]["phone"] == "+14155550126"
    finally:
        await stop(task)


async def test_auxiliary_fetches_have_explicit_small_network_and_body_budgets(monkeypatch):
    limits = {}

    async def fetch(url, **kwargs):
        if url == URL:
            return Page(schema(name="Clinic", telephone="+14155550126"), url, 200)
        limits[url] = kwargs
        return Page("", url, 404)

    install_http(monkeypatch, fetch)
    await service.import_profile(URL, False, False)
    assert set(limits) == AUXILIARY_URLS
    for budget in limits.values():
        assert budget["timeout"] == 2
        assert budget["max_bytes"] == 262144
        assert not budget.get("allow_json", False)


async def test_cancellation_also_cleans_up_jsonld_tasks_discovered_after_main_http(monkeypatch):
    waiting = {"https://example.com/llms.txt", "https://example.com/schema.json"}
    started, cancelled = set(), set()
    all_started, never_release = asyncio.Event(), asyncio.Event()

    async def fetch(url, **kwargs):
        if url == URL:
            return Page('<title>Clinic</title><script type="application/ld+json" src="/schema.json"></script>', url, 200)
        if url not in waiting:
            return Page("", url, 404)
        started.add(url)
        if started == waiting:
            all_started.set()
        try:
            await never_release.wait()
        finally:
            cancelled.add(url)

    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, False, False))
    try:
        await asyncio.wait_for(all_started.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert cancelled == waiting
    finally:
        await stop(task)


async def test_real_robots_cache_shares_one_document_but_evaluates_each_requested_path(monkeypatch):
    cache = {}
    requested = []
    loading, release = asyncio.Event(), asyncio.Event()

    async def fetch(url, **kwargs):
        requested.append(url)
        loading.set()
        await release.wait()
        return Page("User-agent: ProfileKit\nDisallow: /private\nDisallow: /llms.txt\n", url, 200)

    monkeypatch.setattr(fetching, "fetch_http", fetch)
    urls = [URL, URL + "private/contact", URL + "llms.txt", URL + "llm.txt"]
    tasks = [asyncio.create_task(fetching.robots_allowed(url, cache=cache)) for url in urls]
    try:
        await asyncio.wait_for(loading.wait(), 1)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert [allowed for allowed, _ in results] == [True, False, False, True]
        assert requested == [URL + "robots.txt"]
        assert await fetching.robots_allowed(URL + "private/another", cache=cache) == (False, None)
        assert requested == [URL + "robots.txt"]
    finally:
        for task in [*tasks, *cache.values()]:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, *cache.values(), return_exceptions=True)


async def test_cancelling_optional_waiter_does_not_cancel_shared_robots_load(monkeypatch):
    cache = {}
    loading, release, load_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requested = []

    async def fetch(url, **kwargs):
        requested.append(url)
        loading.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            load_cancelled.set()
            raise
        return Page("User-agent: *\nDisallow: /private\n", url, 200)

    monkeypatch.setattr(fetching, "fetch_http", fetch)
    main = asyncio.create_task(fetching.robots_allowed(URL, cache=cache))
    optional = asyncio.create_task(fetching.robots_allowed(URL + "llms.txt", cache=cache))
    try:
        await asyncio.wait_for(loading.wait(), 1)
        optional.cancel()
        await asyncio.gather(optional, return_exceptions=True)
        assert not load_cancelled.is_set()
        assert not main.done()
        release.set()
        assert await asyncio.wait_for(main, 1) == (True, None)
        assert requested == [URL + "robots.txt"]
    finally:
        for task in [main, optional, *cache.values()]:
            if not task.done():
                task.cancel()
        await asyncio.gather(main, optional, *cache.values(), return_exceptions=True)


async def test_import_cancellation_cleans_up_shared_robots_document_task(monkeypatch):
    loading, cancelled, never_release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def fetch(url, **kwargs):
        assert url == URL + "robots.txt"
        loading.set()
        try:
            await never_release.wait()
        finally:
            cancelled.set()

    install_http(monkeypatch, fetch)
    monkeypatch.setattr(service, "robots_allowed", fetching.robots_allowed)
    task = asyncio.create_task(service.import_profile(URL, False, False))
    try:
        await asyncio.wait_for(loading.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert cancelled.is_set()
    finally:
        await stop(task)


@pytest.mark.parametrize("first", ["llms.txt", "llm.txt"])
async def test_standard_llms_file_wins_alias_ties_independent_of_arrival_order(monkeypatch, first):
    release = {"llms.txt": asyncio.Event(), "llm.txt": asyncio.Event()}
    first_found = asyncio.Event()
    phones = {"llms.txt": "+14155550126", "llm.txt": "+14155550127"}

    async def fetch(url, **kwargs):
        if url == URL:
            return Page("<title>Clinic</title>", url, 200)
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        assert filename in release
        await release[filename].wait()
        description = "Canonical description." if filename == "llms.txt" else "Alias description."
        return Page(f"Business Name: Clinic\nPhone: {phones[filename]}\nDescription: {description}", url, 200)

    async def progress(snapshot):
        if snapshot["profile"]["phone"] == phones[first]:
            first_found.set()

    install_http(monkeypatch, fetch)
    task = asyncio.create_task(service.import_profile(URL, False, False, on_progress=progress))
    try:
        release[first].set()
        await asyncio.wait_for(first_found.wait(), 1)
        release["llm.txt" if first == "llms.txt" else "llms.txt"].set()
        result = await asyncio.wait_for(task, 1)
        assert result["profile"]["phone"] == phones["llms.txt"]
        assert result["profile"]["description"] == "Canonical description."
        assert result["provenance"]["phone"] == [{"url": URL + "llms.txt", "method": "llms_txt"}]
    finally:
        await stop(task)


async def test_canonical_www_llms_is_probed_when_initial_origin_files_are_missing(monkeypatch):
    requested = []
    canonical = "https://www.example.com/"

    async def fetch(url, **kwargs):
        requested.append(url)
        if url == URL:
            return Page("<title>Clinic</title>", canonical, 200)
        if url == canonical + "llms.txt":
            return Page("Business Name: Clinic\nPhone: +14155550126", url, 200)
        assert urlsplit(url).path in {"/llms.txt", "/llm.txt"}
        return Page("", url, 404)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert AUXILIARY_URLS.issubset(requested)
    assert canonical + "llms.txt" in requested
    assert result["profile"]["phone"] == "+14155550126"
    assert result["provenance"]["phone"] == [{"url": canonical + "llms.txt", "method": "llms_txt"}]


async def test_cross_domain_browser_redirect_clears_previous_main_contact_fields(monkeypatch):
    destination = "https://destination.example/"

    async def fetch(url, **kwargs):
        if url == URL:
            # A phone without a business name is still an incomplete JS shell.
            return Page('<a href="tel:+14155550126">Call</a><div id="root"></div>', url, 200)
        return Page("", url, 404)

    async def browser(url, **kwargs):
        return Page("<title>New Clinic</title>", destination, 200, "browser")

    install_http(monkeypatch, fetch)
    monkeypatch.setattr(service, "fetch_browser", browser)
    result = await service.import_profile(URL, True, False)
    assert result["profile"]["businessName"] == "New Clinic"
    assert result["profile"]["website"] == destination
    assert result["profile"]["phone"] == ""
    assert "phone" not in result["provenance"]


async def test_declared_jsonld_redirect_to_www_is_ignored_as_different_origin(monkeypatch):
    async def fetch(url, **kwargs):
        if url == URL:
            return Page('<title>Clinic</title><script type="application/ld+json" src="/schema.json"></script>', url, 200)
        if url == URL + "schema.json":
            return Page(json.dumps({"@type": "LocalBusiness", "name": "Clinic", "telephone": "+14155550126"}), "https://www.example.com/schema.json", 200)
        return Page("", url, 404)

    install_http(monkeypatch, fetch)
    result = await service.import_profile(URL, False, False)
    assert result["profile"]["businessName"] == "Clinic"
    assert result["profile"]["phone"] == ""
    assert "phone" not in result["provenance"]
