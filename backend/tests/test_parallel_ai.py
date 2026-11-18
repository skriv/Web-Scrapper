import asyncio,time
from unittest.mock import AsyncMock
import pytest
from app.configuration import ScrapingSettings,trace_context
from app.models import Profile,Extraction,Location
from app import parallel_scraping,service,maps,engine
from app.timed_ai import TimedAI,source_packet
from app.providers import ProviderError

@pytest.mark.parametrize('url,query',[('https://www.bevri.com/contact','bevri'),('https://booking.my-company.co.uk/path','my company'),('https://karatesf.com','karatesf'),('http://8.8.8.8','')])
def test_domain_query(url,query):assert parallel_scraping.domain_query(url)==query

def data():return {'started':time.monotonic(),'logs':[],'raw':[],'texts':[],'sourceReady':asyncio.Event()}

async def test_domain_search_starts_before_homepage_data(monkeypatch):
    searching=asyncio.Event();started=asyncio.Event()
    monkeypatch.setattr(parallel_scraping,'assert_public_url',AsyncMock(side_effect=lambda u:u))
    async def website(url,render,follow,on_progress):
        started.set();await asyncio.wait_for(searching.wait(),1)
        found=Extraction(profile=Profile(businessName='Bevri',website=url))
        await on_progress(found,1);return found,1
    async def lookup(name,address,url):
        assert name=='bevri' and address==''
        searching.set();await started.wait();return [Location(businessName='Bevri',address='Published address')]
    monkeypatch.setattr(service,'import_website',website);monkeypatch.setattr(maps,'search_maps',lookup)
    result=await asyncio.wait_for(parallel_scraping.import_parallel('https://bevri.com',False,True),2)
    assert result['profile']['businessName']=='Bevri' and len(result['mapsCandidates'])==1

async def test_maps_partial_launches_website_before_details(monkeypatch):
    started=asyncio.Event();finished=False
    monkeypatch.setattr(parallel_scraping,'assert_public_url',AsyncMock(side_effect=lambda u:u))
    info=data();token=trace_context.set(info)
    partial=Extraction(profile=Profile(businessName='Clinic',website='https://clinic.example',address='1 Street'))
    async def get_maps(url):
        nonlocal finished
        await info['mapsPartial'](partial);await asyncio.wait_for(started.wait(),1);finished=True;return partial
    async def website(url,render,follow,on_progress):
        assert not finished;started.set()
        found=Extraction(profile=Profile(businessName='Clinic',description='Official description'))
        await on_progress(found,1);return found,1
    monkeypatch.setattr(maps,'extract_maps',get_maps);monkeypatch.setattr(service,'import_website',website)
    try:
        result=await asyncio.wait_for(parallel_scraping.import_parallel('https://www.google.com/maps/place/Clinic',False,True),2)
        assert result['profile']['description']=='Official description' and 'mapsPartial' not in info
    finally:trace_context.reset(token)

async def test_parallel_cancel_stops_both_sources(monkeypatch):
    active=[asyncio.Event(),asyncio.Event()];stopped=[False,False]
    monkeypatch.setattr(parallel_scraping,'assert_public_url',AsyncMock(side_effect=lambda u:u))
    async def wait(index):
        active[index].set()
        try:await asyncio.Event().wait()
        finally:stopped[index]=True
    async def site(*a,**kw):return await wait(0)
    async def search(*a,**kw):return await wait(1)
    monkeypatch.setattr(service,'import_website',site);monkeypatch.setattr(maps,'search_maps',search)
    task=asyncio.create_task(parallel_scraping.import_parallel('https://bevri.com',False,True))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in active)),1);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert all(stopped)

async def test_timer_runs_during_scraping_and_merges_only_supported_missing_fields():
    info=data();info['texts']=[{'url':'https://example.com','text':'Published service: Consultation'}]
    started=time.monotonic();called=asyncio.Event()
    async def model(provider,packet):
        assert not controller.done.is_set() and 'Consultation' in packet
        info['aiFieldEvidence']={'services':[{'value':'Consultation','quote':'Published service: Consultation'}]}
        called.set();return Profile(services=['Consultation'])
    controller=TimedAI(ScrapingSettings().model_copy(update={'ollamaDelaySeconds':.03}),info,started,model,AsyncMock());controller.start()
    await asyncio.wait_for(called.wait(),1);await controller.finish()
    assert time.monotonic()-started>=.025 and controller.state['trigger']=='delay'
    result={'profile':Profile(address='Official address').model_dump(),'warnings':[],'firstFoundMs':1,'lastFoundMs':1}
    controller.merge(result);assert result['profile']['services']==['Consultation']
    assert result['profile']['address']=='Official address'
    newer={'profile':Profile(services=['Published service']).model_dump(),'warnings':[],'firstFoundMs':1,'lastFoundMs':2}
    controller.merge(newer);assert newer['profile']['services']==['Published service']

async def test_early_completion_triggers_exactly_one_model_call():
    info=data();info['texts']=[{'url':'https://example.com','text':'Business source'}];call=AsyncMock(return_value=Profile())
    controller=TimedAI(ScrapingSettings(ollamaDelaySeconds=3),info,time.monotonic(),call,AsyncMock());controller.start();controller.scraping_complete()
    await asyncio.wait_for(controller.finish(),.5)
    assert controller.state['trigger']=='scraping-complete' and controller.state['startedMs']<500
    controller.scraping_complete();await controller.finish();assert call.await_count==1

async def test_timer_waits_for_actual_source_and_empty_scrape_skips_model():
    info=data();call=AsyncMock(return_value=Profile())
    controller=TimedAI(ScrapingSettings().model_copy(update={'ollamaDelaySeconds':.01}),info,time.monotonic(),call,AsyncMock());controller.start();await asyncio.sleep(.02)
    call.assert_not_called();info['texts'].append({'url':'https://example.com','text':'Business source'});info['sourceReady'].set()
    await asyncio.wait_for(controller.finish(),.5);assert call.await_count==1
    info=data();call=AsyncMock(return_value=Profile());controller=TimedAI(ScrapingSettings(),info,time.monotonic(),call,AsyncMock());controller.start();controller.scraping_complete();await controller.finish()
    call.assert_not_called();assert controller.state['status']=='skipped'

async def test_cancellation_stops_inflight_model_request():
    info=data();info['texts']=[{'url':'https://example.com','text':'Business source'}];running=asyncio.Event();stopped=asyncio.Event()
    async def slow(*args):
        running.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    controller=TimedAI(ScrapingSettings(),info,time.monotonic(),slow,AsyncMock());controller.start();controller.scraping_complete();await running.wait();await controller.close()
    assert stopped.is_set()

def test_combined_packet_is_bounded():
    info=data();info['texts']=[{'url':'https://example.com','text':'x'*10000}]
    assert len(source_packet(info,1000)[0])<=1000

async def test_missing_model_does_not_erase_scrape(monkeypatch,tmp_path):
    from app.storage import store
    monkeypatch.setattr(store,'path',tmp_path/'store.sqlite3');monkeypatch.setattr(store,'database_url','');monkeypatch.setattr(store,'redis_url','');monkeypatch.setattr(store,'ready',False)
    async def scrape(url,render,follow,on_progress):
        trace_context.get()['texts']=[{'url':url,'text':'Published Clinic'}]
        result=service.result_payload(Extraction(profile=Profile(businessName='Published Clinic'),provenance={'businessName':[{'url':url,'method':'jsonld'}]}),time.monotonic(),1)
        result.update(firstFoundMs=1,lastFoundMs=1,discoverySpanMs=0);await on_progress(result);return result
    monkeypatch.setattr(engine,'import_profile',scrape);monkeypatch.setattr(engine,'call_provider',AsyncMock(side_effect=ProviderError('Ollama is unavailable.')))
    result=await engine.execute(engine.ScrapeRequest(input='https://example.com',mode='hybrid'),None,ScrapingSettings())
    assert result['profile']['businessName']=='Published Clinic' and result['metrics']['success'] is True and result['ai']['status']=='unavailable'

def test_error_pages_and_robots_are_not_business_model_sources():
    from app.diagnostics import record_page
    info=data()
    record_page(info,'User-agent: *\nDisallow: /private','https://example.com/robots.txt',200,'scrapling')
    record_page(info,'<script type="application/ld+json">{"name":"Error page"}</script>','https://example.com/missing',404,'scrapling')
    assert not info['texts']
    assert source_packet(info,1000)[0]==''

async def test_company_query_enriches_the_unique_maps_website(monkeypatch):
    row=Location(businessName='Clinic',website='https://clinic.example',address='1 Street',sourceUrl='https://maps.google.com/?cid=1')
    monkeypatch.setattr(engine,'maps_chain',AsyncMock(return_value=[row]))
    async def website(url,render,follow,on_progress):
        assert url=='https://clinic.example'
        found=Extraction(profile=Profile(businessName='Clinic',description='Published website detail'))
        await on_progress(found,1);return found,1
    monkeypatch.setattr(service,'import_website',website)
    result=await engine.run_query('Clinic',time.monotonic(),data(),AsyncMock(),ScrapingSettings())
    assert result['profile']['description']=='Published website detail'
    assert len(result['mapsCandidates'])==1

async def test_ambiguous_company_query_does_not_crawl_multiple_candidate_sites(monkeypatch):
    rows=[Location(businessName='Clinic '+str(i),website='https://site'+str(i)+'.example') for i in range(2)]
    monkeypatch.setattr(engine,'maps_chain',AsyncMock(return_value=rows))
    request=AsyncMock();monkeypatch.setattr(service,'import_website',request)
    result=await engine.run_query('Clinic',time.monotonic(),data(),AsyncMock(),ScrapingSettings())
    assert len(result['mapsCandidates'])==2
    request.assert_not_called()

async def test_hybrid_streams_ai_then_keeps_later_deterministic_enrichment(monkeypatch,tmp_path):
    from app.storage import store
    monkeypatch.setattr(store,'path',tmp_path/'flow.sqlite3');monkeypatch.setattr(store,'database_url','');monkeypatch.setattr(store,'redis_url','');monkeypatch.setattr(store,'ready',False)
    events=[];model_done=asyncio.Event()
    async def model(provider,packet):
        trace_context.get()['aiFieldEvidence']={'services':[{'value':'Consultation','quote':'Published service: Consultation'}]}
        model_done.set();return Profile(services=['Consultation'])
    async def scrape(url,render,follow,on_progress):
        trace_context.get()['texts']=[{'url':url,'text':'Published service: Consultation'}]
        result=service.result_payload(Extraction(profile=Profile(businessName='Clinic'),provenance={'businessName':[{'url':url,'method':'jsonld'}]}),time.monotonic(),1)
        result.update(firstFoundMs=1,lastFoundMs=1,discoverySpanMs=0)
        await on_progress(result)
        await asyncio.wait_for(model_done.wait(),1)
        await asyncio.sleep(.01)
        result['profile']['services']=['Official website service']
        result['provenance']['services']=[{'url':url,'method':'jsonld'}]
        await on_progress(result);return result
    async def event(snapshot):
        import copy
        events.append(copy.deepcopy(snapshot))
    monkeypatch.setattr(engine,'import_profile',scrape);call=AsyncMock(side_effect=model);monkeypatch.setattr(engine,'call_provider',call)
    cfg=ScrapingSettings().model_copy(update={'ollamaEnabled':True,'ollamaSchedule':'after-delay','ollamaDelaySeconds':.01})
    result=await engine.execute(engine.ScrapeRequest(input='https://example.com',mode='automatic'),event,cfg)
    assert call.await_count==1
    assert any(row['profile']['services']==['Consultation'] for row in events)
    assert result['profile']['services']==['Official website service']
    assert result['provenance']['services'][0]['method']=='jsonld'
    assert result['ai']['status']=='complete'

@pytest.mark.parametrize('model,thinking',[('qwen3:4b',False),('gemma3:4b',None)])
async def test_qwen_extraction_reserves_budget_for_json_instead_of_thinking(monkeypatch,model,thinking):
    import json
    from app import providers
    from app.configuration import settings_context
    client=AsyncMock(return_value={'response':json.dumps({'businessName':{'value':'Bevri','quote':'Bevri'}})})
    monkeypatch.setattr(providers,'local_json',client)
    token=settings_context.set(ScrapingSettings(ollamaModel=model))
    try:
        result=await providers.OllamaExtractionProvider().scrape('Bevri')
        assert result.businessName=='Bevri'
        payload=client.await_args.kwargs['payload']
        assert payload.get('think') is thinking
        assert isinstance(payload['format'],dict)
    finally:settings_context.reset(token)

async def test_temporary_debug_publishes_waiting_heartbeat_and_does_not_merge_unchecked_values():
    import copy
    info=data();info['texts']=[{'url':'https://example.com','text':'Published business'}]
    snapshots=[];started=asyncio.Event();release=asyncio.Event()
    async def notify():
        snapshots.append(copy.deepcopy(controller.state))
    async def model(provider,packet):
        await info['aiProgress']('waiting-response','Waiting for Ollama response.')
        started.set();await release.wait()
        return Profile(services=['Unsupported value'])
    controller=TimedAI(ScrapingSettings(),info,time.monotonic(),model,notify)
    controller.start();controller.scraping_complete();await started.wait()
    try:
        await asyncio.sleep(1.05)
        assert any(s['debug']['stage']=='waiting-response' and s['debug']['phaseElapsedMs']>=900 for s in snapshots)
        assert not controller.profile.services
    finally:
        release.set();await controller.finish()
    assert controller.state['status']=='complete' and not controller.profile.services
    assert controller.state['debug']['stage']=='complete'
    assert 'aiProgress' not in info

async def test_provider_debug_reports_real_ollama_durations_and_validation_stages(monkeypatch):
    import json
    from app import providers
    from app.configuration import settings_context
    changes=[];info=data();info['aiProgress']=AsyncMock(side_effect=lambda stage,message,**details:changes.append((stage,details)))
    token=trace_context.set(info);cfg=settings_context.set(ScrapingSettings())
    monkeypatch.setattr(providers,'local_json',AsyncMock(return_value={'response':json.dumps({'businessName':{'value':'Bevri','quote':'Bevri'}}),'load_duration':500000000,'prompt_eval_duration':1000000000,'eval_duration':8000000000,'total_duration':9600000000,'prompt_eval_count':100,'eval_count':200}))
    try:
        assert (await providers.OllamaExtractionProvider().scrape('Bevri')).businessName=='Bevri'
        assert [s for s,d in changes]==['waiting-response','response-received','validating-json','checking-quotes']
        assert changes[1][1]['modelStats']=={'loadMs':500,'promptMs':1000,'generationMs':8000,'totalModelMs':9600,'inputTokens':100,'outputTokens':200}
    finally:
        trace_context.reset(token);settings_context.reset(cfg)
