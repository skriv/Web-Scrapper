"""Published contact facts and deterministic English greeting; no personal-name inference."""
import re
from urllib.parse import unquote

EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,63}", re.I)

def email_address(value):
    if not isinstance(value,str):return ''
    value=unquote(value.removeprefix('mailto:').split('?',1)[0]).strip()
    return value if len(value)<=254 and EMAIL.fullmatch(value) else ''

def extract_contacts(node,soup,resolve):
    contacts=[resolve(x) for x in (node.get('contactPoint') if isinstance(node.get('contactPoint'),list) else [node.get('contactPoint')]) if isinstance(x,dict)]
    raw=node.get('email')
    values=(raw if isinstance(raw,list) else [raw])+[c.get('email') for c in contacts]
    email=next((email_address(x) for x in values if email_address(x)), '')
    method='jsonld'
    if not email:
        for tag in soup.select('meta[content]'):
            if (tag.get('name') or tag.get('property') or '').lower() in {'email','business:contact_data:email'}:
                email=email_address(tag.get('content'));method='meta'
                if email:break
    if not email:
        for tag in soup.select('a[href^="mailto:"]'):
            email=email_address(tag.get('href'));method='html'
            if email:break
    if not email:
        # Visible published text only, never scripts, tracking payloads or guessed addresses.
        visible=soup.__class__(str(soup),'html.parser')
        for tag in visible(['script','style','noscript']):tag.decompose()
        match=EMAIL.search(visible.get_text(' ',strip=True))
        if match:email=email_address(match.group());method='html'
    # Only a single explicitly attached Person is a contact. Do not pick an arbitrary team member.
    people=[]
    for value in contacts:
        types=value.get('@type',[]);types=[types] if isinstance(types,str) else types
        if 'Person' in types and isinstance(value.get('name'),str):people.append(value['name'].strip())
    people=list(dict.fromkeys(x for x in people if x))
    return email,method,people[0] if len(people)==1 else ''

def complete_contacts(payload):
    profile=payload['profile'];evidence=payload.setdefault('provenance',{})
    for key,original in [('business_name','businessName'),('phone_number','phone')]:
        profile[key]=profile.get(original,'')
        if evidence.get(original):evidence[key]=[dict(x) for x in evidence[original]]
    name=profile.get('businessName','').strip()
    profile['voice_greeting_phrase']=f'Hello, this is {name}. How can I help you today?' if name else ''
    if name:evidence['voice_greeting_phrase']=[{'url':'','method':'generated-template'}]
    sources=evidence.get('businessName',[]) or evidence.get('phone',[])
    profile['source']=next((x.get('url','') for x in sources if x.get('url')),profile.get('website',''))
    if profile['source']:evidence['source']=[{'url':profile['source'],'method':'source-url'}]
    for key in ('full_name','email'):profile.setdefault(key,'')
    payload['missingFields']=[key for key,value in profile.items() if not value]
    for group in ('locations','mapsCandidates'):
        for row in payload.get(group,[]):
            if isinstance(row,dict):
                row_sources={'businessName':[{'url':row.get('sourceUrl',''),'method':'published-source'}]}
                complete_contacts({'profile':row,'provenance':row_sources})
    return payload
