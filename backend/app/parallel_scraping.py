"""One Maps search + one website crawl, both streaming as soon as a source is known."""
import asyncio
import ipaddress
import re
import time
from urllib.parse import urlsplit
from .configuration import settings,trace_context,trace
from .models import Extraction,Profile,Location
from .extraction import merge_extractions
from .security import assert_public_url,is_maps_url,is_short_maps_url


def domain_query(url):
    host=(urlsplit(url).hostname or '').removeprefix('www.')
    try:ipaddress.ip_address(host);return ''
    except ValueError:pass
    labels=host.split('.')
    # No network PSL lookup on the fast path. Common compound suffixes are explicit.
    compound={('co','uk'),('com','au'),('co','nz'),('co','jp'),('co','in'),('com','br'),('com','mx'),('com','tr')}
    index=-3 if len(labels)>2 and tuple(labels[-2:]) in compound else -2
    name=labels[index] if len(labels)>=abs(index) else labels[0]
    try:name=name.encode('ascii').decode('idna')
    except (UnicodeError,UnicodeEncodeError):pass
    return re.sub(r'[-_]+',' ',name).strip()[:200]

async def import_parallel(url,render_js,follow_links,on_progress=None):
    from .service import DiscoveryProgress,import_website,ImportError,verified_maps_candidates
    from .maps import search_maps,extract_maps
    from .fetching import fetch_http
    started=time.monotonic();progress=DiscoveryProgress(started,on_progress)
    url=await assert_public_url(url)
    if is_short_maps_url(url):url=(await fetch_http(url,maps_only=True)).url
    direct=is_maps_url(url)
    info=trace_context.get()
    web=Extraction();maps=Extraction();candidates=[]
    web_pages=0;maps_pages=0;maps_status='idle';warnings=[];tasks=set();website_task=None
    website_url='' if direct else url
    original_partial=info.get('mapsPartial') if info else None
    cfg=settings()

    def combined():
        if direct:
            from .timezone_resolution import same_address
            conflicting_branch=bool(web.profile.address and maps.profile.address and not same_address(web.profile.address,maps.profile.address))
            merged=merge_extractions(web,maps) if cfg.preferOfficialWebsite and not conflicting_branch else merge_extractions(maps,web)
        else:
            merged=web.model_copy(deep=True)
            verified=verified_maps_candidates(candidates,web.profile,url)
            if len(verified)==1:
                entry=verified[0]
                extra=Extraction(profile=Profile(**{k:v for k,v in entry.model_dump().items() if k in Profile.model_fields}),provenance={k:[{'url':entry.sourceUrl,'method':(info or {}).get('mapsProvider','sonagara')}] for k,v in entry.model_dump().items() if k in Profile.model_fields and v})
                merged=merge_extractions(merged,extra) if cfg.preferOfficialWebsite else merge_extractions(extra,merged)
            merged.mapsCandidates=candidates
        merged.mapsStatus=maps_status
        merged.warnings=list(dict.fromkeys(merged.warnings+warnings))
        return merged

    async def emit():await progress.publish(combined(),web_pages+maps_pages)
    async def website():
        nonlocal web,web_pages
        trace('parallel_website_start')
        async def found(value,count):
            nonlocal web,web_pages
            web=value;web_pages=count
            await emit()
        try:
            web,web_pages=await import_website(website_url,render_js,follow_links,on_progress=found)
            trace('parallel_website_end',pages=web_pages)
        except (RuntimeError,ValueError,TimeoutError) as exc:
            warnings.append(str(exc) or 'Website extraction timed out.')
            trace('parallel_website_end',status='error',error=type(exc).__name__)
        await emit()

    async def start_website(target):
        nonlocal website_task,website_url
        if website_task is not None or not target:return
        website_url=await assert_public_url(target)
        website_task=asyncio.create_task(website());tasks.add(website_task)

    async def partial_maps(value):
        nonlocal maps,maps_pages
        maps=value.model_copy(deep=True);maps_pages=1
        # Start website before awaiting Maps details, not after the provider completes.
        await start_website(value.profile.website)
        await emit()

    async def lookup():
        nonlocal maps,candidates,maps_pages,maps_status
        maps_status='searching'
        query=domain_query(url) if not direct else ''
        trace('parallel_maps_start',query=query if not direct else None)
        try:
            if direct:
                maps=await extract_maps(url);maps_pages=1;maps_status='complete'
                await start_website(maps.profile.website)
            elif query:
                candidates=await search_maps(query,'',url)
                maps_status='selection_required' if candidates else 'not_found'
            else:maps_status='not_found'
            trace('parallel_maps_end',candidates=len(candidates))
        except (RuntimeError,ValueError,TimeoutError) as exc:
            maps_status='error';warnings.append(str(exc) or 'Maps search timed out.')
            trace('parallel_maps_end',status='error',error=type(exc).__name__)
        await emit()

    try:
        if info is not None and direct:info['mapsPartial']=partial_maps
        if not direct:await start_website(url)
        if direct or not (info or {}).get('skipMaps'):
            tasks.add(asyncio.create_task(lookup()))
        else:maps_status='disabled'
        while tasks:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            tasks.difference_update(done)
            # A Maps partial callback can add the website task while Maps is still active.
            for task in done:await task
        result=combined()
        if not any(result.profile.model_dump().values()) and not candidates:raise ImportError('; '.join(warnings) or 'No business data found.')
        await progress.publish(result,web_pages+maps_pages)
        return progress.payload(result,web_pages+maps_pages)
    finally:
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if info is not None:
            if original_partial is None:info.pop('mapsPartial',None)
            else:info['mapsPartial']=original_partial
