"""Deterministic local extraction. Missing data stays missing; no LLM or API calls."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Iterable
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup, Tag

from .models import Extraction, Profile
from .contact_fields import extract_contacts
from .navigation_offerings import navigation_offerings

# Common Schema.org LocalBusiness descendants. Organization remains a lower-ranked fallback.
LOCAL_TYPES = set("""
LocalBusiness AnimalShelter Archive AutomotiveBusiness AutoBodyShop AutoDealer AutoPartsStore
AutoRental AutoRepair AutoWash GasStation MotorcycleDealer MotorcycleRepair ChildCare Dentist
DryCleaningOrLaundry EmergencyService EmploymentAgency EntertainmentBusiness AdultEntertainment
AmusementPark ArtGallery Casino ComedyClub MovieTheater NightClub FinancialService AccountingService
AutomatedTeller BankOrCreditUnion InsuranceAgency FoodEstablishment Bakery BarOrPub Brewery CafeOrCoffeeShop
Distillery FastFoodRestaurant IceCreamShop Restaurant Winery GovernmentOffice HealthAndBeautyBusiness
BeautySalon DaySpa HairSalon HealthClub NailSalon TattooParlor HomeAndConstructionBusiness Electrician
GeneralContractor HVACBusiness HousePainter Locksmith MovingCompany Plumber RoofingContractor
InternetCafe LegalService Attorney Library LodgingBusiness BedAndBreakfast Campground Hostel Hotel
Motel Resort MedicalBusiness CommunityHealth Dental Dermatology DietNutrition Emergency Geriatric
Gynecologic MedicalClinic Midwifery Nursing Obstetric Optician Optometric Otolaryngologic Pediatric
Pharmacy Physician Physiotherapy PlasticSurgery Podiatric PrimaryCare Psychiatric PublicHealth
ProfessionalService RadioStation RealEstateAgent RecyclingCenter SelfStorage ShoppingCenter
SportsActivityLocation BowlingAlley ExerciseGym GolfCourse IceSkatingRink PublicSwimmingPool
SkiResort SportsClub StadiumOrArena TennisComplex Store BikeStore BookStore ClothingStore ComputerStore
ConvenienceStore DepartmentStore ElectronicsStore Florist FurnitureStore GardenStore GroceryStore
HardwareStore HobbyShop HomeGoodsStore JewelryStore LiquorStore MensClothingStore MobilePhoneStore
MovieRentalStore MusicStore OfficeEquipmentStore OutletStore PawnShop PetStore ShoeStore
SportingGoodsStore TireShop ToyStore WholesaleStore TelevisionStation TouristInformationCenter
TravelAgency VeterinaryCare
""".split())
DAYS = dict(zip(
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "PublicHolidays"],
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Public holidays"],
))
SHORT_DAYS = {"Mo": "Mon", "Tu": "Tue", "We": "Wed", "Th": "Thu", "Fr": "Fri", "Sa": "Sat", "Su": "Sun"}


def clean(value: Any, limit: int = 4000) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    from html import unescape
    value=unescape(str(value))
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True) if "<" in str(value) else str(value)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def unique(values: Iterable[str]) -> list[str]:
    result, seen = [], set()
    for value in values:
        value = clean(value)
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result[:100]


def type_names(node: dict) -> set[str]:
    return {str(t).rstrip("/").rsplit("/", 1)[-1] for t in as_list(node.get("@type"))}


def normalize_phone(value: Any) -> str:
    """Only format international numbers; preserve local numbers without inventing a country."""
    value = clean(value, 100)
    value = re.sub(r"^tel:", "", value, flags=re.I).split("?", 1)[0].strip()
    if not value:
        return ""
    try:
        import phonenumbers
    except ImportError:
        return value
    try:
        number = phonenumbers.parse(value, None)
        if phonenumbers.is_possible_number(number):
            formatted = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
            return formatted + (" ext. " + number.extension if number.extension else "")
    except (phonenumbers.NumberParseException, ValueError):
        pass
    return value


def normalize_timezone(value: Any) -> str:
    value = clean(value, 100)
    if not value:
        return ""
    try:
        ZoneInfo(value)
        return value
    except (ZoneInfoNotFoundError, ValueError):
        return ""


@lru_cache(maxsize=1)
def _timezone_finder():
    from timezonefinder import TimezoneFinder
    return TimezoneFinder(in_memory=True)


def timezone_from_coordinates(latitude: Any, longitude: Any) -> str:
    try:
        lat, lng = float(latitude), float(longitude)
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return _timezone_finder().timezone_at(lat=lat, lng=lng) or ""
    except (ImportError, ValueError, TypeError, OverflowError):
        pass
    return ""


def _walk(value: Any, depth: int = 0):
    if depth > 20:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:1000]:
            yield from _walk(child, depth + 1)


def _resolve(value: Any, index: dict[str, dict], trail: frozenset = frozenset(), depth: int = 0):
    if depth > 12:
        return value
    if isinstance(value, list):
        return [_resolve(v, index, trail, depth + 1) for v in value[:1000]]
    if not isinstance(value, dict):
        return value
    reference = value.get("@id")
    if isinstance(reference, str) and reference in index and reference not in trail:
        value = {**index[reference], **value}
        trail = trail | {reference}
    return {k: _resolve(v, index, trail, depth + 1) for k, v in value.items()}


def _address(value: Any) -> str:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return _address(value[0]) if value else ""
    if not isinstance(value, dict):
        return ""
    country = value.get("addressCountry", "")
    if isinstance(country, dict):
        country = country.get("name", "")
    return ", ".join(unique([clean(value.get(k)) for k in (
        "streetAddress", "addressLocality", "addressRegion", "postalCode"
    )] + [clean(country)]))


def _url(value: Any, base: str) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("@id") or value.get("url")
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        result = urldefrag(urljoin(base, value.strip()))[0]
        parsed = urlsplit(result)
        return result if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else ""
    except ValueError:
        return ""


def _name(value: Any) -> str:
    if isinstance(value, dict):
        name = clean(value.get("name"))
        identifier = value.get("@id")
        return name or (clean(identifier.rsplit("/", 1)[-1]) if isinstance(identifier, str) else "")
    return clean(value)


def _payment(value: Any) -> list[str]:
    result = []
    for item in as_list(value):
        text = _name(item)
        if text.startswith(("https://", "http://")):
            text = text.rstrip("/").rsplit("/", 1)[-1]
        result.extend(re.split(r"[,;|]", text))
    return unique(result)


def _services(node: dict) -> list[str]:
    result = [clean(v) for v in as_list(node.get("serviceType"))]
    for key in ("hasOfferCatalog", "makesOffer", "offers", "availableService"):
        for item in _walk(node.get(key)):
            types = type_names(item)
            offered = item.get("itemOffered")
            if offered:
                for service in as_list(offered):
                    if not isinstance(service, dict) or not type_names(service) or "Service" in type_names(service):
                        result.append(_name(service))
            elif "Service" in types or key == "availableService":
                result.append(clean(item.get("name")))
    return unique(result)


def _time(value: Any) -> str:
    value = clean(value, 50)
    match = re.fullmatch(r"(\d{2}):(\d{2})(?::\d{2})?", value)
    if match and int(match[1]) <= 24 and int(match[2]) < 60:
        return f"{match[1]}:{match[2]}"
    return value


def _hours(node: dict) -> tuple[list[str], bool]:
    result, seasonal = [], False
    for spec in as_list(node.get("openingHoursSpecification")):
        if not isinstance(spec, dict):
            continue
        days = []
        for day in as_list(spec.get("dayOfWeek")):
            name = _name(day).rstrip("/").rsplit("/", 1)[-1]
            days.append(DAYS.get(name, SHORT_DAYS.get(name, name)))
        opens, closes = _time(spec.get("opens")), _time(spec.get("closes"))
        if not days or not (opens and closes):
            continue
        span = "closed" if opens == closes == "00:00" else f"{opens}–{closes}"
        valid_from, valid_to = clean(spec.get("validFrom")), clean(spec.get("validThrough"))
        qualifier = f" ({valid_from or '…'} – {valid_to or '…'})" if valid_from or valid_to else ""
        seasonal |= bool(qualifier)
        result.append(f"{', '.join(unique(days))} {span}{qualifier}")
    if not result:
        for item in as_list(node.get("openingHours")):
            text = clean(item)
            text = re.sub(r"\b(Mo|Tu|We|Th|Fr|Sa|Su)\b", lambda m: SHORT_DAYS[m[0]], text)
            result.append(text)
    return unique(result), seasonal


def _score(node: dict) -> int:
    types = type_names(node)
    kind = 100 if types & LOCAL_TYPES else (40 if types & {"Organization", "Corporation", "NGO", "OnlineBusiness"} else 0)
    if not kind:
        return 0
    return kind + sum(bool(node.get(k)) for k in ("name", "address", "telephone", "url"))


def _microdata(scope: Tag, depth: int = 0) -> dict:
    if depth > 12:
        return {}
    result: dict = {"@type": scope.get("itemtype", "").split()}
    for element in scope.select("[itemprop]")[:1000]:
        # A property's closest itemscope parent must be this object.
        parent = element.find_parent(attrs={"itemscope": True})
        if parent is not scope:
            continue
        value: Any
        if element.has_attr("itemscope"):
            value = _microdata(element, depth + 1)
        else:
            value = element.get("content") or element.get("datetime") or element.get("href") or element.get("src") or element.get_text(" ", strip=True)
        for prop in element.get("itemprop", "").split():
            if prop in result:
                result[prop] = as_list(result[prop]) + [value]
            else:
                result[prop] = value
    return result


def _relevant_links(soup: BeautifulSoup, url: str) -> list[str]:
    source = urlsplit(url)
    result = []
    pattern = re.compile(r"contact|about|services?|opening|hours|kontakt|leistungen|контакт|услуг|о[-_ ]компан", re.I)
    for link in soup.select("a[href]")[:3000]:
        href = _url(link.get("href"), url)
        parsed = urlsplit(href)
        if href and parsed.netloc == source.netloc and not re.search(r"/(?:wp-admin|login|logout|cart|checkout)(?:/|$)", parsed.path):
            if not re.search(r"\.(pdf|jpg|png|zip|docx?)$", parsed.path, re.I):
                result.append(href)
    return unique(result)[:100]


def _same_origin_url(value: Any, base: str, *, allow_www: bool = False) -> str:
    """Metadata must not silently substitute an unrelated site's canonical identity."""
    candidate = _url(value, base)
    if not candidate:
        return ""
    try:
        left, right = urlsplit(candidate), urlsplit(base)
        left_host, right_host = (left.hostname or "").lower(), (right.hostname or "").lower()
        if allow_www:
            left_host = left_host.removeprefix("www.")
            right_host = right_host.removeprefix("www.")
        left_port = left.port or (443 if left.scheme == "https" else 80)
        right_port = right.port or (443 if right.scheme == "https" else 80)
        return candidate if (left.scheme, left_host, left_port) == (right.scheme, right_host, right_port) else ""
    except ValueError:
        return ""


def discover_structured_links(html: str, url: str) -> list[str]:
    """Return only explicitly declared, same-origin external JSON-LD documents."""
    soup = BeautifulSoup(html, "lxml")
    found = []
    for element in soup.select("script[src][type], link[href][type]")[:200]:
        mime = element.get("type", "").split(";", 1)[0].strip().lower()
        if mime != "application/ld+json":
            continue
        link = _same_origin_url(element.get("src") if element.name == "script" else element.get("href"), url)
        if link and link not in found:
            found.append(link)
        if len(found) == 2:
            break
    return found


def _document_metadata(soup: BeautifulSoup) -> dict[str, list[str]]:
    metadata: dict[str, list[str]] = {}
    for element in soup.find_all("meta")[:1000]:
        # Scoped microdata is handled by _microdata. Pulling a nested product's or
        # person's meta properties into the document-level business would mix entities.
        if element.find_parent(attrs={"itemscope": True}) is not None:
            continue
        value = clean(element.get("content"))
        if not value:
            continue
        names = [element.get("property", ""), element.get("name", "")]
        names.extend(element.get("itemprop", "").split())
        for name in names:
            if isinstance(name, str) and name.strip():
                metadata.setdefault(name.strip().lower(), []).append(value)
    return metadata


def _meta_first(metadata: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = metadata.get(name.lower(), [])
        if values:
            return values[0]
    return ""


def _meta_coordinates(metadata: dict[str, list[str]]) -> tuple[str, str]:
    for latitude, longitude in (("latitude", "longitude"), ("geo:latitude", "geo:longitude"),
                                ("place:location:latitude", "place:location:longitude")):
        lat, lng = _meta_first(metadata, latitude), _meta_first(metadata, longitude)
        if lat and lng:
            return lat, lng
    for name in ("geo.position", "icbm"):
        value = _meta_first(metadata, name)
        pieces = re.split(r"\s*[;,]\s*", value)
        if len(pieces) == 2:
            return pieces[0], pieces[1]
    return "", ""


def extract_schema_json(text: str, url: str) -> Extraction:
    """Parse a declared external JSON-LD file without HTML or input-URL fallbacks."""
    try:
        document = json.loads(text)
        if not any(_score(node) for node in _walk(document)):
            return Extraction(warnings=["Could not process this source. Check the URL and try again."])
        # Escaping '<' prevents text containing </script> from becoming HTML markup.
        payload = json.dumps(document, ensure_ascii=False).replace("<", "\\u003c")
    except (ValueError, TypeError, RecursionError):
        return Extraction(warnings=["Malformed external JSON-LD was skipped."])
    result = extract_html('<script type="application/ld+json">' + payload + "</script>", url)
    if any(source.get("method") == "input" for source in result.provenance.get("website", [])):
        result.profile.website = ""
        result.provenance.pop("website", None)
    result.links = []
    return result


def extract_html(html: str, url: str) -> Extraction:
    soup = BeautifulSoup(html, "lxml")
    offerings=navigation_offerings(soup,url)
    extraction = Extraction(links=unique([row["url"] for row in offerings]+_relevant_links(soup, url))[:100],navigationOfferings=offerings)
    extraction.language = str(soup.html.get("lang", "") if soup.html else "").lower().split("-")[0]
    documents = []
    malformed = False
    for script in soup.find_all("script", type=lambda x: bool(x and x.lower().split(";", 1)[0].strip() == "application/ld+json"))[:100]:
        if script.has_attr("src"):
            continue
        try:
            documents.append(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError, RecursionError):
            malformed = True
    nodes = [node for document in documents for node in _walk(document)][:3000]
    index: dict[str, dict] = {}
    for node in nodes:
        if isinstance(node.get("@id"), str):
            key = node["@id"]
            index[key] = {**index.get(key, {}), **node}
    # Deduplicate repeated @id references before selecting the richest business node.
    candidates, seen = [], set()
    for node in nodes:
        resolved = _resolve(node, index)
        if _score(resolved):
            identifier = resolved.get("@id")
            identity = identifier if isinstance(identifier, str) else (clean(resolved.get("name")), _address(resolved.get("address")))
            if identity not in seen:
                candidates.append((resolved, "jsonld"))
                seen.add(identity)
    if not candidates:
        for scope in soup.select("[itemscope][itemtype]")[:100]:
            node = _microdata(scope)
            if _score(node):
                candidates.append((node, "microdata"))
    candidates.sort(key=lambda item: _score(item[0]), reverse=True)
    node, method = candidates[0] if candidates else ({}, "jsonld")

    def put(field: str, value: Any, source_method: str = method):
        if value and not getattr(extraction.profile, field):
            setattr(extraction.profile, field, value)
            extraction.provenance[field] = [{"url": url, "method": source_method}]

    put("businessName", clean(node.get("name") or node.get("legalName")))
    email,email_method,contact_name=extract_contacts(node,soup,lambda value:_resolve(value,index))
    put('email',email,email_method)
    put('full_name',clean(contact_name),'jsonld')
    put("description", clean(node.get("description")))
    put("address", _address(node.get("address")))
    telephone = node.get("telephone")
    if not telephone:
        for contact in as_list(node.get("contactPoint")):
            if isinstance(contact, dict) and contact.get("telephone"):
                telephone = contact["telephone"]
                break
    put("phone", normalize_phone(as_list(telephone)[0] if telephone else ""))
    put("website", _url(node.get("url"), url))
    raw_timezone = node.get("timeZone") or node.get("timezone")
    put("timezone", normalize_timezone(raw_timezone))
    if raw_timezone and not extraction.profile.timezone:
        extraction.warnings.append('Invalid IANA time zone in source.')
    geo = node.get("geo")
    if not extraction.profile.timezone and isinstance(geo, dict):
        put("timezone", timezone_from_coordinates(geo.get("latitude"), geo.get("longitude")), "coordinates")
    if not extraction.profile.timezone and node.get("latitude") is not None and node.get("longitude") is not None:
        put("timezone", timezone_from_coordinates(node.get("latitude"), node.get("longitude")), "coordinates")
    hours, seasonal = _hours(node)
    put("openingHours", hours)
    services = _services(node)
    if method == "jsonld":
        for service_node in nodes:
            if "Service" not in type_names(service_node):
                continue
            service_node = _resolve(service_node, index)
            providers = as_list(service_node.get("provider"))
            matches = not providers and len(candidates) == 1
            for provider in providers:
                if isinstance(provider, dict):
                    matches |= bool(node.get("@id") and provider.get("@id") == node.get("@id"))
                    matches |= bool(clean(provider.get("name")) and clean(provider.get("name")) == clean(node.get("name")))
            if matches:
                services.extend([clean(service_node.get("name"))] + [clean(v) for v in as_list(service_node.get("serviceType"))])
    put("services", unique(services))
    for offering in offerings:
        if offering['service'] not in extraction.profile.services:
            extraction.profile.services.append(offering['service'])
            extraction.provenance.setdefault('services',[]).append({'url':url,'method':offering['method'],'value':offering['service'],'targetUrl':offering['url'],'section':offering['section'],'label':offering['label']})

    put("paymentMethods", _payment(node.get("paymentAccepted") or node.get("acceptedPaymentMethod")))

    metadata = _document_metadata(soup)

    def meta(*names: str) -> str:
        return _meta_first(metadata, *names)

    put("businessName", meta("og:site_name", "application-name", "og:title", "twitter:title"), "meta")
    # A document title is a low-confidence fallback, never a replacement for schema.org identity.
    put("businessName", clean(soup.title.get_text()) if soup.title else "", "title")
    put("description", meta("description", "og:description", "twitter:description"), "meta")
    if not extraction.profile.description or re.search(r'Normal\s+0.*false.*false|X-NONE|mso-',extraction.profile.description,re.I):
        if extraction.profile.description:
            extraction.profile.description='';extraction.provenance.pop('description',None)
        for site_node in nodes:
            site_types=as_list(site_node.get('@type'))
            if 'WebSite' in site_types and _same_origin_url(site_node.get('url',''),url,allow_www=True):
                if not site_node.get('name') or re.sub(r'\W+','',clean(site_node.get('name')).casefold())==re.sub(r'\W+','',extraction.profile.businessName.casefold()):
                    put('description',clean(site_node.get('description')),'jsonld')
    address_metadata = {
        "streetAddress": meta("business:contact_data:street_address", "streetAddress"),
        "addressLocality": meta("business:contact_data:locality", "addressLocality"),
        "addressRegion": meta("business:contact_data:region", "addressRegion"),
        "postalCode": meta("business:contact_data:postal_code", "postalCode"),
        "addressCountry": meta("business:contact_data:country_name", "addressCountry"),
    }
    put("address", meta("address") or _address(address_metadata), "meta")
    put("phone", normalize_phone(meta("business:contact_data:phone_number", "telephone")), "meta")
    put("timezone", normalize_timezone(meta("timeZone", "business:contact_data:time_zone")), "meta")
    if not extraction.profile.timezone:
        latitude, longitude = _meta_coordinates(metadata)
        put("timezone", timezone_from_coordinates(latitude, longitude), "coordinates")
    meta_hours = metadata.get("openinghours", [])
    put("openingHours", _hours({"openingHours": meta_hours})[0], "meta")
    meta_services = metadata.get("servicetype", []) + metadata.get("availableservice", [])
    put("services", unique(part for value in meta_services for part in re.split(r"[,;|]", value)), "meta")
    put("paymentMethods", _payment(metadata.get("paymentaccepted") or metadata.get("acceptedpaymentmethod")), "meta")
    # Same-site canonical identities are preferable to a /contact or /about input.
    for website in (meta("business:contact_data:website"), meta("url"), meta("og:url"), meta("twitter:url")):
        put("website", _same_origin_url(website, url, allow_www=True), "meta")
    for canonical in soup.select("link[rel][href]")[:200]:
        if "canonical" in [relation.lower() for relation in canonical.get("rel", [])]:
            put("website", _same_origin_url(canonical.get("href"), url, allow_www=True), "meta")
    phone_link = soup.select_one('a[href^="tel:"]')
    if phone_link:
        put("phone", normalize_phone(phone_link.get("href")), "html")
    if not candidates:
        address = soup.find("address")
        if address:
            put("address", clean(address.get_text(" ", strip=True)), "html")
    put("website", _url(url, url), "input")
    if malformed:
        extraction.warnings.append('Malformed JSON-LD was skipped.')
    if len(candidates) > 1:
        extraction.warnings.append('Multiple organizations found; verify the selected branch.')
    if not candidates:
        extraction.warnings.append('No structured business card found; review metadata fields.')
    elif extraction.provenance.get("businessName", [{}])[0].get("method") == "title":
        extraction.warnings.append('Business name comes from the page title.')
    if seasonal:
        extraction.warnings.append('Seasonal opening hours require review.')
    return extraction


def has_identity_conflict(base: Extraction, extra: Extraction) -> bool:
    def explicit_name(extraction: Extraction) -> bool:
        return bool(extraction.profile.businessName and any(
            source.get("method") in {"jsonld", "microdata", "gosom", "llms_txt"}
            for source in extraction.provenance.get("businessName", [])
        ))

    def identity(name: str) -> str:
        return re.sub(r"\W+", " ", name.casefold()).strip()

    return bool(explicit_name(base) and explicit_name(extra) and identity(base.profile.businessName) != identity(extra.profile.businessName))


def merge_extractions(base: Extraction, extra: Extraction, prefer_base: bool = True) -> Extraction:
    """Fill gaps; union services/payment only when identities do not explicitly disagree."""
    if not prefer_base:
        return merge_extractions(extra, base)
    result = base.model_copy(deep=True)
    known_locations = {location.address.casefold() for location in result.locations}
    for location in extra.locations:
        if location.address.casefold() not in known_locations:
            result.locations.append(location.model_copy(deep=True))
            known_locations.add(location.address.casefold())
    left, right = base.profile, extra.profile
    if has_identity_conflict(base, extra):
        result.warnings = unique(result.warnings + [
            "A different business name was found; its fields were not merged."
        ])
        return result
    known_offerings={(row.get('kind'),row.get('url')) for row in result.navigationOfferings}
    for row in extra.navigationOfferings:
        if (row.get('kind'),row.get('url')) not in known_offerings:
            result.navigationOfferings.append(dict(row));known_offerings.add((row.get('kind'),row.get('url')))
    # Branch data must never silently overwrite or join a different branch's address/hours.
    different_address = bool(left.address and right.address and left.address.casefold() != right.address.casefold())
    for field in Profile.model_fields:
        value = getattr(right, field)
        existing = getattr(result.profile, field)
        if not value:
            continue
        if different_address and field in {"address", "phone", "timezone", "openingHours"}:
            continue
        if field in {"services", "paymentMethods"}:
            combined = unique(existing + value)
            if combined != existing:
                setattr(result.profile, field, combined)
                result.provenance.setdefault(field, []).extend(extra.provenance.get(field, []))
        elif not existing:
            setattr(result.profile, field, value)
            result.provenance[field] = extra.provenance.get(field, [])
    result.warnings = unique(result.warnings + extra.warnings)
    if different_address:
        result.warnings = unique(result.warnings + ["A different address was found; branch fields were not merged."])
    result.links = unique(result.links + extra.links)
    return result
