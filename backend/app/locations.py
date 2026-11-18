"""Separate branches from organization data; never merge branch addresses."""
import json
import re
from urllib.parse import urlsplit, urlencode
from bs4 import BeautifulSoup
from .models import Location
from .extraction import extract_schema_json, normalize_phone, timezone_from_coordinates
from .english import english_hours

def store_source(html, url):
    match = re.search(r'var\s+ASL_REMOTE\s*=\s*(\{[^;]+\});', html)
    if not match or 'agile-store-locator' not in html:
        return None
    try:
        data = json.loads(match[1])
        endpoint = data['ajax_url']
        if urlsplit(endpoint).netloc != urlsplit(url).netloc:
            return None
        return endpoint + '?' + urlencode({'action':'asl_load_stores', 'nonce':data.get('nonce',''), 'asl_lang':data.get('lang',''), 'load_all':'1', 'layout':'0'})
    except (ValueError, KeyError, TypeError):
        return None

def store_locations(text, url):
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:200]:
        if not isinstance(row, dict) or not row.get('title'):
            continue
        clean = lambda v: BeautifulSoup(str(v or ''), 'html.parser').get_text(' ', strip=True) if '<' in str(v) else str(v or '').strip()
        address = ', '.join(clean(row.get(k)) for k in ['street','city','state','postal_code','country'] if row.get(k))
        if not address:
            continue
        hours = row.get('open_hours', '')
        if isinstance(hours, str):
            try: hours = json.loads(hours)
            except ValueError: hours = {}
        lines = [f'{day}: {", ".join(value) if isinstance(value,list) else (hours.get(day + "_label") or ("Closed" if value == "0" else value))}' for day,value in hours.items() if not day.endswith("_label")] if isinstance(hours,dict) else []
        result.append(Location(businessName=clean(row['title']), address=address, phone=normalize_phone(('+1 ' if row.get('country_code') == 'US' and not str(row.get('phone','')).startswith('+') else '') + str(row.get('phone') or '')), website=clean(row.get('website') or row.get('link')), timezone=timezone_from_coordinates(row.get('lat'),row.get('lng')), openingHours=english_hours(lines), sourceUrl=url.split("?",1)[0]))
    return result

def html_locations(html, url):
    soup = BeautifulSoup(html,'html.parser')
    result = []
    def walk(node):
        if isinstance(node,dict):
            yield node
            for value in node.values(): yield from walk(value)
        elif isinstance(node,list):
            for value in node[:200]: yield from walk(value)
    for script in soup.select('script[type="application/ld+json"]')[:50]:
        try: nodes = list(walk(json.loads(script.string or script.get_text())))
        except (ValueError,RecursionError): continue
        for node in nodes[:500]:
            if not node.get('address'): continue
            found = extract_schema_json(json.dumps(node),url).profile
            if found.businessName and found.address:
                result.append(Location(**found.model_dump(),sourceUrl=url.split("?",1)[0]))
    seen = set()
    return [x for x in result if not (x.address.casefold() in seen or seen.add(x.address.casefold()))][:200]
