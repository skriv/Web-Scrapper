from app.extraction import extract_html,merge_extractions
from app.models import Extraction,Profile
from app.service import candidate_links,result_payload
import time
URL='https://vendor.example/'

def test_nav_footer_industries_are_deduplicated_and_not_presented_as_provider_industry():
 html='''<title>Vendor</title><nav><div><button>Industries</button><div><a href="/industries/dental">Dental &amp; Orthodontics</a><a href="/industries/restaurants">Restaurants</a></div></div></nav><footer><section><h3>Industries</h3><a href="/industries/dental">Dental &amp; Orthodontics</a></section></footer>'''
 found=extract_html(html,URL)
 assert found.profile.services==['Industry solutions for Dental & Orthodontics','Industry solutions for Restaurants']
 assert len(found.navigationOfferings)==2
 assert found.navigationOfferings[0]['position']=='navigation, footer'
 assert found.provenance['services'][0]['method']=='navigation-industry'
 payload=result_payload(found,time.monotonic(),1)
 assert payload['industries']==['Dental & Orthodontics','Restaurants']
 assert 'Restaurants' not in found.profile.services
 assert 'https://vendor.example/industries/restaurants' in found.links

def test_generic_industry_group_without_special_url_and_service_section():
 found=extract_html('''<footer><div><h3>Who we serve</h3><a href="/dentists">Dentists</a></div><div><h3>Services</h3><a href="/repairs">Repairs</a><a href="/pricing">Pricing</a></div><div><h3>Company</h3><a href="/about">About</a><a href="/partners">Partners</a></div></footer>''',URL)
 assert found.profile.services==['Industry solutions for Dentists','Repairs']

def test_unrelated_resources_external_links_and_body_mentions_are_not_services():
 found=extract_html('''<nav><a href="/platform">Platform</a><div><button>Resources</button><a href="/case-studies">Case Studies</a><a href="/webinars">Webinars</a></div><a href="/pricing">Pricing</a></nav><article><h2>Industries</h2><a href="/industries/healthcare">Healthcare article</a></article><footer><h3>Industries</h3><a href="https://other.example/industries/dental">Other company</a></footer>''',URL)
 assert found.profile.services==[] and not found.navigationOfferings

def test_industry_links_are_prioritized_within_existing_ten_page_budget():
 links=[URL+'blog/post',URL+'about',URL+'industries/dental',URL+'contact']
 assert candidate_links(links,URL)==[URL+'contact',URL+'industries/dental',URL+'about',URL+'blog/post']
 assert len(candidate_links([URL+f'industries/{i}' for i in range(30)],URL))==9

def test_merging_keeps_schema_services_and_rejects_unrelated_company_navigation():
 base=Extraction(profile=Profile(businessName='Vendor',services=['Published service']),provenance={'businessName':[{'url':URL,'method':'jsonld'}]})
 extra=extract_html('<nav><div><button>Industries</button><a href="/industries/dental">Dental</a></div></nav>',URL)
 merged=merge_extractions(base,extra)
 assert merged.profile.services==['Published service','Industry solutions for Dental'] and merged.navigationOfferings
 extra.profile.businessName='Other';extra.provenance['businessName']=[{'url':URL,'method':'jsonld'}]
 merged=merge_extractions(base,extra)
 assert merged.profile.services==['Published service'] and not merged.navigationOfferings

def test_named_product_dropdown_collects_parent_and_all_children_including_sibling_path():
    html='''<nav><a href="/platform">Platform</a><div><a aria-haspopup="true" href="/voice-ai-agent">Voice AI Employee</a><div><a href="/voice-ai-agent/inbound">Inbound Voice Calls</a><a href="/voice-ai-agent/outbound">Outbound Voice &amp; SMS</a><a href="/ai-chatbot">AI Chat Widget</a></div></div><div><a href="/resources">Resources</a><div><a href="/resources/guides">Guides</a><a href="/resources/webinars">Webinars</a></div></div></nav>'''
    found=extract_html(html,URL)
    assert found.profile.services==['Voice AI Employee','Inbound Voice Calls','Outbound Voice & SMS','AI Chat Widget']
    assert all(row['kind']=='service' and row['section']=='Voice AI Employee' for row in found.navigationOfferings)
    assert all(p['method']=='navigation-service' for p in found.provenance['services'])

def test_named_offering_detection_is_generic_and_requires_actual_child_structure():
    html='''<nav><div><a href="/accounting">Business Accounting</a><a href="/accounting/payroll">Payroll</a><a href="/accounting/taxes">Tax Preparation</a></div><div><a href="/company">Company</a><a href="/company/about">About</a><a href="/company/careers">Careers</a></div><div><a href="/random">Random</a><a href="https://other.example/random/a">External A</a><a href="https://other.example/random/b">External B</a></div></nav>'''
    found=extract_html(html,URL)
    # Three flat sibling links are not a product dropdown root; they need a local group.
    assert found.profile.services==[]
    found=extract_html('''<nav><div><a href="/accounting">Business Accounting</a><div><a href="/accounting/payroll">Payroll</a><a href="/accounting/taxes">Tax Preparation</a></div></div></nav>''',URL)
    assert found.profile.services==['Business Accounting','Payroll','Tax Preparation']
