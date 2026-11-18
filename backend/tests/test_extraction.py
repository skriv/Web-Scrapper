import json
from pathlib import Path

import pytest

from app.extraction import extract_html, merge_extractions, normalize_phone, normalize_timezone
from app.models import Extraction, Profile

URL = "https://harbor.example/"


def schema(node):
    return '<script type="application/ld+json">' + json.dumps(node) + "</script>"


def test_jsonld_graph_references_and_all_profile_fields():
    html = (Path(__file__).parent / "fixtures/business.html").read_text()
    result = extract_html(html, URL)
    profile = result.profile
    assert profile.businessName == "Harbor Dental"
    assert profile.description == "General and cosmetic dentistry."
    assert profile.address == "128 Harbor Street, San Francisco, CA, 94102, US"
    assert profile.phone == "+14155550123"
    assert profile.website == URL
    assert profile.timezone == "America/Los_Angeles"
    assert profile.openingHours == ["Mon, Tue 09:00–17:00", "Sun closed"]
    assert profile.services == ["Dental cleaning", "Teeth whitening"]
    assert profile.paymentMethods == ["Cash", "Visa", "Mastercard"]
    assert result.links == [URL + "contact", URL + "services", URL + "privacy"]
    assert result.provenance["businessName"] == [{"url": URL, "method": "jsonld"}]
    assert not result.warnings


def test_malformed_jsonld_does_not_discard_other_valid_scripts():
    html = '<script type="application/ld+json">{broken</script>' + schema({"@type": "LocalBusiness", "name": "Good"})
    result = extract_html(html, URL)
    assert result.profile.businessName == "Good"
    assert any("Malformed" in warning for warning in result.warnings)


def test_product_and_article_names_are_not_treated_as_business():
    html = '<title>Real page title</title>' + schema([
        {"@type": "Product", "name": "Electric toothbrush", "description": "Wrong product description"},
        {"@type": "Article", "name": "How to brush"},
    ])
    result = extract_html(html, URL)
    assert result.profile.businessName == "Real page title"
    assert result.profile.description == ""
    assert result.provenance["businessName"][0]["method"] == "title"
    assert result.warnings


def test_local_business_has_priority_over_publisher_organization():
    html = schema([
        {"@type": "Organization", "name": "Parent corporation", "url": "https://corporation.example/"},
        {"@type": "Restaurant", "name": "Neighborhood branch", "address": "123 Main Street"},
    ])
    result = extract_html(html, URL)
    assert result.profile.businessName == "Neighborhood branch"
    assert result.profile.address == "123 Main Street"
    assert any("Multiple" in warning for warning in result.warnings)


def test_microdata_scoping_keeps_nested_service_names_out_of_business_name():
    html = '''<div itemscope itemtype="https://schema.org/LocalBusiness">
      <span itemprop="name">Repair shop</span>
      <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
        <span itemprop="streetAddress">14 Park Road</span><span itemprop="addressLocality">Oxford</span>
      </div>
      <div itemprop="availableService" itemscope itemtype="https://schema.org/Service"><span itemprop="name">Bicycle repair</span></div>
      <time itemprop="openingHours" datetime="Mo-Fr 09:00-18:00">Weekdays</time>
    </div>'''
    result = extract_html(html, URL)
    assert result.profile.businessName == "Repair shop"
    assert result.profile.address == "14 Park Road, Oxford"
    assert result.profile.services == ["Bicycle repair"]
    assert result.profile.openingHours == ["Mon-Fri 09:00-18:00"]
    assert result.provenance["businessName"][0]["method"] == "microdata"


def test_missing_fields_stay_empty_and_html_fallback_has_sources():
    result = extract_html('''<meta property="og:site_name" content="Small shop">
      <meta name="description" content="Care &amp; repair"><address>12 Long Lane</address>
      <a href="tel:020 7946 0000">Call</a>''', URL)
    assert result.profile.businessName == "Small shop"
    assert result.profile.description == "Care & repair"
    assert result.profile.address == "12 Long Lane"
    assert result.profile.phone == "020 7946 0000"
    assert result.profile.timezone == ""
    assert result.profile.services == []
    assert result.profile.paymentMethods == []
    assert result.provenance["website"][0]["method"] == "input"


@pytest.mark.parametrize("number, expected", [
    ("tel:+44 20 7946 0000", "+442079460000"),
    ("+44 20 7946 0000 ext. 12", "+442079460000 ext. 12"),
    ("020 7946 0000", "020 7946 0000"),
    ("", ""),
])
def test_phone_normalization_does_not_guess_country(number, expected):
    assert normalize_phone(number) == expected


@pytest.mark.parametrize("timezone, expected", [
    ("Asia/Bishkek", "Asia/Bishkek"), ("UTC", "UTC"),
    ("UTC+6", ""), ("Not/AZone", ""), ("", ""),
])
def test_timezone_requires_actual_iana_identifier(timezone, expected):
    assert normalize_timezone(timezone) == expected


def test_coordinate_timezone_uses_local_lookup(monkeypatch):
    class FakeFinder:
        def timezone_at(self, *, lat, lng):
            assert (lat, lng) == (42.87, 74.59)
            return "Asia/Bishkek"
    monkeypatch.setattr("app.extraction._timezone_finder", lambda: FakeFinder())
    result = extract_html(schema({"@type": "LocalBusiness", "name": "Cafe", "geo": {"latitude": 42.87, "longitude": 74.59}}), URL)
    assert result.profile.timezone == "Asia/Bishkek"
    assert result.provenance["timezone"][0]["method"] == "coordinates"


def test_seasonal_hours_keep_date_qualifiers():
    result = extract_html(schema({"@type": "LocalBusiness", "name": "Summer cafe", "openingHoursSpecification": {
        "dayOfWeek": "Monday", "opens": "00:00", "closes": "23:59",
        "validFrom": "2026-06-01", "validThrough": "2026-08-31",
    }}), URL)
    assert result.profile.openingHours == ["Mon 00:00–23:59 (2026-06-01 – 2026-08-31)"]
    assert any("Seasonal" in warning for warning in result.warnings)


def test_merge_fills_gaps_but_keeps_primary_identity_and_evidence():
    primary = Extraction(profile=Profile(businessName="Shop", services=["Repair"]), provenance={"businessName": [{"url": URL, "method": "jsonld"}]})
    other = Extraction(profile=Profile(businessName="Contact | Shop", phone="+442079460000", services=["Repair", "Cleaning"]), provenance={"phone": [{"url": URL + "contact", "method": "html"}]})
    result = merge_extractions(primary, other)
    assert result.profile.businessName == "Shop"
    assert result.profile.phone == "+442079460000"
    assert result.profile.services == ["Repair", "Cleaning"]
    assert result.provenance["phone"][0]["url"] == URL + "contact"
    assert primary.profile.phone == ""  # No mutation of original extraction.


def test_merge_does_not_mix_different_branch_hours_or_phone():
    primary = Extraction(profile=Profile(businessName="Shop", address="12 Lane"))
    other = Extraction(profile=Profile(businessName="Shop", address="99 Avenue", phone="+442079460000", openingHours=["Mon closed"]))
    result = merge_extractions(primary, other)
    assert result.profile.address == "12 Lane"
    assert result.profile.phone == ""
    assert result.profile.openingHours == []
    assert any("different address" in warning for warning in result.warnings)


def test_circular_jsonld_references_are_bounded():
    html = schema({"@graph": [
        {"@id": "#business", "@type": "LocalBusiness", "name": "Shop", "parentOrganization": {"@id": "#business"}},
        {"@id": "#unused", "@type": "Thing", "isPartOf": {"@id": "#unused"}},
    ]})
    result = extract_html(html, URL)
    assert result.profile.businessName == "Shop"


def test_malformed_field_shapes_and_urls_do_not_crash_extraction():
    html = schema({"@id": {}, "@type": "LocalBusiness", "name": "Shop", "url": "https://[broken",
                   "paymentAccepted": {"@id": {}}, "offers": {"itemOffered": {"@type": "Product", "name": "Shoe"}}})
    result = extract_html(html + '<a href="https://[broken">Contact</a>', URL)
    assert result.profile.businessName == "Shop"
    assert result.profile.website == URL
    assert result.profile.services == []
    assert result.profile.paymentMethods == []


def test_service_provider_reference_is_associated_with_selected_business():
    html = schema({"@graph": [
        {"@id": "#shop", "@type": "LocalBusiness", "name": "Bike shop"},
        {"@type": "Service", "name": "Puncture repair", "provider": {"@id": "#shop"}},
        {"@type": "Service", "name": "Unrelated service", "provider": {"@type": "Organization", "name": "Other shop"}},
    ]})
    result = extract_html(html, URL)
    assert result.profile.services == ["Puncture repair"]


def test_merge_rejects_explicitly_different_businesses():
    primary = Extraction(profile=Profile(businessName="Harbor Dental"), provenance={"businessName": [{"url": URL, "method": "gosom"}]})
    other = Extraction(profile=Profile(businessName="Another Clinic", phone="+442079460000", services=["Cleaning"], paymentMethods=["Cash"]), provenance={"businessName": [{"url": URL, "method": "jsonld"}]})
    result = merge_extractions(primary, other)
    assert result.profile.phone == ""
    assert result.profile.services == []
    assert result.profile.paymentMethods == []
    assert any("different business name" in warning for warning in result.warnings)


def test_merge_accepts_case_and_punctuation_differences_in_explicit_names():
    primary = Extraction(profile=Profile(businessName="Harbor Dental, LLC."), provenance={"businessName": [{"url": URL, "method": "jsonld"}]})
    other = Extraction(profile=Profile(businessName="HARBOR DENTAL LLC", phone="+442079460000"), provenance={"businessName": [{"url": URL, "method": "microdata"}]})
    assert merge_extractions(primary, other).profile.phone == "+442079460000"
