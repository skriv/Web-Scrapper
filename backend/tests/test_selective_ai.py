import asyncio,json,time
from unittest.mock import AsyncMock
import pytest
from app.models import Profile
from app.configuration import ScrapingSettings,trace_context,settings_context
from app.timed_ai import TimedAI
from app.providers import OllamaExtractionProvider
from app.ai_planner import focused_packet

def complete():
    return Profile(businessName='Bevri',description='Georgian restaurant',address='1 Street',phone='+16506000433',website='https://bevri.com',timezone='America/Los_Angeles',openingHours=['Monday 11:30-21:00'],services=['Dining'],paymentMethods=['Visa'],email='eat@bevri.com').model_dump()
def data():return {'started':time.monotonic(),'logs':[],'raw':[],'texts':[{'url':'https://bevri.com','text':'Georgian restaurant in downtown Palo Alto.'}]}
def snapshot(profile):return {'profile':profile,'provenance':{},'warnings':[],'firstFoundMs':1,'lastFoundMs':1}

async def test_complete_parser_profile_skips_model_despite_unknown_personal_name():
    info=data();call=AsyncMock(return_value=Profile())
    ai=TimedAI(ScrapingSettings(),info,info['started'],call,AsyncMock());ai.observe(snapshot(complete()));ai.start();ai.scraping_complete();await ai.finish()
    call.assert_not_called();assert ai.state['status']=='skipped' and ai.state['reason']=='parser-complete'
    assert ai.state['requestedFields']==[] and ai.state['startedMs'] is None

async def test_only_missing_services_are_inferred_with_supporting_quote_and_label():
    info=data();p=complete();p['services']=[]
    async def model(provider,packet):
        assert info['aiRequestedFields']==('services',) and len(packet)<=6000
        info['aiFieldEvidence']={'services':[{'value':'Georgian dining','quote':'Georgian restaurant in downtown Palo Alto.'}]}
        return Profile(services=['Georgian dining'],phone='Unrequested invention')
    ai=TimedAI(ScrapingSettings(),info,info['started'],model,AsyncMock());ai.observe(snapshot(p));ai.start();ai.scraping_complete();await ai.finish()
    out=snapshot(p);ai.merge(out)
    assert out['profile']['services']==['Georgian dining'] and out['profile']['phone']=='+16506000433'
    assert out['provenance']['services'][0]['method']=='ollama-inferred'
    assert ai.state['requestedFields']==['services']

async def test_parser_completion_cancels_pending_request_without_failing_job():
    info=data();p=complete();p['services']=[];entered=asyncio.Event();cancelled=asyncio.Event()
    async def model(*args):
        entered.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    ai=TimedAI(ScrapingSettings(),info,info['started'],model,AsyncMock());ai.observe(snapshot(p));ai.start();ai.scraping_complete();await entered.wait()
    ai.observe(snapshot(complete()));await asyncio.wait_for(ai.finish(),1)
    assert cancelled.is_set() and ai.state['status']=='cancelled'
    assert ai.state['reason']=='parser-filled-requested-fields' and not ai.profile.services

async def test_partial_parser_completion_keeps_other_gaps_inflight():
    info=data();p=complete();p['services']=[];p['phone']='';entered=asyncio.Event();release=asyncio.Event()
    async def model(*args):
        entered.set();await release.wait();return Profile()
    ai=TimedAI(ScrapingSettings(),info,info['started'],model,AsyncMock());ai.observe(snapshot(p));ai.start();ai.scraping_complete();await entered.wait()
    partially=complete();partially['phone']='';ai.observe(snapshot(partially))
    assert not ai.cancelled_for_completion
    release.set();await ai.finish();assert ai.state['status']=='complete'

async def test_model_schema_and_budget_only_cover_requested_missing_field(monkeypatch):
    from app import providers
    info=data();info.update(aiRequestedFields=('services',),aiInferServices=True)
    tokens=trace_context.set(info);cfg=settings_context.set(ScrapingSettings())
    client=AsyncMock(return_value={'response':json.dumps({'services':[{'value':'Georgian dining','quote':'Georgian restaurant'}]})})
    monkeypatch.setattr(providers,'local_json',client)
    try:
        result=await OllamaExtractionProvider().scrape('Georgian restaurant')
        payload=client.await_args.kwargs['payload']
        assert set(payload['format']['properties'])=={'services'}
        assert payload['options']['num_predict']==600 and payload['think'] is False
        assert result.services==['Georgian dining']
        assert not result.businessName and not result.phone
    finally:trace_context.reset(tokens);settings_context.reset(cfg)

def test_focused_input_is_bounded_and_prioritizes_contact_evidence():
    info=data();info['texts']=[{'url':'https://bevri.com','text':'Navigation '*1000+' Email: eat@bevri.com '+ 'footer '*1000}]
    packet,sources=focused_packet(info,1000,['email'])
    assert len(packet)<=1000 and 'eat@bevri.com' in packet and sources
