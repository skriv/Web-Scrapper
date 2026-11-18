"""URL guards for an intentionally local/trusted-user scraping service."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class UnsafeURL(ValueError):
    pass


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise UnsafeURL("Could not process this source. Verify the URL and try again.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None or "\\" in value:
            raise ValueError()
        if parts.port not in {None, 80, 443}:
            raise ValueError()
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if "%" in host or not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        literal = f"[{host}]" if ":" in host else host
        netloc = literal + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise UnsafeURL("Could not process this source. Check the URL and try again.") from exc


async def assert_public_url(value: str) -> str:
    url = normalize_url(value)
    parts = urlsplit(url)
    try:
        records = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM),
            timeout=5,
        )
    except (OSError, TimeoutError) as exc:
        raise UnsafeURL("Could not process this source. Verify the URL and try again.") from exc
    if not records or any(not ipaddress.ip_address(record[4][0].split("%")[0]).is_global for record in records):
        raise UnsafeURL("Could not process this source. Check the URL and try again.")
    return url


def is_maps_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    domain = host.removeprefix("www.").removeprefix("maps.")
    google_domains = {
        "google.com", "google.co.uk", "google.de", "google.fr", "google.es", "google.it", "google.nl",
        "google.ca", "google.com.au", "google.co.nz", "google.co.jp", "google.co.in", "google.com.br",
        "google.com.mx", "google.ru", "google.kg", "google.kz", "google.ae", "google.ch", "google.at",
        "google.be", "google.pl", "google.pt", "google.ie", "google.cz", "google.dk", "google.se",
        "google.no", "google.fi", "google.com.tr", "google.com.cy", "google.gr", "google.co.za",
    }
    return (host == "maps.app.goo.gl" or (host == "goo.gl" and parts.path.startswith("/maps"))
            or (domain in google_domains and (parts.path.startswith("/maps") or host.startswith("maps."))))


def is_short_maps_url(url: str) -> bool:
    return urlsplit(url).hostname in {"maps.app.goo.gl", "goo.gl"}
