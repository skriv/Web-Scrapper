import json

import pytest

from app.extraction import discover_structured_links, extract_html, extract_schema_json

URL = "https://clinic.example/contact"


def meta(name, value, attribute="property"):
    return f'<meta {attribute}="{name}" content="{value}">'


def test_business_contact_metadata_fills_published_profile_fields():
    html = "".join([
        meta("og:site_name", "Harbor Dental"), meta("og:title", "Contact | Wrong title"),
        meta("og:description", "Family dental care."), meta("business:contact_data:street_address", "12 Lane"),
        meta("business:contact_data:locality", "Oxford"), meta("business:contact_data:region", "Oxfordshire"),
        meta("business:contact_data:postal_code", "OX1 1AA"), meta("business:contact_data:country_name", "GB"),
        meta("business:contact_data:phone_number", "+44 20 7946 0000"),
        meta("business:contact_data:website", "https://clinic.example/"),
        meta("timeZone", "Europe/London", "itemprop"), meta("openingHours", "Mo-Fr 09:00-17:00", "itemprop"),
        meta("serviceType", "Cleaning; Whitening", "itemprop"), meta("paymentAccepted", "Cash, Visa", "itemprop"),
    ])
    result = extract_html(html, URL)
    assert result.profile.businessName == "Harbor Dental"
    assert result.profile.description == "Family dental care."
    assert result.profile.address == "12 Lane, Oxford, Oxfordshire, OX1 1AA, GB"
    assert result.profile.phone == "+442079460000"
    assert result.profile.website == "https://clinic.example/"
    assert result.profile.timezone == "Europe/London"
    assert result.profile.openingHours == ["Mon-Fri 09:00-17:00"]
    assert result.profile.services == ["Cleaning", "Whitening"]
    assert result.profile.paymentMethods == ["Cash", "Visa"]
    assert all(sources[0]["method"] == "meta" for sources in result.provenance.values())


def test_twitter_metadata_is_a_fallback_with_document_source():
    html = meta("twitter:title", "Small cafe", "name") + meta("twitter:description", "Coffee and lunch.", "name") + meta("twitter:url", "https://clinic.example/", "name")
    result = extract_html(html, URL)
    assert result.profile.businessName == "Small cafe"
    assert result.profile.description == "Coffee and lunch."
    assert result.profile.website == "https://clinic.example/"
    assert result.provenance["businessName"] == [{"url": URL, "method": "meta"}]


def test_jsonld_remains_authoritative_over_conflicting_metadata():
    html = '<script type="application/ld+json">' + json.dumps({
        "@type": "LocalBusiness", "name": "Correct clinic", "description": "Published description.",
        "telephone": "+14155550126", "address": "12 Lane", "url": "https://clinic.example/real",
        "timeZone": "America/Los_Angeles", "paymentAccepted": "Cash", "openingHours": "Mo-Fr 09:00-17:00",
    }) + "</script>"
    html += "".join([meta("og:site_name", "Other clinic"), meta("description", "Wrong description", "name"),
                     meta("telephone", "+442079460000", "itemprop"), meta("address", "99 Avenue", "itemprop"),
                     meta("og:url", "https://clinic.example/wrong"), meta("timeZone", "Europe/London", "itemprop"),
                     meta("paymentAccepted", "Visa", "itemprop"), meta("openingHours", "Mo-Fr 00:00-23:59", "itemprop")])
    result = extract_html(html, URL)
    assert result.profile.businessName == "Correct clinic"
    assert result.profile.description == "Published description."
    assert result.profile.phone == "+14155550126"
    assert result.profile.address == "12 Lane"
    assert result.profile.website == "https://clinic.example/real"
    assert result.profile.timezone == "America/Los_Angeles"
    assert result.profile.paymentMethods == ["Cash"]
    assert result.profile.openingHours == ["Mon-Fri 09:00-17:00"]
    assert all(sources[0]["method"] == "jsonld" for sources in result.provenance.values())


def test_nested_product_and_person_meta_do_not_leak_into_business():
    html = meta("og:site_name", "Clinic") + '''<div itemscope itemtype="https://schema.org/Product">
      <meta itemprop="telephone" content="+442079460000"><meta itemprop="address" content="Wrong product address">
      <meta itemprop="timeZone" content="Europe/London"><meta itemprop="paymentAccepted" content="Visa">
      </div><div itemscope itemtype="https://schema.org/Person"><meta itemprop="address" content="Private home"></div>'''
    result = extract_html(html, URL)
    assert result.profile.phone == ""
    assert result.profile.address == ""
    assert result.profile.timezone == ""
    assert result.profile.paymentMethods == []


@pytest.mark.parametrize("coordinate_meta", [
    meta("geo.position", "42.87;74.59", "name"), meta("ICBM", "42.87, 74.59", "name"),
    meta("place:location:latitude", "42.87") + meta("place:location:longitude", "74.59"),
    meta("latitude", "42.87", "itemprop") + meta("longitude", "74.59", "itemprop"),
])
def test_metadata_coordinates_use_local_timezone_lookup(monkeypatch, coordinate_meta):
    calls = []

    def lookup(latitude, longitude):
        calls.append((latitude, longitude))
        return "Asia/Bishkek"

    monkeypatch.setattr("app.extraction.timezone_from_coordinates", lookup)
    result = extract_html(meta("og:site_name", "Clinic") + coordinate_meta, URL)
    assert result.profile.timezone == "Asia/Bishkek"
    assert calls[-1] == ("42.87", "74.59")
    assert result.provenance["timezone"] == [{"url": URL, "method": "coordinates"}]


def test_locale_is_not_a_timezone_or_coordinate_hint():
    html = meta("og:locale", "en_US") + meta("geo.country", "US", "name") + meta("geo.placename", "New York", "name")
    result = extract_html(html, URL)
    assert result.profile.timezone == ""
    assert result.profile.address == ""


@pytest.mark.parametrize("metadata", [
    '<link rel="canonical" href="https://attacker.example/">',
    meta("og:url", "https://attacker.example/"), meta("twitter:url", "https://attacker.example/", "name"),
    '<link rel="canonical" href="https://clinic.example.attacker.example/">',
    '<link rel="canonical" href="javascript:alert(1)">', '<link rel="canonical" href="https://[broken">',
])
def test_cross_origin_or_invalid_canonical_cannot_replace_input_website(metadata):
    result = extract_html(metadata, URL)
    assert result.profile.website == URL
    assert result.provenance["website"][0]["method"] == "input"


def test_same_origin_and_www_alias_canonical_are_accepted():
    result = extract_html('<link rel="canonical" href="https://www.clinic.example/">', URL)
    assert result.profile.website == "https://www.clinic.example/"
    assert result.provenance["website"][0]["method"] == "meta"


def test_external_jsonld_graph_references_and_actual_source_url():
    source = "https://clinic.example/business.jsonld"
    document = {"@context": "https://schema.org", "@graph": [
        {"@id": "#shop", "@type": "Dentist", "name": "Harbor Dental", "address": {"@id": "#address"}, "telephone": "+14155550126"},
        {"@id": "#address", "@type": "PostalAddress", "streetAddress": "12 Lane", "addressLocality": "Oxford"},
    ]}
    result = extract_schema_json(json.dumps(document), source)
    assert result.profile.businessName == "Harbor Dental"
    assert result.profile.address == "12 Lane, Oxford"
    assert result.profile.phone == "+14155550126"
    assert result.profile.website == ""  # The JSON filename is not a business website.
    assert result.links == []
    assert all(sources[0] == {"url": source, "method": "jsonld"} for sources in result.provenance.values())


@pytest.mark.parametrize("text", ["{broken", '{"businessName":"Not schema.org"}', '{"@type":"Product","name":"Shoe"}', "[]", "null"])
def test_external_jsonld_rejects_arbitrary_json_without_fake_filename_fields(text):
    result = extract_schema_json(text, "https://clinic.example/schema.jsonld")
    assert not any(result.profile.model_dump().values())
    assert result.provenance == {}
    assert result.warnings


def test_external_jsonld_does_not_execute_or_parse_embedded_script_markup():
    text = json.dumps({"@type": "LocalBusiness", "name": "Clinic", "description": '</script><meta property="og:site_name" content="Attacker"><script>'})
    result = extract_schema_json(text, "https://clinic.example/schema.jsonld")
    assert result.profile.businessName == "Clinic"
    assert result.profile.website == ""
    assert result.provenance["businessName"][0]["method"] == "jsonld"


def test_discovery_only_follows_declared_same_origin_jsonld_and_is_bounded():
    html = '''<script type="application/ld+json" src="/one.jsonld"></script>
      <link type="application/ld+json" href="https://other.example/schema.jsonld">
      <link type="application/ld+json" href="/one.jsonld#duplicate">
      <script type="application/json" src="/ignored.json"></script>
      <a href="/guessed-schema.jsonld">Schema</a>
      <link type="APPLICATION/LD+JSON; charset=utf-8" href="/two.jsonld">
      <link type="application/ld+json" href="/three.jsonld">'''
    assert discover_structured_links(html, URL) == ["https://clinic.example/one.jsonld", "https://clinic.example/two.jsonld"]
    result = extract_html(html, URL)
    assert not any("Malformed" in warning for warning in result.warnings)
