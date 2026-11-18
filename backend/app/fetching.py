import asyncio
import os
import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .security import UnsafeURL, assert_public_url
from .configuration import settings_context, trace_context, settings, count, trace

MAX_BYTES = 3 * 1024 * 1024
HTTP_TIMEOUT = 12


class FetchError(RuntimeError):
    pass


@dataclass
class Page:
    html: str
    url: str
    status: int
    method: str = "http"


def _http_get(url: str, *, timeout: float = HTTP_TIMEOUT, max_bytes: int = MAX_BYTES):
    # One session per request prevents cookie/session leakage between businesses.
    from scrapling.fetchers import Fetcher

    body = bytearray()

    def collect(chunk: bytes):
        if len(body) + len(chunk) > max_bytes:
            raise FetchError("Could not process this source. Verify the URL and try again.")
        body.extend(chunk)
        return len(chunk)

    count('httpRequests')
    response = Fetcher.get(
        url, timeout=timeout, retries=0, follow_redirects=False,
        stealthy_headers=True, headers={"Accept-Language": "en-US,en;q=0.9", "User-Agent": "ProfileKit/2.0 (self-hosted business profile tool)", "Accept": "text/html,application/xhtml+xml,text/plain;q=0.5"},
        content_callback=collect,
    )
    return response.status, dict(response.headers), bytes(body)


async def _pooled_get(data,url,timeout,max_bytes):
    from scrapling.fetchers import FetcherSession
    # One asynchronous keep-alive pool per extraction. Cookies never cross jobs.
    async with data['httpPoolLock']:
        if 'httpPool' not in data:
            pool=FetcherSession(retries=0,follow_redirects=False,verify=True,stealthy_headers=False,
                headers={'Accept-Language':'en-US,en;q=0.9','User-Agent':'ProfileKit/2.0 (self-hosted business profile tool)'})
            data['httpClient']=await pool.__aenter__()
            data['httpPool']=pool
    body=bytearray()
    def collect(chunk):
        if len(body)+len(chunk)>max_bytes: raise FetchError('Source response exceeded the size limit.')
        body.extend(chunk);return len(chunk)
    count('httpRequests')
    response=await data['httpClient'].get(url,timeout=timeout,content_callback=collect)
    return response.status,dict(response.headers),bytes(body)


async def fetch_http(url: str, *, maps_only: bool = False, timeout: float = HTTP_TIMEOUT, max_bytes: int = MAX_BYTES, allow_json: bool = False) -> Page:
    from .security import is_maps_url
    if settings_context.get() is not None:
        timeout = min(timeout, settings().requestTimeout) if not maps_only and "google.com" not in urlsplit(url).netloc else timeout
    for _ in range(6):
        url = await assert_public_url(url)
        if maps_only and not is_maps_url(url):
            raise UnsafeURL("Could not process this source. Verify the URL and try again.")
        try:
            data=trace_context.get()
            if data is not None and 'httpPoolLock' in data:
                status,raw_headers,body=await _pooled_get(data,url,timeout,max_bytes)
            elif timeout == HTTP_TIMEOUT and max_bytes == MAX_BYTES:
                status, raw_headers, body = await asyncio.to_thread(_http_get, url)
            else:
                status, raw_headers, body = await asyncio.to_thread(_http_get, url, timeout=timeout, max_bytes=max_bytes)
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError("Source data could not be verified. Review the result.") from exc
        headers = {k.lower(): v for k, v in raw_headers.items()}
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise FetchError("Could not process this source. Verify the URL and try again.")
            url = urljoin(url, location)
            continue
        content_type = headers.get("content-type", "text/html").lower()
        is_json = content_type.split(";", 1)[0].strip() in {"application/json", "application/ld+json"}
        if not any(kind in content_type for kind in ("text/", "application/xhtml+xml")) and not (allow_json and is_json):
            raise FetchError("Could not process this source. Check the URL and try again.")
        import re
        charset = re.search(r"charset=([\w-]+)", content_type)
        try:
            html = body.decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        data = trace_context.get()
        if data is not None and urlsplit(url).hostname not in {"www.google.com","maps.google.com"}:
            from .diagnostics import record_page
            record_page(data, html, url, status, 'scrapling')
            trace('page_received', provider='scrapling', status=status, bytes=len(body))
        return Page(html, url, status)
    raise FetchError("Could not process this source. Verify the URL and try again.")


async def fetch_browser(url: str) -> Page:
    """Crawl4AI only when HTTP has insufficient business data; no cloud/LLM calls."""
    url = await assert_public_url(url)
    if os.getenv("ENABLE_BROWSER", "true").lower() != "true":
        raise FetchError("Could not process this source. Check the URL and try again.")
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    except ImportError as exc:
        raise FetchError("Could not process this source. Check the URL and try again.") from exc

    budget = 0
    browser_requests = 0
    job_trace = trace_context.get()

    async def setup(page, context, **_):
        async def guard(route):
            nonlocal budget, browser_requests
            budget += 1
            try:
                if budget > 80 or route.request.resource_type in {"image", "media", "font"}:
                    await route.abort()
                    return
                await assert_public_url(route.request.url)
            except UnsafeURL:
                await route.abort()
                return
            browser_requests += 1
            await route.continue_()

        await context.route("**/*", guard)
        # Service workers bypass Playwright route guards, so disable registration.
        await context.add_init_script("if (navigator.serviceWorker) navigator.serviceWorker.register = () => Promise.reject(new Error('Disabled'));")
        if hasattr(context, "route_web_socket"):
            await context.route_web_socket("**/*", lambda ws: ws.close())
        return page

    try:
        async with asyncio.timeout(35):
            async with AsyncWebCrawler(config=BrowserConfig(
                headless=True, verbose=False, accept_downloads=False, ignore_https_errors=False,
                chrome_channel=os.getenv("BROWSER_CHANNEL", "chromium"), headers={"Accept-Language": "en-US,en;q=0.9"},
            )) as crawler:
                crawler.crawler_strategy.set_hook("on_page_context_created", setup)
                result = await crawler.arun(url=url, config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS, page_timeout=25000, wait_until="domcontentloaded",
                    delay_before_return_html=1.0, verbose=False, check_robots_txt=False,
                ))
                if not result.success:
                    logging.getLogger(__name__).warning("Crawl4AI failed: %s", result.error_message)
                    raise FetchError("Could not process this source. Check the URL and try again.")
                final = await assert_public_url(result.redirected_url or result.url or url)
                html = result.html or ""
                if len(html.encode()) > MAX_BYTES:
                    raise FetchError("Could not process this source. Check the URL and try again.")
                if budget == 0: raise FetchError('Browser network guard did not activate.')
                if job_trace is not None: job_trace['httpRequests'] += browser_requests
                return Page(html, final, result.redirected_status_code or result.status_code or 200, "browser")
    except FetchError:
        raise
    except TimeoutError as exc:
        raise FetchError("Could not process this source. Verify the URL and try again.") from exc
    except Exception as exc:
        raise FetchError("Source data could not be verified. Review the result.") from exc


async def _load_robots(origin: str):
    from urllib.robotparser import RobotFileParser
    try:
        async with asyncio.timeout(5):
            page = await fetch_http(origin + "/robots.txt", timeout=4, max_bytes=256 * 1024)
        if page.status in {401, 403}:
            return None, False, None
        if page.status == 404:
            return None, True, None
        if page.status >= 400:
            return None, True, "Source data could not be verified. Review the result."
        parser = RobotFileParser()
        parser.parse(page.html.splitlines())
        return parser, True, None
    except (FetchError, UnsafeURL, TimeoutError):
        return None, True, "Source data could not be verified. Review the result."


async def robots_allowed(url: str, *, cache: dict | None = None) -> tuple[bool, str | None]:
    """A request-local shared document; rules are still evaluated for every path."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if cache is None:
        parser, allowed, warning = await _load_robots(origin)
    else:
        if origin not in cache:
            cache[origin] = asyncio.create_task(_load_robots(origin))
        # An optional source timeout must not cancel a policy another source uses.
        parser, allowed, warning = await asyncio.shield(cache[origin])
    if parser is not None:
        entries = [entry for entry in parser.entries if entry.applies_to('ProfileKit')]
        if not entries and parser.default_entry:
            entries = [parser.default_entry]
        path = urlsplit(url).path or '/'
        matches = [rule for entry in entries for rule in entry.rulelines if rule.applies_to(path)]
        if matches:
            return max(matches, key=lambda rule: (len(rule.path), rule.allowance)).allowance, warning
        return parser.can_fetch('ProfileKit',url), warning
    return allowed, warning
