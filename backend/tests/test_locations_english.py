import json
import pytest
from app.english import english_hours, english_content
from app.locations import store_source, store_locations, html_locations
from app.models import Extraction, Profile, Location
from app.service import DiscoveryProgress

def test_kyrgyz_days_are_english_and_week_ordered():
    result=english_hours(['бейшемби: 11:30–21:00','дүйшөмбү: 11:30–21:00','жекшемби: жабык','жума: 11:30–21:00','ишемби: 11:30–21:00','шаршемби: 11:30–21:00','шейшемби: 11:30–21:00'])
    assert [x.split(':')[0] for x in result] == ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
    assert result[-1]=='Sunday: Closed'

def test_store_locations_keep_branch_data_separate():
    rows=[dict(title='Clinic A',street='1 Street',city='Town',country_code='US',phone='415-555-0126',open_hours=json.dumps({'sun':'0','mon':['09:00-17:00']})),dict(title='Clinic B',street='2 Street',city='Town')]
    found=store_locations(json.dumps(rows),'https://example.com/locations')
    assert len(found)==2 and found[0].phone=='+14155550126'
    assert found[0].openingHours==['Monday: 09:00-17:00','Sunday: Closed']
    assert found[1].phone==''

def test_locator_source_must_belong_to_site():
    assert store_source('agile-store-locator; var ASL_REMOTE = {"ajax_url":"https://evil.example/ajax"};','https://example.com/') is None

def test_jsonld_branches_are_deduplicated():
    nodes=[{'@type':'Dentist','name':'A','address':'1 Street'},{'@type':'Dentist','name':'B','address':'2 Street'},{'@type':'Dentist','name':'A','address':'1 Street'}]
    assert len(html_locations('<script type="application/ld+json">'+json.dumps(nodes)+'</script>','https://example.com/'))==2

async def test_location_only_update_is_streamed():
    events=[]
    async def emit(event): events.append(event)
    progress=DiscoveryProgress(0,emit)
    await progress.publish(Extraction(profile=Profile()),1)
    await progress.publish(Extraction(locations=[Location(businessName='Branch',address='1 Street')]),2)
    assert len(events)==1 and events[0]['locations'][0]['address']=='1 Street'

async def test_untranslated_prose_does_not_pretend_to_be_english(monkeypatch):
    monkeypatch.delenv('OLLAMA_MODEL',raising=False)
    extraction=Extraction(profile=Profile(businessName='Клиника',description='Описание клиники'))
    await english_content(extraction)
    assert extraction.profile.description==''
    assert extraction.profile.businessName=='Клиника'
    assert 'OLLAMA_MODEL' in extraction.warnings[0]

async def test_robots_longer_allow_rule_wins(monkeypatch):
    from app import fetching
    from unittest.mock import AsyncMock
    monkeypatch.setattr(fetching,'fetch_http',AsyncMock(return_value=fetching.Page('User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php','https://example.com/robots.txt',200)))
    assert (await fetching.robots_allowed('https://example.com/wp-admin/admin-ajax.php?action=public'))[0]
    assert not (await fetching.robots_allowed('https://example.com/wp-admin/private'))[0]
