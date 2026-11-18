"""Timezone completion from explicit facts, coordinates, matching branches and phone hints."""
import asyncio
import hashlib
import os
import re
import time
from urllib.parse import urlencode
from .extraction import normalize_timezone,timezone_from_coordinates


def address_tokens(value):
    text=str(value).lower()
    text=re.sub(r'\bunited states(?: of america)?\b|\busa\b|\bus\b',' ',text)
    words=re.findall(r'[a-z0-9]+',text)
    aliases={'suite':'ste','street':'st','avenue':'ave','road':'rd','boulevard':'blvd','drive':'dr','north':'n','south':'s','east':'e','west':'w'}
    return {aliases.get(word,word) for word in words}


def same_address(left,right):
    if not left or not right:return False
    a,b=address_tokens(left),address_tokens(right)
    # Formatting differences (Ste/Suite, omitted state/country) are not another branch.
    if a==b:return True
    z1=set(re.findall(r'\b\d{5}(?:-\d{4})?\b',left));z2=set(re.findall(r'\b\d{5}(?:-\d{4})?\b',right))
    n1=re.match(r'\s*(\d+)\b',left);n2=re.match(r'\s*(\d+)\b',right)
    return bool(z1 and z1==z2 and n1 and n2 and n1[1]==n2[1] and len(a&b)/max(1,min(len(a),len(b)))>=.85)


def explicit_zone(row):
    for key in ('timezone','timeZone','time_zone'):
        zone=normalize_timezone(row.get(key))
        if zone:return zone
    return ''


def coordinates_zone(row):
    extra=row.get('additionalData') or {}
    coordinates=row.get('coordinates') or row.get('geo') or extra.get('coordinates') or {}
    if not isinstance(coordinates,dict):return ''
    return timezone_from_coordinates(coordinates.get('latitude',coordinates.get('lat')),coordinates.get('longitude',coordinates.get('lng',coordinates.get('lon'))))


def phone_zone(phone,address=''):
    import phonenumbers
    from phonenumbers import timezone,geocoder
    try:
        number=phonenumbers.parse(phone,None)
        if not phonenumbers.is_valid_number(number):return ''
        zones={normalize_timezone(value) for value in timezone.time_zones_for_number(number)}-{''}
        if len(zones)!=1:return ''
        # Do not let a foreign/portable phone prefix override contradictory address evidence.
        if address:
            description=geocoder.description_for_number(number,'en')
            tokens=address_tokens(description)
            if not tokens or not tokens.issubset(address_tokens(address)):return ''
        return next(iter(zones))
    except (phonenumbers.NumberParseException,ValueError):return ''


def resolve_local(payload,additional=None):
    profile=payload['profile'];locations=payload.get('locations') or []
    for row in locations:
        row['timezone']=explicit_zone(row) or coordinates_zone(row)
    existing=explicit_zone(profile)
    if existing:
        profile['timezone']=existing
        return False
    source_url=profile.get('website','')
    method='';zone='';detail=''
    normalized=payload.get('businessProfile') or {}
    zone=explicit_zone(normalized) or explicit_zone(payload)
    if zone:method='explicit_timezone';detail='Published time zone'
    if not zone:
        matching=[row for row in locations+(payload.get('mapsCandidates') or []) if same_address(profile.get('address',''),row.get('address',''))]
        zones={explicit_zone(row) or coordinates_zone(row) for row in matching}-{''}
        if len(zones)==1:
            zone=next(iter(zones));method='location_timezone';detail='Detected from the matching business address'
            source_url=next((row.get('sourceUrl','') for row in matching if row.get('sourceUrl')),source_url)
        elif len(zones)>1:return False
    if not zone and locations:
        # A chain-wide zone is useful only if every discovered location declares the same zone.
        zones={explicit_zone(row) for row in locations}
        if '' not in zones and len(zones)==1:
            zone=next(iter(zones));method='location_consensus';detail='All discovered locations use this time zone'
            source_url=locations[0].get('sourceUrl') or source_url
    if not zone and (not locations or len(locations)==1):
        zone=coordinates_zone(profile) or coordinates_zone({'additionalData':additional or normalized.get('additionalData') or {}})
        if zone:method='coordinates';detail='Calculated locally from published coordinates'
    if not zone:
        zone=phone_zone(profile.get('phone',''),profile.get('address',''))
        if zone:method='phone_prefix';detail='Estimated from the phone number prefix; not a confirmed physical location'
    if not zone:return False
    set_zone(payload,zone,method,detail,source_url)
    return True


def set_zone(payload,zone,method,detail,url):
    payload['profile']['timezone']=zone
    payload.setdefault('provenance',{})['timezone']=[{'url':url,'method':method}]
    payload['timezoneResolution']={'value':zone,'method':method,'detail':detail,'evidenceStatus':'inferred' if method=='phone_prefix' else 'derived'}
    payload['missingFields']=[key for key,value in payload['profile'].items() if not value]

_geo_cache={};_geo_lock=asyncio.Lock();_geo_last=0.0
async def resolve_address(payload):
    """Optional operator-owned Nominatim-compatible endpoint; no public geocoder by default."""
    base=os.getenv('GEOCODER_BASE_URL','').rstrip('/')
    address=payload['profile'].get('address','')
    if not base or not address or payload['profile'].get('timezone'):return False
    from .providers import local_json
    key=hashlib.sha256((base+'|'+address).encode()).hexdigest()
    global _geo_last
    try:
        async with _geo_lock:
            entry=_geo_cache.get(key)
            if entry and entry[0]>time.monotonic():rows=entry[1]
            else:
                await asyncio.sleep(max(0,1.1-(time.monotonic()-_geo_last)))
                try:rows=await local_json(base,'/search?'+urlencode({'q':address,'format':'jsonv2','limit':2,'accept-language':'en'}),timeout=4,limit=100000)
                finally:_geo_last=time.monotonic()
                if not isinstance(rows,list):return False
                if len(_geo_cache)>=256:_geo_cache.pop(next(iter(_geo_cache)))
                _geo_cache[key]=(time.monotonic()+86400,rows)
        zones={timezone_from_coordinates(row.get('lat'),row.get('lon')) for row in rows}-{''}
        if len(zones)!=1:return False
        set_zone(payload,next(iter(zones)),'address_geocoding','Calculated from the geocoded business address',payload['profile'].get('website',''))
        return True
    except Exception:
        return False


def published_coordinates(trace):
    extra=dict(trace.get('additionalData') or {})
    for candidate in sorted(trace.get('extraCandidates',[]),key=lambda item:(len(item['url']),item['url']),reverse=True):
        coordinates=candidate.get('data',{}).get('coordinates')
        if coordinates:extra['coordinates']=coordinates
    return extra
