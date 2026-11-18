import asyncio
from unittest.mock import AsyncMock
import pytest
from app import service,maps
from app.fetching import Page
from app.models import Extraction,Profile,Location

@pytest.mark.parametrize("nested",[False,True])
async def test_ten_pages_are_crawled_with_nested_discovery(monkeypatch,nested):
    monkeypatch.setattr(service,'assert_public_url',AsyncMock(side_effect=lambda x:x))
    monkeypatch.setattr(service,'robots_allowed',AsyncMock(return_value=(True,None)))
    fetched=[]
    async def fetch(url,**kwargs):
        if url.endswith('.txt'):return Page('',url,404)
        fetched.append(url)
        number=int(url.rsplit('-',1)[1]) if '/page-' in url else 0
        links=''.join(f'<a href="/page-{n}">Page</a>' for n in ([number+1] if nested else range(1,20)))
        return Page('<title>Clinic</title>'+links,url,200)
    monkeypatch.setattr(service,'fetch_http',fetch)
    await service.import_website('https://example.com/',False,True)
    assert len(fetched)==10 and len(set(fetched))==10

async def test_maps_starts_after_identity_while_website_keeps_running(monkeypatch):
    gate=asyncio.Event(); searching=asyncio.Event(); events=[]
    monkeypatch.setattr(service,'assert_public_url',AsyncMock(side_effect=lambda x:x))
    async def website(url,render_js,follow_links,on_progress):
        data=Extraction(profile=Profile(businessName='Clinic',address='1 Street'))
        await on_progress(data,1)
        await searching.wait()
        assert not gate.is_set()
        gate.set()
        return data,10
    async def lookup(name,address,website):
        searching.set()
        await gate.wait()
        return [Location(businessName='Other clinic',address='9 Street')]
    monkeypatch.setattr(service,'import_website',website)
    monkeypatch.setattr(maps,'search_maps',lookup)
    async def progress(event):events.append(event)
    result=await service.import_profile('https://example.com/',on_progress=progress)
    assert events[0]['profile']['address']=='1 Street'
    assert result['profile']['address']=='1 Street'
    assert result['mapsCandidates'][0]['address']=='9 Street'
    assert result['mapsStatus']=='selection_required'

async def test_maps_failure_preserves_site(monkeypatch):
    monkeypatch.setattr(service,'assert_public_url',AsyncMock(side_effect=lambda x:x))
    monkeypatch.setattr(service,'import_website',AsyncMock(return_value=(Extraction(profile=Profile(businessName='Clinic')),1)))
    monkeypatch.setattr(maps,'search_maps',AsyncMock(side_effect=RuntimeError('Maps unavailable')))
    result=await service.import_profile('https://example.com/')
    assert result['profile']['businessName']=='Clinic'
    assert result['mapsStatus']=='error'

async def test_unique_same_phone_and_domain_match_enriches_profile(monkeypatch):
    monkeypatch.setattr(service,'assert_public_url',AsyncMock(side_effect=lambda x:x))
    data=Extraction(profile=Profile(businessName='Clinic',phone='+14155550126',website='https://example.com/',description='Website description'))
    monkeypatch.setattr(service,'import_website',AsyncMock(return_value=(data,10)))
    candidate=Location(businessName='Clinic',phone='+14155550126',website='https://example.com/',address='1 Street',openingHours=['Monday 09:00-17:00'],sourceUrl='https://www.google.com/maps/place/Clinic')
    monkeypatch.setattr(maps,'search_maps',AsyncMock(return_value=[candidate]))
    result=await service.import_profile('https://example.com/')
    assert result['mapsStatus']=='matched'
    assert result['profile']['openingHours']==['Monday 09:00-17:00']
    assert result['profile']['description']=='Website description'
