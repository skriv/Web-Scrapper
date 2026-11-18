import json,time
from app.extraction import extract_html,merge_extractions
from app.contact_fields import complete_contacts
from app.service import result_payload
from app.models import CONTACT_FIELDS

URL='https://example.com/'
def extract(node,extra=''):
    return extract_html('<script type="application/ld+json">'+json.dumps({'@type':'LocalBusiness','name':'Clinic',**node})+'</script>'+extra,URL)

def test_contact_schema_stream_payload_and_no_removed_fields():
    found=extract({'telephone':'+14155550123','email':'office@example.com','contactPoint':{'@type':'Person','name':'Anna Smith'}})
    data=result_payload(found,time.monotonic(),1);profile=data['profile']
    assert all(key in profile for key in CONTACT_FIELDS)
    assert profile['business_name']=='Clinic'
    assert profile['phone_number']=='+14155550123'
    assert profile['source']==URL
    assert profile['email']=='office@example.com'
    assert profile['full_name']=='Anna Smith'
    assert profile['voice_greeting_phrase']=='Hello, this is Clinic. How can I help you today?'
    assert 'external_logo_id' not in profile and 'consent' not in profile
    assert data['provenance']['email'][0]['method']=='jsonld'
    assert data['provenance']['voice_greeting_phrase'][0]['method']=='generated-template'

def test_mailto_query_decoding_and_later_contact_page_merge():
    base=extract({})
    contact=extract({},'<a href="mailto:office%40example.com?subject=Hello">Email us</a>')
    merged=merge_extractions(base,contact)
    assert merged.profile.email=='office@example.com'
    assert merged.provenance['email'][0]['method']=='html'

def test_ambiguous_or_unrelated_people_are_not_promoted():
    assert not extract({'contactPoint':[{'@type':'Person','name':'Anna'},{'@type':'Person','name':'Ben'}]}).profile.full_name
    assert not extract({'contactPoint':{'@type':'ContactPoint','name':'Customer Support'}}).profile.full_name
    assert not extract({},'<script type="application/ld+json">{"@type":"Person","name":"Unrelated Person"}</script>').profile.full_name

def test_email_metadata_and_visible_text_not_scripts():
    assert extract({},'<meta name="business:contact_data:email" content="office@example.com">').profile.email=='office@example.com'
    assert extract({},'<p>Contact office@example.com</p>').profile.email=='office@example.com'
    assert not extract({},'<script>const secret="analytics@example.net"</script>').profile.email

def test_greeting_is_empty_without_business_name_and_updates_with_name():
    payload={'profile':{'businessName':'','phone':''}}
    assert complete_contacts(payload)['profile']['voice_greeting_phrase']==''
    payload['profile']['businessName']='New Name'
    assert complete_contacts(payload)['profile']['voice_greeting_phrase']=='Hello, this is New Name. How can I help you today?'


def test_candidate_and_location_exports_have_same_contact_aliases():
    row={'businessName':'Branch','phone':'+14155550123','sourceUrl':'https://maps.google.com/?cid=123'}
    payload={'profile':{},'locations':[dict(row)],'mapsCandidates':[dict(row)]}
    complete_contacts(payload)
    for key in ('locations','mapsCandidates'):
        assert payload[key][0]['business_name']=='Branch'
        assert payload[key][0]['phone_number']=='+14155550123'
        assert payload[key][0]['source']==row['sourceUrl']
