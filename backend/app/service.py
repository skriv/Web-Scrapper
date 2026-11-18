import asyncio
import time
from copy import deepcopy
import re
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urldefrag, urljoin

from bs4 import BeautifulSoup

from .extraction import extract_html, extract_schema_json, discover_structured_links, merge_extractions, has_identity_conflict
from .llms import extract_llms
from .locations import html_locations, store_locations, store_source
from .english import english_hours, english_content
from .fetching import FetchError, fetch_browser, fetch_http, robots_allowed
from .models import Extraction, Profile, CORE_FIELDS
from .contact_fields import complete_contacts
from .configuration import settings_context, settings, trace_context
from .security import UnsafeURL, assert_public_url, is_maps_url, is_short_maps_url


class ImportError(RuntimeError):
    """A recoverable user-facing import failure."""


def result_payload(extraction: Extraction, started: float, pages: int) -> dict:
    return complete_contacts({
        "profile": extraction.profile.model_dump(),
        "industries": list(dict.fromkeys(row["label"] for row in extraction.navigationOfferings if row["kind"]=="industry")),
        "navigationOfferings": deepcopy(extraction.navigationOfferings),
        "provenance": deepcopy(extraction.provenance),
        "warnings": list(dict.fromkeys(extraction.warnings)),
        "missingFields": [key for key, value in extraction.profile.model_dump().items() if not value],
        "elapsedMs": round((time.monotonic() - started) * 1000),
        "pagesFetched": pages,
        "locations": [location.model_dump() for location in extraction.locations],
        "mapsCandidates": [candidate.model_dump() for candidate in extraction.mapsCandidates],
        "mapsStatus": extraction.mapsStatus,
        "htmlPagesFetched": extraction.htmlPagesFetched,
    })


PageProgress = Callable[[Extraction, int], Awaitable[None]]
ProgressCallback = Callable[[dict], Awaitable[None]]


class DiscoveryProgress:
    """Measure actual discoveries, independently of transport and cleanup time."""

    def __init__(self, started: float, callback: ProgressCallback | None):
        self.started = started
        self.callback = callback
        self.first: int | None = None
        self.last: int | None = None
        self.discovered: dict = {}
        self.previous = Profile().model_dump()
        self.previous_locations = []
        self.previous_maps = ("idle", [])

    async def publish(self, extraction: Extraction, pages: int):
        await english_content(extraction)
        extraction.profile.openingHours = english_hours(extraction.profile.openingHours)
        profile = extraction.profile.model_dump()
        locations = [location.model_dump() for location in extraction.locations]
        maps_state = (extraction.mapsStatus,[candidate.model_dump() for candidate in extraction.mapsCandidates])
        discovered = {
            key: value for key, value in profile.items()
            if value and any(source.get("method") != "input" for source in extraction.provenance.get(key, []))
        }
        if any(value != self.discovered.get(key) for key, value in discovered.items()) or locations != self.previous_locations or maps_state[1] != self.previous_maps[1]:
            found_at = round((time.monotonic() - self.started) * 1000)
            if self.first is None:
                self.first = found_at
            self.last = found_at
        self.discovered = deepcopy(discovered)
        if profile != self.previous or locations != self.previous_locations or maps_state != self.previous_maps:
            self.previous = deepcopy(profile)
            self.previous_locations = deepcopy(locations)
            self.previous_maps = deepcopy(maps_state)
            if self.callback:
                await self.callback(self.payload(extraction, pages))

    def payload(self, extraction: Extraction, pages: int) -> dict:
        return {
            **result_payload(extraction, self.started, pages),
            "firstFoundMs": self.first,
            "lastFoundMs": self.last,
            "discoverySpanMs": self.last - self.first if self.first is not None and self.last is not None else None,
        }


def candidate_links(links: list[str], origin: str) -> list[str]:
    base = urlsplit(origin)
    candidates = []
    seen = {urldefrag(origin)[0].rstrip("/")}
    for link in links:
        link = urldefrag(link)[0]
        parsed = urlsplit(link)
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or parsed.query:
            continue
        if link.rstrip("/") in seen:
            continue
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in (".pdf", ".png", ".jpg", ".zip", ".xml")):
            continue
        seen.add(link.rstrip("/"))
        priority = 0 if "contact" in path or "контакт" in path else 1 if "service" in path or "услуг" in path or re.search(r"/(?:industries|industry|sectors|verticals|markets|who-we-serve|solutions|products)/",path) else 2 if "about" in path else 3 if "location" in path else 4
        candidates.append((priority, link))
    return [link for _, link in sorted(candidates)[:9]]


def needs_browser(extraction: Extraction, html: str) -> bool:
    # Missing fields alone do not imply that a browser will find anything new.
    substantive = [extraction.profile.address, extraction.profile.phone,
                   extraction.profile.openingHours, extraction.profile.services]
    if extraction.profile.businessName and any(substantive):
        return False
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return len(soup.get_text(" ", strip=True)) < 160


SOURCE_STRENGTH = {"input": 0, "title": 1, "llms_txt": 2, "meta": 3, "html": 4, "microdata": 5, "jsonld": 6, "coordinates": 6, "gosom": 7}


def merge_rendered(original: Extraction, rendered: Extraction) -> Extraction:
    merged = merge_extractions(original, rendered)
    if has_identity_conflict(original, rendered):
        return merged
    different_address = bool(original.profile.address and rendered.profile.address and original.profile.address.casefold() != rendered.profile.address.casefold())
    strength = SOURCE_STRENGTH
    for field, value in rendered.profile.model_dump().items():
        if different_address and field in {"address", "phone", "timezone", "openingHours"}:
            continue
        old_sources, new_sources = original.provenance.get(field, []), rendered.provenance.get(field, [])
        old_rank = max((strength.get(s.get("method", ""), 0) for s in old_sources), default=0)
        new_rank = max((strength.get(s.get("method", ""), 0) for s in new_sources), default=0)
        if value and new_rank > old_rank:
            setattr(merged.profile, field, value)
            merged.provenance[field] = new_sources
    return merged


async def import_website(url: str, render_js: bool, follow_links: bool, on_progress: PageProgress | None = None) -> tuple[Extraction, int]:
    url = await assert_public_url(url)
    robots_cache: dict = {}
    pending: dict[asyncio.Task, tuple[str, str]] = {}
    sources: dict[str, Extraction] = {}
    scheduled: set[tuple[str, str]] = set()
    schema_urls: set[str] = set()
    contact_urls: set[str] = set()
    llms_origins: set[tuple[str, str]] = set()
    warnings: list[str] = []
    pages = 0
    html_urls: set[str] = set()
    html_completed: set[str] = set()
    cfg = settings()
    page_slots = asyncio.Semaphore(cfg.websiteConcurrency)
    depths = {url: 0}
    if settings_context.get() is not None:
        from .providers import website_fetch, browser_fetch
        http_fetch, js_fetch = website_fetch, browser_fetch
    else:
        http_fetch, js_fetch = fetch_http, fetch_browser
    extraction = Extraction()
    main_page = None
    scope_url = url
    browser_started = False
    main_error = ""

    def same_site(left: str, right: str) -> bool:
        a, b = urlsplit(left), urlsplit(right)
        return (a.hostname or "").removeprefix("www.") == (b.hostname or "").removeprefix("www.") and a.port == b.port

    def combine():
        # Rebuild in a stable confidence order: a slow structured source must
        # supersede an earlier text fallback, not depend on arrival order.
        def order(item):
            key, source = item
            strength = max((SOURCE_STRENGTH.get(p.get("method", ""), 0)
                            for values in source.provenance.values() for p in values
                            if p.get("method") != "coordinates"), default=0)
            main_priority = 0 if key.startswith("main:") else 1 if key.startswith("schema:") else 2
            canonical_llms = 0 if key.startswith("llms:") and urlsplit(key[5:]).path.endswith("/llms.txt") else 1
            return -strength, main_priority, canonical_llms, key
        merged = Extraction()
        for _, source in sorted(sources.items(), key=order):
            merged = merge_rendered(merged, source)
        directory_locations = [location for key, source in sources.items() if key.startswith('locations:') for location in source.locations]
        if directory_locations:
            merged.locations = directory_locations
        if any(p.get("method") in {"jsonld", "microdata"} for p in merged.provenance.get("businessName", [])):
            merged.warnings = [w for w in merged.warnings if not w.startswith("No structured business card found")]
        return merged

    async def publish():
        if on_progress:
            snapshot = extraction.model_copy(deep=True)
            snapshot.warnings.extend(warnings)
            await on_progress(snapshot, pages)

    async def read(kind: str, target: str):
        auxiliary = kind in {"llms", "schema", "locations"}
        try:
            async with asyncio.timeout(10 if kind == "locations" else 3 if auxiliary else 35 if kind == "browser" else 20):
                allowed, warning = await robots_allowed(target, cache=robots_cache)
                if not allowed:
                    if kind == "main":
                        raise ImportError("robots.txt disallows this page.")
                    return None, None
                if kind == "browser":
                    page = await js_fetch(target)
                elif kind == "locations":
                    page = await http_fetch(target, timeout=6, max_bytes=2 * 1024 * 1024, allow_json=True)
                elif auxiliary:
                    page = await http_fetch(target, timeout=2, max_bytes=256 * 1024, allow_json=kind == "schema")
                elif kind == "related":
                    async with page_slots:
                        page = await http_fetch(target, timeout=6)
                else:
                    page = await http_fetch(target)
                if page.status >= 400:
                    return None, None if auxiliary else f"Page returned HTTP {page.status}: {target}"
                if kind not in {"main", "browser"} and not same_site(page.url, scope_url):
                    return None, None if auxiliary else "Source data could not be verified. Review the result."
                if kind == "schema" and (urlsplit(page.url).scheme, urlsplit(page.url).netloc) != (urlsplit(target).scheme, urlsplit(target).netloc):
                    return None, None
                if page.url != target:
                    final_allowed, final_warning = await robots_allowed(page.url, cache=robots_cache)
                    if not final_allowed:
                        if kind == "main":
                            raise ImportError("Could not process this source. Check the URL and try again.")
                        return None, None
                    warning = warning or final_warning
                return page, warning
        except (FetchError, UnsafeURL, TimeoutError) as exc:
            return None, None if auxiliary else (str(exc) or "Page load timed out.")

    def start(kind: str, target: str):
        key = (kind, target)
        if key not in scheduled:
            if kind in {"main", "related"}:
                if target not in html_urls and len(html_urls) >= cfg.maxPages:
                    return
                html_urls.add(target)
            scheduled.add(key)
            pending[asyncio.create_task(read(kind, target))] = key

    def start_llms(base: str):
        origin = urlsplit(base)
        site = (origin.scheme, origin.netloc)
        if site in llms_origins:
            return
        llms_origins.add(site)
        for path in ("/llms.txt", "/llm.txt"):
            start("llms", f"{origin.scheme}://{origin.netloc}{path}")

    def discover(html: str, base: str):
        for alternate in BeautifulSoup(html, 'html.parser').select('link[rel~="alternate"][hreflang][href]')[:30]:
            target = urljoin(base, str(alternate.get('href','')))
            if str(alternate.get('hreflang','')).lower() in {'en','en-us','en-gb'} and urlsplit(target).netloc == urlsplit(base).netloc:
                start('main', target)
                break
        location_source = store_source(html, base)
        if location_source:
            start("locations", location_source)
        for target in discover_structured_links(html, base):
            if len(schema_urls) < 2 and target not in schema_urls:
                schema_urls.add(target)
                start("schema", target)
        # A path-scoped llms.txt may be explicitly declared in the HTML head.
        for tag in BeautifulSoup(html, "html.parser").select('link[rel~="describedby"][href]')[:10]:
            target = urldefrag(urljoin(base, str(tag.get("href", ""))))[0]
            parsed, origin = urlsplit(target), urlsplit(base)
            if parsed.path.endswith(("/llms.txt", "/llm.txt")) and (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc):
                start("llms", target)
                break

    try:
        allowed, warning = await robots_allowed(url, cache=robots_cache)
        if not allowed:
            raise ImportError("robots.txt disallows this page.")
        if warning:
            warnings.append(warning)
        start("main", url)
        start_llms(url)
        while pending:
            completed, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                kind, target = pending.pop(task)
                if task.cancelled():
                    continue
                page, warning = await task
                if warning:
                    warnings.append(warning)
                    if kind == "main":
                        main_error = warning
                if page is None:
                    continue
                if kind in {"main", "browser"}:
                    changed_site = not same_site(page.url, scope_url)
                    if changed_site:
                        sources.clear()
                        for other, (other_kind, other_url) in list(pending.items()):
                            if other_kind != "main" and not same_site(other_url, page.url):
                                other.cancel()
                        contact_urls.clear()
                    scope_url = page.url
                    main_page = page
                    if changed_site:
                        start_llms(scope_url)
                elif not same_site(page.url, scope_url):
                    continue
                if kind == "locations":
                    found = Extraction(locations=store_locations(page.html, page.url))
                elif kind == "schema":
                    found = extract_schema_json(page.html, page.url)
                elif kind == "llms" or (kind == "related" and urlsplit(page.url).path.endswith(".md")):
                    found = extract_llms(page.html, page.url)
                else:
                    found = extract_html(page.html, page.url)
                    found.locations = html_locations(page.html, page.url)
                    discover(page.html, page.url)
                if kind in {"llms", "schema", "locations"} and not any(found.profile.model_dump().values()) and not found.links and not found.locations:
                    continue
                await english_content(found)
                pages += 1
                if kind in {'main','related'}:
                    html_completed.add(page.url)
                sources[("main:" + kind) if kind in {"main", "browser"} else kind + ":" + page.url] = found
                extraction = combine()
                extraction.htmlPagesFetched = len(html_completed)
                await publish()
                if follow_links:
                    for link in candidate_links(extraction.links, scope_url):
                        if len(contact_urls) < cfg.maxPages - 1 and link not in contact_urls and (settings_context.get() is None or depths.get(page.url, 0) < cfg.crawlDepth):
                            depths[link] = depths.get(page.url, 0) + 1
                            contact_urls.add(link)
                            start("related", link)
            # A www/HTTPS canonical homepage may have files even when the
            # original host's files returned 404. Avoid duplicate alias probes
            # while the first files are still being resolved successfully.
            origin = urlsplit(scope_url)
            if main_page is not None and (origin.scheme, origin.netloc) not in llms_origins and not any(
                kind == "llms" for kind, _ in pending.values()
            ) and not any(key.startswith("llms:") for key in sources):
                start_llms(scope_url)
            if not follow_links and main_page is not None and all(getattr(extraction.profile,k) for k in CORE_FIELDS) and any(
                p.get("method") in {"jsonld", "microdata"} for p in extraction.provenance.get("businessName", [])
            ):
                break
            # Start the expensive browser only when cheap, parallel sources have
            # finished and the page still looks like a JavaScript shell.
            if not pending and render_js and not browser_started and needs_browser(extraction, main_page.html if main_page else ""):
                browser_started = True
                start("browser", scope_url)
        if main_page is None and not extraction.profile.businessName:
            raise ImportError(main_error or "Could not process this source. Check the URL and try again.")
        extraction.warnings.extend(warnings)
        if not extraction.profile.businessName:
            extraction.warnings.append("Source data could not be verified. Review the result.")
        return extraction, pages
    finally:
        tasks = list(pending) + list(robots_cache.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def verified_maps_candidates(candidates, profile, website):
    def host(value):return (urlsplit(value).hostname or '').removeprefix('www.')
    def address(value):return re.sub(r'\W+','',value.casefold())
    return [c for c in candidates if c.phone and c.phone==profile.phone and host(c.website)==host(website)
            and not (c.address and profile.address and address(c.address)!=address(profile.address))]


async def import_profile(url: str, render_js: bool = True, follow_links: bool = True, on_progress: ProgressCallback | None = None) -> dict:
    if settings_context.get() is not None and settings().bidirectionalSearch:
        from .parallel_scraping import import_parallel
        return await import_parallel(url,render_js,follow_links,on_progress)
    started = time.monotonic()
    progress = DiscoveryProgress(started, on_progress)
    url = await assert_public_url(url)
    if is_short_maps_url(url):
        resolved = await fetch_http(url, maps_only=True)
        url = resolved.url
    if is_maps_url(url):
        from .maps import extract_maps
        try:
            extraction = await extract_maps(url)
        except (RuntimeError, ValueError) as exc:
            raise ImportError(str(exc)) from exc
        pages = 1
        await progress.publish(extraction, pages)
        # Maps stays authoritative for branch-specific fields; website only fills gaps.
        if extraction.profile.website:
            try:
                base_maps = extraction.model_copy(deep=True)

                async def website_progress(extra: Extraction, count: int):
                    nonlocal extraction, pages
                    extraction = merge_extractions(extra, base_maps) if settings_context.get() is not None and settings().preferOfficialWebsite else merge_extractions(base_maps, extra)
                    pages = 1 + count
                    await progress.publish(extraction, pages)

                remaining = max(5, min(55, 170 - (time.monotonic() - started)))
                extra, count = await asyncio.wait_for(import_website(extraction.profile.website, render_js, follow_links, on_progress=website_progress), timeout=remaining)
                extraction = merge_extractions(extra, extraction) if settings_context.get() is not None and settings().preferOfficialWebsite else merge_extractions(extraction, extra)
                pages = 1 + count
            except (ImportError, UnsafeURL) as exc:
                extraction.warnings.append("Could not process this source. Check the URL and try again." + str(exc))
            except TimeoutError:
                extraction.warnings.append("Source data could not be verified. Review the result.")
    else:
        from .maps import search_maps
        latest = Extraction()
        latest_pages = 0
        maps_task = None
        candidates = []
        maps_status = 'disabled' if (trace_context.get() or {}).get('skipMaps') else 'waiting_for_identity'
        maps_warning = ''
        async def emit():
            snapshot=latest.model_copy(deep=True)
            if maps_status == 'matched':
                def host(value): return (urlsplit(value).hostname or '').removeprefix('www.')
                matches=verified_maps_candidates(candidates,latest.profile,url)
                if len(matches)==1:
                    verified=Extraction(profile=Profile(**{key:value for key,value in matches[0].model_dump().items() if key in Profile.model_fields}),provenance={key:[{'url':matches[0].sourceUrl,'method':(trace_context.get() or {}).get('mapsProvider','gosom')}] for key,value in matches[0].model_dump().items() if key in Profile.model_fields and value})
                    snapshot=merge_extractions(snapshot,verified) if settings_context.get() is not None and settings().preferOfficialWebsite else merge_extractions(verified,snapshot)
            snapshot.mapsCandidates=candidates
            snapshot.mapsStatus=maps_status
            if maps_warning: snapshot.warnings.append(maps_warning)
            await progress.publish(snapshot,latest_pages)
        async def lookup():
            nonlocal candidates,maps_status,maps_warning
            try:
                candidates=await search_maps(latest.profile.businessName,latest.profile.address or latest.profile.phone,url)
                maps_status='selection_required' if candidates else 'not_found'
                def host(value):return (urlsplit(value).hostname or '').removeprefix('www.')
                verified=verified_maps_candidates(candidates,latest.profile,url)
                if len(verified)==1:maps_status='matched'
            except RuntimeError as exc:
                maps_status='error';maps_warning=str(exc)
            await emit()
        async def website_progress(found,count):
            nonlocal latest,latest_pages,maps_task,maps_status
            latest=found;latest_pages=count
            if maps_status != 'disabled' and maps_task is None and latest.profile.businessName and (latest.profile.address or latest.profile.phone):
                maps_status='searching'
                maps_task=asyncio.create_task(lookup())
            await emit()
        try:
            latest,latest_pages=await import_website(url,render_js,follow_links,on_progress=website_progress)
            if maps_status != 'disabled' and maps_task is None and latest.profile.businessName:
                maps_status='searching'
                await emit()
                maps_task=asyncio.create_task(lookup())
            if maps_task is not None: await maps_task
            await emit()
            extraction,pages=latest.model_copy(deep=True),latest_pages
            if maps_status=='matched':
                def host(value):return (urlsplit(value).hostname or '').removeprefix('www.')
                verified=verified_maps_candidates(candidates,latest.profile,url)
                if len(verified)==1:
                    entry=verified[0]
                    extra=Extraction(profile=Profile(**{k:v for k,v in entry.model_dump().items() if k in Profile.model_fields}),provenance={k:[{'url':entry.sourceUrl,'method':(trace_context.get() or {}).get('mapsProvider','gosom')}] for k,v in entry.model_dump().items() if k in Profile.model_fields and v})
                    extraction=merge_extractions(extraction,extra) if settings_context.get() is not None and settings().preferOfficialWebsite else merge_extractions(extra,extraction)
            extraction.mapsCandidates=candidates
            extraction.mapsStatus=maps_status
            if maps_warning:extraction.warnings.append(maps_warning)
        finally:
            if maps_task is not None and not maps_task.done():
                maps_task.cancel()
                await asyncio.gather(maps_task,return_exceptions=True)
    await progress.publish(extraction, pages)
    return progress.payload(extraction, pages)
