import socket
from unittest.mock import AsyncMock

import pytest

from app.security import UnsafeURL, assert_public_url, normalize_url, is_maps_url


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com", "https://user:pass@example.com", "http://localhost", "https://host.internal", "https://example.com:9000", "https://example.com/\nfoo", "https://example.com\\@localhost", "http://[::1%25lo0]/"])
def test_rejects_unsafe_url_syntax(url):
    with pytest.raises(UnsafeURL):
        normalize_url(url)


def test_normalizes_scheme_unicode_fragment():
    assert normalize_url("example.com#section") == "https://example.com/"
    assert normalize_url("https://ПРИМЕР.РФ") == "https://xn--e1afmkfd.xn--p1ai/"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.2", "::1", "fc00::1", "::ffff:127.0.0.1", "0.0.0.0"])
async def test_rejects_private_resolved_addresses(monkeypatch, ip):
    import asyncio
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]))
    with pytest.raises(UnsafeURL):
        await assert_public_url("https://example.com")


async def test_mixed_public_private_dns_is_rejected(monkeypatch):
    import asyncio
    records = [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("10.0.0.2", 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=records))
    with pytest.raises(UnsafeURL):
        await assert_public_url("https://example.com")


def test_maps_detection_requires_actual_google_host():
    assert is_maps_url("https://www.google.com/maps/place/Clinic")
    assert is_maps_url("https://maps.app.goo.gl/123")
    assert is_maps_url("https://www.google.co.uk/maps/place/Clinic")
    assert is_maps_url("https://www.google.kg/maps/place/Clinic")
    assert not is_maps_url("https://google.com.attacker.example/maps/place/Clinic")
