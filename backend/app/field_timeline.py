"""Per-job, server-observed discoveries. Never infer timings from the final payload."""
import copy
from urllib.parse import urlsplit
from .models import CONTACT_FIELDS,CORE_FIELDS

REPORT_FIELDS=CONTACT_FIELDS+tuple(k for k in CORE_FIELDS if k not in {'businessName','phone'})

def origin(source):
    method=source.get('method','').lower();url=source.get('url','')
    host=(urlsplit(url).hostname or '').lower()
    if method=='generated-template':return 'generated'
    if method in {'ollama','ollama-inferred'}:return 'llm'
    if method in {'gosom','sonagara','maps'} or (host in {'maps.google.com','maps.app.goo.gl'} or (host.endswith('google.com') and urlsplit(url).path.startswith('/maps'))):return 'maps'
    if method in {'coordinates','phone_prefix','location_timezone','location_consensus','address_geocoding','explicit_timezone'}:return 'derived'
    if urlsplit(url).scheme in {'http','https'}:return 'web'
    return 'unknown'

class FieldTimeline:
    def __init__(self):
        self.rows=[];self.previous={}

    def observe(self,payload,elapsed_ms,cache=False):
        profile=payload.get('profile',{});provenance=payload.get('provenance',{})
        for field in REPORT_FIELDS:
            value=profile.get(field)
            if not value:continue
            sources=provenance.get(field,[])
            if field=='source':sources=provenance.get('businessName',[]) or sources
            source=sources[0] if sources else {}
            category='cache' if cache else origin(source)
            signature=(value,category,source.get('url',''),source.get('method',''))
            if signature==self.previous.get(field):continue
            self.rows.append({'sequence':len(self.rows)+1,'field':field,'value':copy.deepcopy(value),'elapsedMs':elapsed_ms,
                'source':category,'sourceUrl':source.get('url',''),'method':source.get('method','unknown'),
                'event':'updated' if field in self.previous else 'found'})
            self.previous[field]=copy.deepcopy(signature)
        payload['fieldTimeline']=copy.deepcopy(self.rows)
        payload['fieldTimelineNote']='Server-observed availability since request start. Equal times mean the same snapshot. Updates retain original discoveries; generated greetings are labeled separately. Missing fields have no fabricated discovery time.'
        return payload
