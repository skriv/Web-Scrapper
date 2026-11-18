import asyncio
import json
import time
from unittest.mock import AsyncMock
import pytest
import httpx
from app.configuration import ScrapingSettings,settings_context,trace_context
from app import engine,providers,scraping_api
from app.storage import Storage,cache_key,store
from app.models import Extraction,Profile
from app.service import result_payload
from app.main import app

@pytest.fixture(autouse=True)
def isolated_store(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'path',tmp_path/'test.sqlite3')
    monkeypatch.setattr(store,'database_url','');monkeypatch.setattr(store,'redis_url','');monkeypatch.setattr(store,'ready',False)

@pytest.mark.parametrize('value,kind',[
    ('bevri.com','website'),('https://example.com/contact','website'),('https://maps.app.goo.gl/test','maps'),
    ('https://www.google.com/maps/place/Bevri/','maps'),('ChIJabcdefghi12345','place-id'),('Bevri Palo Alto','query')])
def test_input_detection(value,kind): assert engine.detect_input(value)[0]==kind
@pytest.mark.parametrize('value',['file:///etc/passwd','https://user:pass@example.com','https://example.com:8080','a\nb',''])
def test_invalid_input(value):
    with pytest.raises(ValueError): engine.detect_input(value)

async def test_settings_survive_new_store_instance():
    cfg=ScrapingSettings(maxPages=7,renderJs=False,mapsPrimary='gosom')
    await engine.save_settings(cfg)
    another=Storage();another.path=store.path
    assert (await another.get('settings','current'))['maxPages']==7
    assert (await engine.load_settings()).mapsPrimary=='gosom'

async def test_cache_expiry_clear_and_namespace():
    await store.cache_put('one',{'profile':{'a':1}},10)
    assert (await store.cache_get('one'))[0]['profile']['a']==1
    await store.put('cache','expired',{'v':1},-1)
    assert await store.get('cache','expired') is None
    await store.clear_cache()
    assert (await store.cache_get('one'))[0] is None
    assert cache_key({'a':1,'b':2})==cache_key({'b':2,'a':1})

async def test_maps_fallback_and_configuration(monkeypatch):
    async def call(provider,value):
        if provider=='sonagara': raise providers.ProviderError('blocked')
        return ['real-shape-result']
    mocked=AsyncMock(side_effect=call);monkeypatch.setattr(providers,'call_provider',mocked)
    token=settings_context.set(ScrapingSettings())
    try:
        assert await providers.maps_chain('query')==['real-shape-result']
        assert [c.args[0] for c in mocked.await_args_list]==['sonagara','gosom']
    finally:settings_context.reset(token)
    token=settings_context.set(ScrapingSettings(mapsFallbackEnabled=False))
    try:
        with pytest.raises(providers.ProviderError):await providers.maps_chain('query')
    finally:settings_context.reset(token)

async def test_provider_timeout_and_disabled(monkeypatch):
    class Slow:
        name='Slow';capabilities=['website']
        async def scrape(self,v):await asyncio.sleep(.1)
    monkeypatch.setitem(providers.REGISTRY,'scrapling',Slow())
    cfg=ScrapingSettings().model_copy(update={'requestTimeout':.005})
    token=settings_context.set(cfg)
    try:
        with pytest.raises(providers.ProviderError,match='timed out'): await providers.call_provider('scrapling','https://example.com')
    finally:settings_context.reset(token)
    token=settings_context.set(ScrapingSettings(enabledProviders=[]))
    try:
        with pytest.raises(providers.ProviderError,match='disabled'): await providers.call_provider('scrapling','https://example.com')
    finally:settings_context.reset(token)

async def test_progress_cache_and_maps_not_persisted(monkeypatch):
    calls=[]
    async def importer(url,render,follow,on_progress):
        calls.append(url)
        extraction=Extraction(profile=Profile(businessName='Published name',website=url),provenance={'businessName':[{'url':url,'method':'jsonld'}]})
        snapshot=result_payload(extraction,time.monotonic(),1)
        snapshot.update(firstFoundMs=1,lastFoundMs=1,discoverySpanMs=0)
        await on_progress(snapshot)
        return snapshot
    monkeypatch.setattr(engine,'import_profile',importer)
    snapshots=[]
    async def progress(s):snapshots.append(s)
    req=engine.ScrapeRequest(input='https://example.com',mode='fast',bypassCache=False)
    first=await engine.execute(req,progress,ScrapingSettings())
    second=await engine.execute(req,None,ScrapingSettings())
    assert len(calls)==1 and snapshots
    assert first['metrics']['cache']=='miss' and second['metrics']['cache']=='hit'
    assert first['fieldEvidence']['businessName']['sources'][0]['evidenceStatus']=='explicit-structured'
    assert second['metrics']['httpRequests']==0
    assert len(await store.history())==1
    maps_req=engine.ScrapeRequest(input='https://www.google.com/maps/place/Clinic',bypassCache=False)
    await engine.execute(maps_req,None,ScrapingSettings())
    assert len(await store.history())==1

async def test_ollama_rejects_unsupported_values(monkeypatch):
    async def response(*a,**kw):
        return {'response':json.dumps({'businessName':{'value':'Actual Clinic','quote':'Actual Clinic'},'phone':{'value':'+15555555555','quote':'Actual Clinic'},'services':[{'value':'Invented treatment','quote':'Invented treatment'}]})}
    monkeypatch.setattr(providers,'local_json',response)
    result=await providers.OllamaExtractionProvider().scrape('Actual Clinic. Contact us.')
    assert result.businessName=='Actual Clinic'
    assert result.phone=='' and not result.services

async def test_dataset_provider_real_file(tmp_path,monkeypatch):
    source=tmp_path/'places.geojson';source.write_text(json.dumps({'features':[{'properties':{'names':{'primary':'Regional Clinic'},'address':'Example street'},'geometry':{'coordinates':[-122,37]}}]}))
    monkeypatch.setenv('OVERTURE_DATASET',str(source))
    provider=providers.OvertureDiscoveryProvider()
    assert (await provider.health_check())['status']=='ready'
    rows=await provider.scrape('Regional Clinic');assert rows[0]['businessName']=='Regional Clinic'

async def test_api_jobs_sse_cancel_and_validation(monkeypatch):
    async def fake(req,progress,cfg):
        await progress({'profile':{'businessName':'unit fixture'},'metrics':{'totalMs':1}})
        await asyncio.sleep(.2)
        return {'profile':{'businessName':'unit fixture'}}
    monkeypatch.setattr(scraping_api,'execute',fake)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.put('/api/scraping/settings',json={'maxPages':500})).status_code==422
        cfg=ScrapingSettings(maxPages=6).model_dump()
        assert (await client.put('/api/scraping/settings',json=cfg)).status_code==200
        assert (await client.get('/api/scraping/settings')).json()['maxPages']==6
        response=await client.post('/api/scraping/test',json={'input':'https://example.com','mode':'fast'})
        job=response.json()['id'];await asyncio.sleep(.01)
        duplicate=await client.post('/api/scraping/test',json={'input':'https://example.com','mode':'fast'})
        assert duplicate.json()['id']==job and duplicate.json()['deduplicated']
        stopped=await client.delete('/api/scraping/jobs/'+job)
        assert stopped.json()['status']=='cancelled'
        events=await client.get('/api/scraping/jobs/'+job+'/events')
        assert 'event: progress' in events.text and 'event: cancelled' in events.text
        assert (await client.get('/api/scraping/jobs/missing')).status_code==404
    await scraping_api.shutdown_jobs()

def test_percentiles_include_interpolated_small_sample():
    assert engine.percentile([100,300],.5)==200
    assert engine.percentile([100,300],.9)==280
    assert engine.percentile([],.95) is None

@pytest.mark.skipif(__import__('os').getenv('RUN_V2_INTEGRATION')!='1',reason='Explicit opt-in real local Redis / PostgreSQL services required')
async def test_real_storage_services():
    live=Storage()
    assert live.redis_url and live.database_url
    await live.init();await live.cache_put('integration-health',{'verified':True},10)
    assert (await live.cache_get('integration-health'))==({'verified':True},'redis')

def test_additional_schema_fields_prices_and_redaction():
    from app.diagnostics import record_page,enrich_response,redact
    trace={'raw':[]}
    html='<script type="application/ld+json">'+json.dumps({'@type':'Dentist','name':'Clinic','geo':{'latitude':37,'longitude':-122},'aggregateRating':{'ratingValue':4.5,'reviewCount':12},'makesOffer':{'@type':'Service','name':'Consultation','offers':{'price':'100','priceCurrency':'USD'}}})+'</script>'
    record_page(trace,html,'https://example.com/?token=secret',200,'scrapling')
    result={'profile':Profile(businessName='Clinic',services=['Consultation']).model_dump(),'provenance':{}}
    enrich_response(result,trace)
    assert result['businessProfile']['services'][0]['price']=='100 USD'
    assert result['businessProfile']['additionalData']['coordinates']['latitude']==37
    assert 'secret' not in json.dumps(redact(trace['raw']))

async def test_first_early_provider_snapshot_survives_later_import_clock(monkeypatch):
    async def importer(url,render,follow,on_progress):
        result=result_payload(Extraction(profile=Profile(businessName='Clinic'),provenance={'businessName':[{'url':url,'method':'jsonld'}]}),time.monotonic(),1)
        await on_progress({**result,'firstFoundMs':20,'lastFoundMs':20,'discoverySpanMs':0})
        return {**result,'firstFoundMs':100,'lastFoundMs':150,'discoverySpanMs':50}
    monkeypatch.setattr(engine,'import_profile',importer)
    result=await engine.execute(engine.ScrapeRequest(input='https://example.com',mode='fast'),None,ScrapingSettings())
    assert result['firstFoundMs']==20 and result['lastFoundMs']==150 and result['discoverySpanMs']==130

def test_unique_maps_match_fills_form_gaps_without_domain_match():
    from app.profile_completion import complete_profile
    candidate=Profile(businessName='Ishinryu Karate SF',address='736 14th St, San Francisco, CA 94114',phone='+14155624752',website='http://www.ishinryukaratesf.com/',timezone='America/Los_Angeles').model_dump()
    payload={'profile':Profile(businessName='Ishinryu Karate SF',website='https://karatesf.com').model_dump(),'mapsCandidates':[{**candidate,'sourceUrl':'https://maps.google.com/?cid=17623478142683588316'}]}
    complete_profile(payload)
    assert payload['profile']['address']==candidate['address']
    assert payload['profile']['phone']==candidate['phone']
    assert payload['profile']['website']=='https://karatesf.com'
    assert payload['provenance']['address'][0]['method']=='sonagara'
    assert payload['locationCount']==1

def test_maps_candidates_do_not_mix_ambiguous_branches_or_replace_values():
    from app.profile_completion import complete_profile
    rows=[{**Profile(businessName='Clinic',address=address,phone=phone).model_dump(),'sourceUrl':'https://maps.google.com/?cid=1'} for address,phone in [('1 First St','+14155550100'),('2 Second St','+14155550200')]]
    payload={'profile':Profile(businessName='Clinic',description='Official description').model_dump(),'mapsCandidates':rows}
    complete_profile(payload)
    assert not payload['profile']['address']
    payload['mapsCandidates']=rows[:1];payload['profile']['phone']='Manually published phone'
    complete_profile(payload)
    assert payload['profile']['phone']=='Manually published phone'
    assert payload['profile']['description']=='Official description'

def test_location_count_uses_known_locations_not_unrelated_search_results():
    from app.profile_completion import complete_profile
    payload={'profile':Profile().model_dump(),'mapsCandidates':[], 'locations':[{'address':'A'},{'address':'A'},{'address':'B'}]}
    assert complete_profile(payload)['locationCount']==2

def test_llms_payment_and_explicit_service_headings_populate_fields():
    from app.llms import extract_llms
    parsed=extract_llms('# Bevri\n> Georgian restaurant.\n\n## Practical extras\n- Payment: all major payment methods are accepted.\n\n## Takeout and delivery\n- Order on our website.','https://www.bevri.com/llms.txt')
    assert parsed.profile.paymentMethods==['all major payment methods are accepted.']
    assert parsed.profile.services==['Takeout','Delivery']

def test_published_website_description_replaces_word_metadata_noise():
    from app.extraction import extract_html
    html='<title>Ishinryu Karate SF</title><meta name="description" content="&nbsp; Normal 0 false false EN-AU JA X-NONE"><script type="application/ld+json">'+json.dumps({'@type':'WebSite','@context':'https://schema.org','name':'Ishinryu Karate SF','url':'https://karatesf.com','description':'<p>Welcome to the online home of Ishinryu Karate SF.</p>'})+'</script>'
    result=extract_html(html,'https://karatesf.com')
    assert result.profile.description=='Welcome to the online home of Ishinryu Karate SF.'
