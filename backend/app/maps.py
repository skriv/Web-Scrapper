"""Normalize Gosom's documented JSON shape without guessing missing fields."""

import os
import re
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from .english import english_hours
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import phonenumbers

from .models import Extraction, Profile
from .models import Location


class MapsError(RuntimeError):
    pass


async def search_maps_gosom(name: str, address: str, website: str) -> list[Location]:
    query = ' '.join(filter(None,[name,address]))[:300]
    base = os.getenv('MAPS_BASE_URL','http://127.0.0.1:8001').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=115,trust_env=False) as client:
            response = await client.post(base+'/search',json={'query':query})
            response.raise_for_status()
            rows = response.json()['entries']
            if not isinstance(rows,list):
                raise ValueError()
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        raise MapsError('Google Maps search could not finish. Website data is preserved.') from exc
    found=[]
    for row in rows[:10]:
        if not isinstance(row,dict) or not row.get('title'): continue
        place_id=row.get('place_id')
        link='https://www.google.com/maps/search/?api=1&query='+urlencode({'q':row['title']})[2:]
        if place_id: link+='&query_place_id='+str(place_id)
        normalized=normalize_maps(row,link)
        found.append(Location(**normalized.profile.model_dump(),sourceUrl=link))
    return found


def _text(value) -> str:
    return re.sub(r"\s+", " ", value).strip()[:4000] if isinstance(value, str) else ""


def _unique(values) -> list[str]:
    return list(dict.fromkeys(filter(None, (_text(x) for x in values))))[:100]


def normalize_maps(entry: dict, url: str) -> Extraction:
    data = {target: _text(entry.get(source)) for target, source in {
        "businessName": "title", "description": "description", "address": "address",
        "phone": "phone", "website": "web_site", "timezone": "timezone",
    }.items()}
    warnings = []
    try:
        website = urlsplit(data["website"])
        website_valid = website.scheme in {"http", "https"} and bool(website.hostname) and not website.username
    except ValueError:
        website_valid = False
    if data["website"] and not website_valid:
        data["website"] = ""
        warnings.append("Could not process this source. Check the URL and try again.")
    if data["timezone"]:
        try:
            ZoneInfo(data["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            data["timezone"] = ""
            warnings.append("Source data could not be verified. Review the result.")
    address = entry.get("complete_address")
    country = address.get("country") if isinstance(address, dict) else None
    if data["phone"]:
        try:
            parsed = phonenumbers.parse(data["phone"], country if isinstance(country, str) else None)
            if phonenumbers.is_possible_number(parsed):
                data["phone"] = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
                if parsed.extension:
                    data["phone"] += " ext. " + parsed.extension
        except phonenumbers.NumberParseException:
            pass  # Keep the published number when a reliable region is unavailable.
    hours = entry.get("open_hours")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    data["openingHours"] = []
    if isinstance(hours, dict):
        ordered = sorted(hours, key=lambda d: days.index(d) if d in days else 7)
        for day in ordered:
            spans = hours[day] if isinstance(hours[day], list) else [hours[day]]
            text = ", ".join(_unique(spans))
            if text:
                data["openingHours"].append(f"{_text(day)}: {text}")
    payments = entry.get("credit_cards_accepted")
    data["paymentMethods"] = _unique(payments) if isinstance(payments, list) else []
    data["services"] = []
    about = entry.get("about")
    for section in about if isinstance(about, list) else []:
        if not isinstance(section, dict):
            continue
        options = section.get("options")
        enabled = [x.get("name") for x in options if isinstance(x, dict) and x.get("enabled") is True] if isinstance(options, list) else []
        if section.get("id") == "payments":
            data["paymentMethods"] = _unique(data["paymentMethods"] + enabled)
        elif section.get("id") == "service_options":
            data["services"] = _unique(enabled)
    data["openingHours"] = english_hours(data["openingHours"])
    profile = Profile(**data)
    provenance = {key: [{"url": url, "method": "gosom"}] for key, value in profile.model_dump().items() if value}
    if "/maps/place/" in url and not entry.get("place_id") and not entry.get("data_id"):
        warnings.append("Could not process this source. Check the URL and try again.")
    return Extraction(profile=profile, provenance=provenance, warnings=warnings)


async def extract_maps_gosom(url: str) -> Extraction:
    parts = urlsplit(url)
    query = [(k,v) for k,v in parse_qsl(parts.query) if k not in {"hl", "gl"}]
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query + [("hl","en"),("gl","us")]), parts.fragment))
    base = os.getenv("MAPS_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=115, trust_env=False) as client:
            response = await client.post(base + "/extract", json={"url": url})
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise MapsError("Could not process this source. Check the URL and try again.") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        raise MapsError(_text(detail) or "Could not process this source. Check the URL and try again.")
    try:
        entry = response.json()["entry"]
        if not isinstance(entry, dict) or not _text(entry.get("title")):
            raise ValueError("missing business title")
        return normalize_maps(entry, url)
    except (ValueError, KeyError, TypeError) as exc:
        raise MapsError("Could not process this source. Check the URL and try again.") from exc


async def search_maps(name: str, address: str, website: str):
    from .configuration import settings_context
    if settings_context.get() is None: return await search_maps_gosom(name,address,website)
    from .providers import maps_chain
    return await maps_chain(' '.join(filter(None,[name,address]))[:300])

async def extract_maps(url: str):
    from .configuration import settings_context
    if settings_context.get() is None: return await extract_maps_gosom(url)
    from .providers import maps_chain
    return await maps_chain(url)
