"""Promote a uniquely matching Maps record into empty form fields, with evidence."""
import re
from urllib.parse import urlsplit
from .models import Profile

def identity(value):return re.sub(r'\W+','',str(value).casefold())
def host(value):
    try:return (urlsplit(value).hostname or '').removeprefix('www.').lower()
    except ValueError:return ''

def complete_profile(payload,provider='sonagara'):
    profile=payload['profile']
    rows=[row for row in payload.get('mapsCandidates',[]) if isinstance(row,dict)]
    name=identity(profile.get('businessName',''))
    phone=identity(profile.get('phone',''))
    website=host(profile.get('website',''))
    matches=[row for row in rows if (name and name==identity(row.get('businessName',''))) or (phone and phone==identity(row.get('phone',''))) or (website and website==host(row.get('website','')))]
    if not name and not phone and not website and len(rows)==1:matches=rows
    # Multiple branches or unrelated search results require a deliberate selection.
    signatures={(identity(row.get('address','')),identity(row.get('phone',''))) for row in matches}
    chosen=matches[0] if matches and len(signatures)==1 else None
    if chosen and profile.get('address') and chosen.get('address') and identity(profile['address'])!=identity(chosen['address']) and not (phone and phone==identity(chosen.get('phone',''))):chosen=None
    filled=[]
    if chosen:
        for field in Profile.model_fields:
            value=chosen.get(field)
            if not profile.get(field) and value:
                profile[field]=value
                payload.setdefault('provenance',{})[field]=[{'url':chosen.get('sourceUrl',''),'method':provider}]
                filled.append(field)
    if filled:
        payload['mapsStatus']='filled_missing_fields'
    payload['mapsFilledFields']=list(dict.fromkeys(payload.get('mapsFilledFields',[])+filled))
    payload['missingFields']=[field for field in Profile.model_fields if not profile.get(field)]
    locations=payload.get('locations') or []
    addresses={identity(row.get('address','')) for row in locations if row.get('address')}
    payload['locationCount']=len(addresses) or (1 if profile.get('address') else 0)
    return payload
