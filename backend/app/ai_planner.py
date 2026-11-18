"""Plan gap-only extraction and compact, relevant source excerpts."""
import json,re
from .models import CORE_FIELDS

FIELDS=CORE_FIELDS+('email',)
HINTS={'businessName':r'name|business|company','description':r'about|welcome|business|restaurant|clinic','address':r'address|street|location|avenue','phone':r'phone|telephone|call|tel:','email':r'email|mailto|@','timezone':r'timezone|time zone|America/|Europe/','openingHours':r'hours|monday|tuesday|opening|closed','services':r'service|offer|provide|restaurant|dining|clinic|repair|delivery|takeout|catering','paymentMethods':r'payment|credit|visa|mastercard|cash'}

def missing_fields(profile,data):
    fields=[key for key in FIELDS if not profile.get(key)]
    # Absence of a personal name alone never warrants inference. Only explicit contact-person hints qualify.
    contact_hint=any(re.search(r'contact person|contact name|contactPoint.{0,100}Person',row.get('text',''),re.I) for row in data.get('texts',[]))
    if not profile.get('full_name') and contact_hint:fields.append('full_name')
    return fields

def focused_packet(data,max_chars,fields):
    limit=min(max_chars,6000);chunks=[];seen=set()
    pattern=re.compile('|'.join(HINTS.get(field,r'contact person|contact name') for field in fields),re.I)
    for row in data.get('texts',[]):
        text=row.get('text','');url=row.get('url','')
        for match in list(pattern.finditer(text))[:30]:
            a=max(0,match.start()-140);z=min(len(text),match.end()+420)
            excerpt=text[a:z].strip()
            signature=(url,excerpt)
            if signature not in seen:
                seen.add(signature);chunks.append((len(pattern.findall(excerpt)),url,excerpt))
        if text.strip():chunks.append((0,url,text[:700]))
    for row in data.get('raw',[]):
        if not 200<=row.get('status',200)<300:continue
        structured=row.get('structuredData') or row.get('data')
        if structured:
            text=json.dumps(structured,ensure_ascii=False)
            for match in list(pattern.finditer(text))[:10]:
                chunks.append((len(pattern.findall(text)),row.get('sourceUrl',''),text[max(0,match.start()-120):match.end()+420]))
    parts=[];included=[];used=set();remaining=limit
    for _,url,text in sorted(chunks,key=lambda x:(-x[0],x[1])):
        if text in used:continue
        header=f'SOURCE {len(parts)+1}: {url}\n'
        budget=min(900,remaining-len(header)-2)
        if budget<80:break
        excerpt=text[:budget];parts.append(header+excerpt+'\n\n');included.append({'url':url,'text':excerpt});used.add(text)
        remaining-=len(parts[-1])
        if len(parts)>=6:break
    return ''.join(parts),included
