from app.field_timeline import FieldTimeline
from app.contact_fields import complete_contacts

def payload(name='Bevri',email='',method='sonagara',url='https://maps.google.com/?cid=123'):
    data={'profile':{'businessName':name,'phone':'+16506000433','website':'https://bevri.com','email':email},'provenance':{'businessName':[{'url':url,'method':method}],'phone':[{'url':url,'method':method}]}}
    if email:data['provenance']['email']=[{'url':'https://bevri.com/contact','method':'html'}]
    return complete_contacts(data)

def test_first_maps_then_web_email_and_no_fabricated_contact_name_time():
    timeline=FieldTimeline();data=payload();timeline.observe(data,700)
    first=list(data['fieldTimeline'])
    assert next(r for r in first if r['field']=='business_name')['source']=='maps'
    assert next(r for r in first if r['field']=='source')['elapsedMs']==700
    assert next(r for r in first if r['field']=='voice_greeting_phrase')['source']=='generated'
    data=payload(email='eat@bevri.com');timeline.observe(data,2000)
    row=next(r for r in data['fieldTimeline'] if r['field']=='email')
    assert row['source']=='web' and row['elapsedMs']==2000
    assert row['sourceUrl']=='https://bevri.com/contact'
    assert not any(r['field']=='full_name' for r in data['fieldTimeline'])
    timeline.observe(data,3000)
    assert data['fieldTimeline']==first+[row]

def test_updates_preserve_first_discovery_and_source_changes():
    timeline=FieldTimeline();data=payload();timeline.observe(data,700)
    updated=payload(name='Bevri Restaurant',method='jsonld',url='https://bevri.com')
    timeline.observe(updated,2100)
    rows=[r for r in updated['fieldTimeline'] if r['field']=='business_name']
    assert [(r['event'],r['source'],r['elapsedMs']) for r in rows]==[('found','maps',700),('updated','web',2100)]
    updated['profile']['description']='Evidence supported prose'
    updated['provenance']['description']=[{'url':'https://bevri.com','method':'ollama'}]
    timeline.observe(updated,4000)
    assert updated['fieldTimeline'][-1]['source']=='llm'
    assert sorted(r['elapsedMs'] for r in updated['fieldTimeline'])==[r['elapsedMs'] for r in updated['fieldTimeline']]

def test_cache_is_current_availability_not_previous_crawl_times():
    timeline=FieldTimeline();data=payload();timeline.observe(data,5,cache=True)
    assert all(row['source']=='cache' and row['elapsedMs']==5 for row in data['fieldTimeline'])
