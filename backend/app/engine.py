"""Version 2 orchestration around the existing deterministic ten-page extractor."""
import asyncio
import copy
import re
import time
from datetime import datetime,timezone
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .timed_ai import TimedAI
from .profile_completion import complete_profile
from .timezone_resolution import resolve_local,resolve_address,published_coordinates
from .configuration import ScrapingSettings, settings_context, trace_context, trace
from .diagnostics import enrich_response,record_page,safe_url,redact
from .storage import store,cache_key
from .providers import REGISTRY,ProviderError,call_provider,maps_chain
from .models import Extraction,Profile,Location,CORE_FIELDS
from .contact_fields import complete_contacts
from .field_timeline import FieldTimeline
from .service import import_profile,result_payload,DiscoveryProgress
from .security import normalize_url,is_maps_url,assert_public_url

class ScrapeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    input: str = Field(min_length=1,max_length=4096)
    mode: Literal['automatic','single','compare','deep','fast','hybrid']='automatic'
    provider: Literal['sonagara','gosom','crw','scrapling','crawl4ai','playwright','ollama','overture','osm','foursquare'] | None=None
    providers: list[Literal['sonagara','gosom','crw','scrapling','crawl4ai','playwright','ollama','overture','osm','foursquare']] = Field(default_factory=list,max_length=4)
    repeats: int=Field(default=3,ge=1,le=5)
    bypassCache: bool | None=None
    renderJs: bool | None=None
    followLinks: bool=True

def detect_input(raw):
    value=raw.strip()
    if not value or any(ord(c)<32 for c in value): raise ValueError('Enter a URL, place ID, or company search query.')
    if re.fullmatch(r'ChIJ[\w-]{8,250}',value):
        return 'place-id','https://www.google.com/maps/search/?api=1&query=Place&query_place_id='+value
    if '://' in value or ('.' in value and not any(c.isspace() for c in value)):
        value=normalize_url(value)
        return ('maps' if is_maps_url(value) else 'website'),value
    if len(value)>300: raise ValueError('Company queries are limited to 300 characters.')
    return 'query',value

async def load_settings():
    data=await store.get('settings','current')
    return ScrapingSettings.model_validate(data or {})

async def save_settings(data: ScrapingSettings):
    await store.put('settings','current',data.model_dump())
    return data

async def execute(request: ScrapeRequest, on_progress=None, cfg=None):
    cfg=(cfg or await load_settings()).model_copy(deep=True)
    if request.renderJs is not None: cfg.renderJs=request.renderJs
    if request.mode=='hybrid': cfg.ollamaEnabled=True; cfg.ollamaSchedule='after-delay'
    if request.mode=='fast': cfg.renderJs=False; cfg.ollamaEnabled=False
    # Deep mode uses configured AI/JS opt-ins rather than silently enabling heavyweight engines.
    kind,value=detect_input(request.input)
    started=time.monotonic()
    data={'started':started,'logs':[],'raw':[],'httpRequests':0,'fallbacks':0,'retries':0,'ollamaMs':0,'mapsDerived':kind in {'maps','place-id'},'httpPoolLock':asyncio.Lock(),'sourceReady':asyncio.Event()}
    data['skipWebsiteEnrichment']=request.mode in {'fast','single','compare'}
    data['skipMaps']=request.mode=='single' or (request.mode=='fast' and kind=='website')
    st=settings_context.set(cfg); tt=trace_context.set(data)
    progress=DiscoveryProgress(started,None)
    last_snapshot=None
    timed_ai=None
    field_timeline=FieldTimeline()
    async def publish(snapshot):
        nonlocal last_snapshot
        complete_profile(snapshot,data.get('mapsProvider','sonagara'))
        resolve_local(snapshot,published_coordinates(data))
        if timed_ai is not None:
            timed_ai.observe(snapshot)
            timed_ai.merge(snapshot)
        if snapshot.get('firstFoundMs') is not None:
            data['firstUsefulMs']=min(data.get('firstUsefulMs',snapshot['firstFoundMs']),snapshot['firstFoundMs'])
            snapshot['firstFoundMs']=data['firstUsefulMs']
            if snapshot.get('lastFoundMs') is not None:
                snapshot['discoverySpanMs']=max(0,snapshot['lastFoundMs']-snapshot['firstFoundMs'])
        complete_contacts(snapshot)
        field_timeline.observe(snapshot,round((time.monotonic()-started)*1000))
        last_snapshot=copy.deepcopy(snapshot)
        enrich_response(snapshot,data)
        snapshot['metrics']=metrics(snapshot,data,started,'miss')
        snapshot['logs']=copy.deepcopy(data['logs'])
        if on_progress: await on_progress(snapshot)
    async def partial_maps(found):
        await progress.publish(found,1)
        await publish(progress.payload(found,1))
    async def ai_status_changed():
        if last_snapshot is not None: await publish(copy.deepcopy(last_snapshot))
    data['mapsPartial']=partial_maps
    try:
        trace('input_detected',kind=kind,mode=request.mode)
        bypass=request.bypassCache if request.bypassCache is not None else cfg.bypassCacheForTesting
        key=cache_key({'schema':10,'input':value,'settings':cfg.model_dump(),'mode':request.mode,'provider':request.provider,'followLinks':request.followLinks})
        eligible=kind=='website' and request.mode not in {'single','compare'}
        if cfg.cacheEnabled and not bypass and eligible and cfg.cacheStrategy=='cache-first':
            hit,backend=await store.cache_get(key)
            if hit:
                trace('cache_hit',backend=backend)
                hit['cachedExtractionMetrics']=hit.get('metrics')
                hit['metrics']={'cache':'hit','cacheBackend':backend,'totalMs':round((time.monotonic()-started)*1000),'httpRequests':0,'fallbacks':0,'retries':0,'ollamaMs':0,'peakMemoryBytes':None,'success':True,'fieldsExtracted':sum(bool(hit['profile'].get(k)) for k in CORE_FIELDS),'completeness':sum(bool(hit['profile'].get(k)) for k in CORE_FIELDS)/9,'firstFieldMs':round((time.monotonic()-started)*1000),'firstPartialMs':round((time.monotonic()-started)*1000),'pagesVisited':0}
                hit['logs']=data['logs']; hit['raw']=[]
                hit['elapsedMs']=hit['metrics']['totalMs']
                hit['firstFoundMs']=hit['lastFoundMs']=hit['elapsedMs']; hit['discoverySpanMs']=0
                hit['cachedFieldTimeline']=hit.pop('fieldTimeline',[])
                field_timeline.observe(hit,hit['elapsedMs'],cache=True)
                if on_progress: await on_progress(hit)
                return hit
        trace('cache_bypass' if bypass else 'cache_miss')
        if cfg.ollamaEnabled and request.mode not in {'fast','single','compare'}:
            data['aiAfterScraping']=cfg.ollamaSchedule=='after-scraping'
            timed_ai=TimedAI(cfg,data,started,call_provider,ai_status_changed)
            timed_ai.start()
        async with asyncio.timeout(240):
            if request.mode=='single':
                if not request.provider: raise ValueError('Select a provider for Single Engine mode.')
                provider=REGISTRY[request.provider]
                if ('maps' in provider.capabilities or 'discovery' in provider.capabilities) and kind=='website': raise ValueError('This provider needs a Maps URL or company query.')
                if 'website' in provider.capabilities and kind!='website': raise ValueError('This provider needs a website URL.')
                if 'browser' in provider.capabilities and 'website' in provider.capabilities and not cfg.renderJs: raise ValueError('Enable JavaScript rendering before testing browser engines.')
                if 'website' in provider.capabilities:
                    from .fetching import robots_allowed
                    allowed,_=await robots_allowed(await assert_public_url(value))
                    if not allowed: raise ProviderError('This URL is disallowed by robots.txt.')
                if request.provider=='ollama':
                    if kind!='website': raise ValueError('For Ollama, enter a website URL to provide source text.')
                    from .fetching import fetch_http,robots_allowed
                    allowed,_=await robots_allowed(await assert_public_url(value))
                    if not allowed: raise ProviderError('This URL is disallowed by robots.txt.')
                    from bs4 import BeautifulSoup
                    source=await fetch_http(value)
                    raw=await call_provider('ollama',BeautifulSoup(source.html,'html.parser').get_text(' ',strip=True))
                else:
                    from .security import is_short_maps_url
                    if kind=='maps' and is_short_maps_url(value):
                        from .fetching import fetch_http
                        value=(await fetch_http(value,maps_only=True)).url
                    raw=await call_provider(request.provider,value)
                result=await normalize_provider(raw,value,started,data)
                await progress.publish(Extraction.model_validate({k:v for k,v in result.items() if k in Extraction.model_fields}),result['pagesFetched'])
                result.update({k:v for k,v in progress.payload(Extraction.model_validate({k:v for k,v in result.items() if k in Extraction.model_fields}),result['pagesFetched']).items() if k in {'firstFoundMs','lastFoundMs','discoverySpanMs'}})
                await publish(result)
            elif kind=='website' or kind in {'maps','place-id'}:
                # Existing importer streams website facts before its one parallel Maps lookup completes.
                result=await import_profile(value,cfg.renderJs,request.followLinks and request.mode!='fast',on_progress=publish)
            else:
                result=await run_query(value,started,data,publish,cfg)
            if timed_ai is not None:
                timed_ai.scraping_complete()
                await timed_ai.finish()
                timed_ai.merge(result)
                await publish(result)
            if timed_ai is None and cfg.ollamaEnabled and request.mode!='fast' and request.mode!='single' and any(not result['profile'].get(k) for k in CORE_FIELDS):
                for source in data.get('texts',[])[:3]:
                    try:
                        ai=await call_provider('ollama',source['text'])
                        added=False
                        for field,val in ai.model_dump().items():
                            if val and not result['profile'][field]:
                                added=True
                                result['profile'][field]=val
                                result.setdefault('provenance',{})[field]=[{'url':source['url'],'method':'ollama'}]
                        from .english import english_content
                        checked=Extraction(profile=Profile(**result['profile']),provenance=result.get('provenance',{}))
                        await english_content(checked)
                        result['profile']=checked.profile.model_dump()
                        result['provenance']=checked.provenance
                        result.setdefault('warnings',[]).extend(checked.warnings)
                        result['missingFields']=[k for k,v in result['profile'].items() if not v]
                        if added:
                            at=round((time.monotonic()-started)*1000)
                            if result.get('firstFoundMs') is None: result['firstFoundMs']=at
                            result['lastFoundMs']=at; result['discoverySpanMs']=at-result['firstFoundMs']
                        await publish(result)
                    except ProviderError as exc:
                        result.setdefault('warnings',[]).append(str(exc)); break
        if timed_ai is not None: timed_ai.merge(result)
        complete_profile(result,data.get('mapsProvider','sonagara'))
        resolve_local(result,published_coordinates(data))
        if not result['profile'].get('timezone') and await resolve_address(result):
            at=round((time.monotonic()-started)*1000)
            if result.get('firstFoundMs') is None: result['firstFoundMs']=at
            result['lastFoundMs']=at;result['discoverySpanMs']=at-result['firstFoundMs']
            await publish(result)
        complete_contacts(result)
        result['elapsedMs']=round((time.monotonic()-started)*1000)
        result['missingFields']=[k for k,v in result['profile'].items() if not v]
        if data.get('firstUsefulMs') is not None:
            result['firstFoundMs']=min(data['firstUsefulMs'],result.get('firstFoundMs') if result.get('firstFoundMs') is not None else data['firstUsefulMs'])
            if result.get('lastFoundMs') is not None: result['discoverySpanMs']=max(0,result['lastFoundMs']-result['firstFoundMs'])
        result['metrics']=metrics(result,data,started,'bypass' if bypass else 'miss')
        # Update discovery clocks after evidenced AI additions as well.
        if data.get('ollamaMs') and last_snapshot and result['profile'] != last_snapshot['profile']:
            result['lastFoundMs']=result['elapsedMs']; result['discoverySpanMs']=result['lastFoundMs']-(result.get('firstFoundMs') or 0)
        field_timeline.observe(result,round((time.monotonic()-started)*1000))
        enrich_response(result,data)
        trace('complete',fields=result['metrics']['fieldsExtracted'],pages=result['metrics']['pagesVisited'])
        result['logs']=redact(data['logs']); result['raw']=redact(data['raw'])
        if result['metrics']['success'] and eligible and not data['mapsDerived'] and not result.get('mapsCandidates'):
            if cfg.cacheEnabled and not bypass: await store.cache_put(key,result,cfg.cacheTtl)
            await store.put('history',str(time.time_ns()),{'input':safe_url(value),'createdAt':datetime.now(timezone.utc).isoformat(),'result':result},7*86400)
        return result
    finally:
        if timed_ai is not None: await timed_ai.close()
        if data.get('httpPool') is not None:
            await data['httpPool'].__aexit__(None,None,None)
        settings_context.reset(st); trace_context.reset(tt)

def metrics(result,data,started,cache):
    fields=sum(bool(result['profile'].get(k)) for k in CORE_FIELDS)
    return {'firstFieldMs':result.get('firstFoundMs'),'firstPartialMs':result.get('firstFoundMs'),
            'totalMs':round((time.monotonic()-started)*1000),'httpRequests':None if data.get('unmeasuredHttp') else data['httpRequests'],
            'instrumentedHttpRequests':data['httpRequests'],'pagesVisited':result.get('htmlPagesFetched',result.get('pagesFetched',0)),
            'fieldsExtracted':fields,'completeness':fields/9,'success':bool(result['profile'].get('businessName') or result.get('mapsCandidates') or result.get('discoveryCandidates')),
            'fallbacks':data['fallbacks'],'retries':data['retries'],'cache':cache,'ollamaMs':data['ollamaMs'],'peakMemoryBytes':None,
            'memoryNote':'Per-job peak RSS is unavailable in the shared process.'}

async def normalize_provider(raw,value,started,data):
    from .extraction import extract_html
    from .english import english_content
    from .fetching import Page
    discovery=[]
    if isinstance(raw,Page):
        if raw.status>=400: raise ProviderError(f'Source returned HTTP {raw.status}.')
        found=extract_html(raw.html,raw.url)
        from .locations import html_locations
        found.locations=html_locations(raw.html,raw.url)
        if raw.method!='http': record_page(data,raw.html,raw.url,raw.status,raw.method)
    elif isinstance(raw,Extraction): found=raw
    elif isinstance(raw,Profile): found=Extraction(profile=raw)
    elif isinstance(raw,list):
        found=Extraction()
        if raw and isinstance(raw[0],Location):
            found.mapsCandidates=raw
            if len(raw)==1:
                found.profile=Profile(**{k:v for k,v in raw[0].model_dump().items() if k in Profile.model_fields})
                found.provenance={k:[{'url':raw[0].sourceUrl,'method':'sonagara' if not data.get('unmeasuredHttp') else 'gosom'}] for k,v in found.profile.model_dump().items() if v}
        else: discovery=raw
    else: raise ProviderError('Unsupported provider result.')
    await english_content(found)
    if isinstance(raw,Page): found.htmlPagesFetched=1
    output=result_payload(found,started,1)
    output.update(firstFoundMs=round((time.monotonic()-started)*1000) if any(found.profile.model_dump().values()) or found.mapsCandidates or discovery else None,lastFoundMs=None,discoverySpanMs=None)
    output['lastFoundMs']=output['firstFoundMs']; output['discoverySpanMs']=0 if output['firstFoundMs'] is not None else None
    if discovery: output['discoveryCandidates']=discovery
    return output

async def run_query(value,started,data,publish,cfg):
    output=result_payload(Extraction(),started,0)
    output.update(firstFoundMs=None,lastFoundMs=None,discoverySpanMs=None)
    async def maps(): return await maps_chain(value)
    async def discovery(provider): return await call_provider(provider,value)
    async def enrich_query_website(target,base):
        from .service import import_website
        from .extraction import merge_extractions
        from .timezone_resolution import same_address
        trace('parallel_website_start')
        async def found(website,count):
            prior=copy.deepcopy(output['profile'])
            conflict=bool(website.profile.address and base.profile.address and not same_address(website.profile.address,base.profile.address))
            merged=merge_extractions(website,base) if cfg.preferOfficialWebsite and not conflict else merge_extractions(base,website)
            payload=result_payload(merged,started,1+count)
            output.update({k:v for k,v in payload.items() if k not in {'mapsCandidates','mapsStatus','warnings'}})
            output['warnings']=list(dict.fromkeys(output.get('warnings',[])+payload['warnings']))
            if output['profile']!=prior:
                now=round((time.monotonic()-started)*1000)
                if output['firstFoundMs'] is None:output['firstFoundMs']=now
                output['lastFoundMs']=now;output['discoverySpanMs']=now-output['firstFoundMs']
            await publish(output)
        try:
            website,count=await import_website(target,cfg.renderJs,True,on_progress=found)
            await found(website,count)
            trace('parallel_website_end',pages=count)
        except (RuntimeError,ValueError,TimeoutError) as exc:
            output['warnings'].append(str(exc) or 'Website enrichment timed out.')
            trace('parallel_website_end',status='error',error=type(exc).__name__)
    tasks={asyncio.create_task(maps()):'maps'}
    for provider,enabled in [('osm',cfg.osmEnabled),('overture',cfg.overtureEnabled),('foursquare',cfg.foursquareEnabled)]:
        if enabled: tasks[asyncio.create_task(discovery(provider))]=provider
    try:
        while tasks:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                provider=tasks.pop(task)
                try:
                    if provider=='website':
                        await task
                        continue
                    normalized=await normalize_provider(await task,value,started,data)
                    if provider=='maps':
                        output.update({k:v for k,v in normalized.items() if k!='warnings'})
                        if cfg.bidirectionalSearch and not data.get('skipWebsiteEnrichment') and output['profile'].get('website'):
                            base=Extraction(profile=Profile(**output['profile']),provenance=copy.deepcopy(output.get('provenance',{})))
                            tasks[asyncio.create_task(enrich_query_website(output['profile']['website'],base))]='website'
                    else: output.setdefault('discoveryCandidates',[]).extend(normalized.get('discoveryCandidates',[]))
                    if output.get('mapsCandidates') or output.get('discoveryCandidates'):
                        now=round((time.monotonic()-started)*1000)
                        if output['firstFoundMs'] is None: output['firstFoundMs']=now
                        output['lastFoundMs']=now; output['discoverySpanMs']=now-output['firstFoundMs']
                    await publish(output)
                except ProviderError as exc: output['warnings'].append(str(exc))
        if not output.get('mapsCandidates') and not output.get('discoveryCandidates'): raise ProviderError('; '.join(output['warnings']) or 'No matching businesses found.')
        return output
    finally:
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)

def percentile(values,p):
    values=sorted(values)
    if not values: return None
    index=(len(values)-1)*p
    low=int(index); high=min(low+1,len(values)-1)
    return round(values[low]+(values[high]-values[low])*(index-low),2)
