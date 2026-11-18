"""Bounded source capture and explicitly heuristic evidence, not probability."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup
from .models import CONTACT_FIELDS

SENSITIVE=re.compile(r'token|key|secret|password|auth|nonce|signature|session|credential',re.I)
def safe_url(value):
    try:
        p=urlsplit(value)
        return urlunsplit((p.scheme,p.netloc.split('@')[-1],p.path,urlencode([(k,'[redacted]' if SENSITIVE.search(k) else v) for k,v in parse_qsl(p.query)]),''))
    except ValueError: return '[invalid URL]'

def record_page(trace,html,url,status,provider):
    if len(trace['raw'])>=25: return
    soup=BeautifulSoup(html[:1000000],'html.parser')
    structured=[]
    for tag in soup.select('script[type="application/ld+json"]')[:12]:
        try: structured.append(json.loads((tag.string or tag.get_text())[:100000]))
        except (ValueError,TypeError): continue
    for tag in soup(['script','style','noscript']): tag.decompose()
    text=soup.get_text(' ',strip=True)[:12000]
    trace['raw'].append({'provider':provider,'sourceUrl':safe_url(url),'status':status,'structuredData':structured,'textExcerpt':text[:4000]})
    if 200 <= status < 300 and text.strip() and urlsplit(url).path.rstrip('/') != '/robots.txt':
        trace.setdefault('texts',[]).append({'url':url,'text':text})
        if trace.get('sourceReady') is not None: trace['sourceReady'].set()
    extra=trace.setdefault('additionalData',{})
    page_extra={}
    email_text=text+' '+ ' '.join(a.get('href','')[7:].split('?')[0] for a in soup.select('a[href^="mailto:"]'))
    emails=re.findall(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',email_text)
    if emails:
        page_extra['emails']=list(dict.fromkeys(emails))[:20]
        extra['emails']=list(dict.fromkeys(extra.get('emails',[])+emails))[:20]
    social=[tag.get('href','') for tag in soup.select('a[href]') if re.search(r'^https?://(www\.)?(facebook.com|instagram.com|linkedin.com|youtube.com|x.com)/',tag.get('href',''))]
    if social:
        page_extra['socialProfiles']=list(dict.fromkeys(social))[:20]
        extra['socialProfiles']=list(dict.fromkeys(extra.get('socialProfiles',[])+social))[:20]
    details,services=structured_details(structured)
    # Keep explicit source metadata; stable ordering resolves repeated page claims.
    trace.setdefault('extraCandidates',[]).append({'url':safe_url(url),'provider':provider,'data':{**page_extra,**details},'services':services})

def enrich_response(result,trace):
    timestamp=datetime.now(timezone.utc).isoformat()
    profile=result['profile']
    additional=dict(trace.get('additionalData',{})); services={}
    extra_sources={}
    for candidate in sorted(trace.get('extraCandidates',[]),key=lambda v:(len(urlsplit(v['url']).path),v['url']),reverse=True):
        additional.update(candidate['data']); services.update(candidate['services'])
        extra_sources.update({k:candidate for k in candidate['data']})
    if result.get('industries'):additional['industries']=result['industries']
    if result.get('navigationOfferings'):additional['navigationOfferings']=result['navigationOfferings']
    result['businessProfile']={
        'businessName':profile['businessName'] or None,'businessDescription':profile['description'] or None,
        'address':profile['address'] or None,'businessPhone':profile['phone'] or None,'website':profile['website'] or None,
        'timeZone':profile['timezone'] or None,'openingHours':profile['openingHours'],
        'services':[services.get(s.casefold(),{'name':s,'description':None,'price':None}) for s in profile['services']],
        'paymentMethods':profile['paymentMethods'],'additionalData':additional,
        **{key:profile.get(key,'') for key in CONTACT_FIELDS},
    }
    providers={item.get('sourceUrl'):item.get('provider') for item in trace['raw']}
    result['fieldEvidence']={}
    for key,value in profile.items():
        if not value: continue
        sources=result.get('provenance',{}).get(key,[])
        result['fieldEvidence'][key]={'value':value,'sources':[{
            'sourceUrl':safe_url(source['url']), 'provider': providers.get(safe_url(source['url'])) or (source.get('method') if source.get('method') in {'gosom','sonagara','ollama','ollama-inferred'} else 'local' if source.get('method') in {'coordinates','location_timezone','location_consensus','phone_prefix','address_geocoding','explicit_timezone'} else 'scrapling'),
            'extractionMethod':source.get('method','unknown'),'extractedAt':timestamp,
            'evidenceStatus':'published-industry-target' if source.get('method')=='navigation-industry' else 'published-navigation' if source.get('method')=='navigation-service' else 'inferred-ai' if source.get('method')=='ollama-inferred' else 'generated-template' if source.get('method')=='generated-template' else 'inferred-phone-prefix' if source.get('method')=='phone_prefix' else 'derived-from-location' if source.get('method') in {'coordinates','location_timezone','location_consensus','address_geocoding'} else 'explicit-structured' if source.get('method') in {'jsonld','microdata'} else 'source-supported-ai' if source.get('method')=='ollama' else 'published-provider' if source.get('method') in {'gosom','sonagara'} else 'parsed-heuristic',
        } for source in sources]}
    for key,value in additional.items():
        source=extra_sources.get(key,{})
        result['fieldEvidence']['additionalData.'+key]={'value':value,'sources':[{'sourceUrl':source.get('url',''),'provider':source.get('provider','sonagara'),'extractionMethod':'jsonld' if source else 'maps-provider','extractedAt':timestamp,'evidenceStatus':'parsed-published-data'}]}
    for key in ('industries','navigationOfferings'):
        if key not in additional:continue
        rows=[row for row in result.get('navigationOfferings',[]) if key!='industries' or row.get('kind')=='industry']
        result['fieldEvidence']['additionalData.'+key]={'value':additional[key],'sources':[{'sourceUrl':row['sourceUrl'],'provider':'scrapling','extractionMethod':row['method'],'extractedAt':timestamp,'evidenceStatus':'published-industry-target' if row['kind']=='industry' else 'published-navigation'} for row in rows]}
    result['confidenceNote']='Evidence categories describe extraction methods; they are not calibrated probabilities.'
    return result

def redact(value):
    if isinstance(value,dict):
        return {key:'[redacted]' if SENSITIVE.search(key) else redact(item) for key,item in value.items()}
    if isinstance(value,list):return [redact(item) for item in value]
    if isinstance(value,str):
        return re.sub(r'https?://[^\s<>"\']+',lambda match:safe_url(match[0]),value)
    return value

def structured_details(nodes):
    additional={}; services={}
    def walk(node):
        if isinstance(node,list):
            for child in node:walk(child)
        if not isinstance(node,dict):return
        geo=node.get('geo')
        if isinstance(geo,dict):
            try:
                lat,lon=float(geo['latitude']),float(geo['longitude'])
                if -90<=lat<=90 and -180<=lon<=180:additional['coordinates']={'latitude':lat,'longitude':lon}
            except (KeyError,ValueError,TypeError):pass
        rating=node.get('aggregateRating')
        if isinstance(rating,dict):
            try:
                val=float(rating.get('ratingValue'))
                if 0<=val<=5:additional['rating']=val
                count=int(rating.get('reviewCount') or rating.get('ratingCount') or 0)
                if count>0:additional['reviewCount']=count
            except (ValueError,TypeError):pass
        types=node.get('@type',[]);types=[types] if isinstance(types,str) else types
        if 'Service' in types and isinstance(node.get('name'),str):
            offer=node.get('offers',{})
            if isinstance(offer,list):offer=offer[0] if offer else {}
            price=offer.get('price') if isinstance(offer,dict) else None
            services[node['name'].casefold()]={'name':node['name'],'description':node.get('description') if isinstance(node.get('description'),str) and not re.search(r'[\u0370-\u06ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]',node['description']) else None,'price':str(price)+' '+str(offer.get('priceCurrency','')) if isinstance(price,(str,int,float)) else None}
        same=node.get('sameAs',[]);same=[same] if isinstance(same,str) else same
        if isinstance(same,list):
            social=[v for v in same if isinstance(v,str) and re.search(r'^https?://(www\.)?(facebook.com|instagram.com|linkedin.com|youtube.com|x.com)/',v)]
            if social:additional['socialProfiles']=social
        for child in node.values():
            if isinstance(child,(dict,list)):walk(child)
    walk(nodes)
    return additional,services
