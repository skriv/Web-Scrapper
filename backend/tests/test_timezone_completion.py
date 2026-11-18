from unittest.mock import AsyncMock
import pytest
from app.models import Profile
from app.timezone_resolution import resolve_local,resolve_address,same_address

def payload(**kwargs):return {'profile':Profile(**kwargs).model_dump(),'provenance':{},'missingFields':[]}

def test_json_timezone_alias_promoted():
    result=payload();result['businessProfile']={'timeZone':'America/Los_Angeles'}
    assert resolve_local(result)
    assert result['profile']['timezone']=='America/Los_Angeles'

def test_location_address_matches_ste_suite_and_omitted_state():
    result=payload(address='1010, Shaw Ave Suite B, Clovis, 93612, US')
    result['mapsCandidates']=[{'address':'1010 Shaw Ave Ste B, Clovis, CA 93612','timezone':'America/Los_Angeles','sourceUrl':'https://maps.google.com/?cid=1'}]
    assert resolve_local(result)
    assert result['profile']['timezone']=='America/Los_Angeles'
    assert result['timezoneResolution']['method']=='location_timezone'

def test_all_sixteen_locations_can_supply_shared_zone():
    result=payload(address='Main office')
    result['locations']=[{'address':f'{i} Road','timezone':'America/Los_Angeles'} for i in range(16)]
    assert resolve_local(result)
    assert result['timezoneResolution']['method']=='location_consensus'

def test_different_branch_zones_do_not_use_random_global_coordinates():
    result=payload()
    result['locations']=[{'address':'San Francisco','timezone':'America/Los_Angeles'},{'address':'New York','timezone':'America/New_York'}]
    assert not resolve_local(result,{'coordinates':{'latitude':37.78,'longitude':-122.44}})
    assert not result['profile']['timezone']

def test_coordinates_and_invalid_explicit_value():
    result=payload(timezone='not-a-zone')
    assert resolve_local(result,{'coordinates':{'latitude':37.78,'longitude':-122.44}})
    assert result['profile']['timezone']=='America/Los_Angeles'

def test_existing_zone_is_kept():
    result=payload(timezone='Europe/London')
    assert not resolve_local(result,{'coordinates':{'latitude':37.78,'longitude':-122.44}})
    assert result['profile']['timezone']=='Europe/London'

def test_phone_is_marked_as_inference_and_ambiguous_number_is_skipped():
    result=payload(phone='+14155624752')
    assert resolve_local(result)
    assert result['profile']['timezone']=='America/Los_Angeles'
    assert result['timezoneResolution']['evidenceStatus']=='inferred'
    assert not resolve_local(payload(phone='+18005551234'))

def test_phone_does_not_override_contradictory_address():
    result=payload(phone='+14155624752',address='Berlin, Germany')
    assert not resolve_local(result)

def test_unrelated_addresses_do_not_match():
    assert not same_address('1010 Shaw Ave, Clovis, CA 93612','1020 Shaw Ave, Clovis, CA 93612')
    assert not same_address('1010 Shaw Ave, Clovis, CA 93612','1010 Other Rd, Clovis, CA 93612')

async def test_address_geocoding_is_opt_in_cached_and_bounded(monkeypatch):
    from app import providers,timezone_resolution
    timezone_resolution._geo_cache.clear();timezone_resolution._geo_last=0
    request=AsyncMock(return_value=[{'lat':'37.78','lon':'-122.44'}]);monkeypatch.setattr(providers,'local_json',request)
    monkeypatch.delenv('GEOCODER_BASE_URL',raising=False)
    assert not await resolve_address(payload(address='736 14th St, San Francisco, CA 94114'))
    request.assert_not_called()
    monkeypatch.setenv('GEOCODER_BASE_URL','http://local-geocoder:8080')
    first=payload(address='736 14th St, San Francisco, CA 94114')
    assert await resolve_address(first)
    assert first['profile']['timezone']=='America/Los_Angeles'
    assert await resolve_address(payload(address=first['profile']['address']))
    assert request.await_count==1
    assert request.await_args.kwargs['timeout']==4
