from unittest.mock import AsyncMock

import pytest

from app import service, fetching
from app.fetching import Page, FetchError
from app.models import Extraction, Profile
from app.security import UnsafeURL

HTML = '<html><head><title>App loading</title></head><body><div id="root"></div></body></html>'
RENDERED = '<script type="application/ld+json">{"@type":"Dentist","name":"Actual Clinic","telephone":"+14155550126"}</script>'


@pytest.fixture(autouse=True)
def public_urls(monkeypatch):
    async def validate(url):
        return url
    monkeypatch.setattr(service, "assert_public_url", validate)
    monkeypatch.setattr(service, "robots_allowed", AsyncMock(return_value=(True, None)))


async def test_js_fallback_promotes_structured_data(monkeypatch):
    monkeypatch.setattr(service, "fetch_http", AsyncMock(return_value=Page(HTML, "https://example.com/", 200)))
    browser = AsyncMock(return_value=Page(RENDERED, "https://example.com/", 200, "browser"))
    monkeypatch.setattr(service, "fetch_browser", browser)
    result = await service.import_profile("https://example.com/", True, False)
    assert result["profile"]["businessName"] == "Actual Clinic"
    assert result["pagesFetched"] == 2
    browser.assert_awaited_once()


async def test_http_only_never_opens_browser(monkeypatch):
    monkeypatch.setattr(service, "fetch_http", AsyncMock(return_value=Page(HTML, "https://example.com/", 200)))
    browser = AsyncMock()
    monkeypatch.setattr(service, "fetch_browser", browser)
    await service.import_profile("https://example.com/", False, False)
    browser.assert_not_awaited()


def test_weaker_rendered_title_does_not_replace_jsonld():
    original = service.extract_html(RENDERED, "https://example.com/")
    rendered = service.extract_html(HTML, "https://example.com/")
    assert service.merge_rendered(original, rendered).profile.businessName == "Actual Clinic"


async def test_browser_failure_preserves_http_data(monkeypatch):
    monkeypatch.setattr(service, "fetch_http", AsyncMock(return_value=Page(HTML, "https://example.com/", 200)))
    monkeypatch.setattr(service, "fetch_browser", AsyncMock(side_effect=FetchError("browser unavailable")))
    result = await service.import_profile("https://example.com/", True, False)
    assert result["profile"]["businessName"] == "App loading"
    assert "browser unavailable" in result["warnings"]


async def test_robots_denial_stops_fetch(monkeypatch):
    monkeypatch.setattr(service, "robots_allowed", AsyncMock(return_value=(False, None)))
    fetch = AsyncMock()
    monkeypatch.setattr(service, "fetch_http", fetch)
    with pytest.raises(service.ImportError, match="robots"):
        await service.import_profile("https://example.com/", False, True)
    fetch.assert_not_awaited()


def test_discovery_is_same_origin_and_bounded():
    links = ["https://example.com/contact", "https://other.com/services", "https://example.com/services", "https://example.com/about", "https://example.com/locations", "https://example.com/contact?download=1", "http://example.com/contact", "https://example.com/about.pdf"]
    assert service.candidate_links(links, "https://example.com/") == ["https://example.com/contact", "https://example.com/services", "https://example.com/about", "https://example.com/locations"]


async def test_redirect_to_private_host_blocked_before_second_request(monkeypatch):
    async def guard(url):
        if "127.0.0.1" in url:
            raise UnsafeURL("private")
        return url
    calls = []
    def http(url):
        calls.append(url)
        return 302, {"Location": "http://127.0.0.1/secret"}, b""
    monkeypatch.setattr(fetching, "assert_public_url", guard)
    monkeypatch.setattr(fetching, "_http_get", http)
    with pytest.raises(UnsafeURL):
        await fetching.fetch_http("https://example.com/")
    assert calls == ["https://example.com/"]


async def test_maps_survives_website_failure(monkeypatch):
    from app import maps
    monkeypatch.setattr(maps, "extract_maps", AsyncMock(return_value=Extraction(profile=Profile(businessName="Clinic", address="A", website="https://example.com/"))))
    monkeypatch.setattr(service, "import_website", AsyncMock(side_effect=service.ImportError("site denied")))
    result = await service.import_profile("https://www.google.com/maps/place/Clinic", False, True)
    assert result["profile"]["address"] == "A"
    assert "site denied" in result["warnings"][0]


def test_rendered_other_business_cannot_override_html_phone_or_fill_address():
    original = service.extract_html(
        '<script type="application/ld+json">{"@type":"LocalBusiness","name":"Harbor Dental"}</script>'
        '<a href="tel:+14155550126">Call</a>',
        "https://example.com/",
    )
    rendered = service.extract_html(
        '<script type="application/ld+json">{"@type":"LocalBusiness","name":"Other Clinic",'
        '"address":"99 Avenue","telephone":"+442079460000",'
        '"hasOfferCatalog":{"itemListElement":[{"@type":"Offer","itemOffered":{"@type":"Service","name":"Whitening"}}]}}</script>',
        "https://example.com/",
    )
    merged = service.merge_rendered(original, rendered)
    assert merged.profile.businessName == "Harbor Dental"
    assert merged.profile.phone == "+14155550126"
    assert merged.profile.address == ""
    assert merged.profile.services == []
    assert merged.provenance["phone"][0]["method"] == "html"
    assert any("different business name" in warning for warning in merged.warnings)


def test_rendered_other_branch_cannot_override_phone_even_with_stronger_jsonld():
    original = service.extract_html(
        '<script type="application/ld+json">{"@type":"LocalBusiness","name":"Harbor Dental","address":"12 Lane"}</script>'
        '<a href="tel:+14155550126">Call</a>',
        "https://example.com/",
    )
    rendered = service.extract_html(
        '<script type="application/ld+json">{"@type":"LocalBusiness","name":"Harbor Dental",'
        '"address":"99 Avenue","telephone":"+442079460000","timeZone":"Europe/London",'
        '"openingHours":"Mo-Fr 09:00-18:00"}</script>',
        "https://example.com/",
    )
    merged = service.merge_rendered(original, rendered)
    assert merged.profile.businessName == "Harbor Dental"
    assert merged.profile.address == "12 Lane"
    assert merged.profile.phone == "+14155550126"
    assert merged.profile.timezone == ""
    assert merged.profile.openingHours == []
    assert merged.provenance["phone"][0]["method"] == "html"
    assert any("different address" in warning for warning in merged.warnings)
