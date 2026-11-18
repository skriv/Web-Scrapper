"""Published navigation/footer offerings and industry targets, without mixing customer industries with the provider's business type."""
import re
from urllib.parse import urlsplit,urljoin,urldefrag

INDUSTRIES=re.compile(r'^(?:industr(?:y|ies)(?: we serve)?|sectors|verticals|markets|who we (?:serve|help))$',re.I)
SERVICES=re.compile(r'^(?:services|our services|solutions|our solutions|products?|platform|offerings|what we do)$',re.I)
INDUSTRY_PATH=re.compile(r'/(?:industries|industry|sectors|verticals|markets|who-we-serve)/[^/]+',re.I)
SERVICE_PATH=re.compile(r'/(?:services|solutions|products|offerings)/[^/]+',re.I)
IGNORE=re.compile(r'^(?:home|about(?: us)?|contact(?: us)?|pricing|plans|partners?|login|log in|sign in|sign up|register|blog|news|insights|resources|integrations|privacy(?: policy)?|terms(?: of service)?|documentation|help center|all (?:industries|services|solutions)|view all|learn more|read more|get started|book a demo|get a demo)$',re.I)
TAGS=['h2','h3','h4','h5','h6','p','span','strong','button','summary','a']

def _text(tag):return re.sub(r'\s+',' ',tag.get_text(' ',strip=True)).strip()
def _kind(label):return 'industry' if INDUSTRIES.fullmatch(label) else 'service' if SERVICES.fullmatch(label) else ''
NON_OFFERING_GROUP=re.compile(r'^(?:resources|company|partners?|customers?|learn|developers?|documentation|help|support|blog|news|insights|legal|trust|privacy|careers|community|use cases)$',re.I)

def _named_offering_group(parent,base):
    direct=parent.find_all('a',href=True,recursive=False)
    if len(direct)!=1:return ''
    head=direct[0];label=_text(head)
    if not label or len(label)>100 or _kind(label) or IGNORE.fullmatch(label) or NON_OFFERING_GROUP.fullmatch(label):return ''
    target=urlsplit(urljoin(base,head.get('href','')))
    host=(urlsplit(base).hostname or '').removeprefix('www.')
    path=target.path.rstrip('/')
    if target.scheme not in {'http','https'} or target.username or target.password or not path or (target.hostname or '').removeprefix('www.')!=host:return ''
    # A named dropdown with at least two nested product/service destinations is
    # published offering structure, even when its title is a product name.
    children=set()
    for link in parent.find_all('a',href=True,limit=41):
        if link is head:continue
        child=urlsplit(urljoin(base,link.get('href','')))
        if child.scheme in {'http','https'} and (child.hostname or '').removeprefix('www.')==host and child.path.startswith(path+'/'):
            children.add(child.path.rstrip('/'))
    return label if len(children)>=2 else ''

def _section(link,root,base):
    for parent in link.parents:
        if parent is root:
            headings=root.find_all(['h2','h3','h4','h5','h6','p','button','summary'],recursive=False)
            if len(headings)==1 and _kind(_text(headings[0])):return _kind(_text(headings[0])),_text(headings[0])
            break
        anchors=parent.find_all('a',href=True,limit=41)
        if len(anchors)>40:break
        named=_named_offering_group(parent,base)
        if named:return 'service',named
        # A local section heading/root-menu link is evidence. Global page headings are not.
        headers=parent.find_all(['h2','h3','h4','h5','h6','p','strong','button','summary'],recursive=False)
        labels=[_text(child) for child in headers if 0<len(_text(child))<=70]
        if labels:
            # An unrelated local group (Resources, Company, Partners, etc.) is a boundary.
            for label in labels:
                if _kind(label):return _kind(label),label
            return '',''
        direct_links=parent.find_all('a',href=True,recursive=False)
        if len(direct_links)==1 and len(anchors)>1:
            label=_text(direct_links[0])
            if len(label)<=70:return _kind(label),label
    return '',''

def navigation_offerings(soup,url):
    origin=urlsplit(url);host=(origin.hostname or '').removeprefix('www.')
    records={}
    for root in soup.select('nav, footer, header, [role="navigation"], [role="contentinfo"], [role="menu"], [role="menubar"]')[:50]:
        position='footer' if root.name=='footer' or root.get('role')=='contentinfo' else 'navigation'
        for link in root.select('a[href]')[:1000]:
            label=_text(link)
            if not label or len(label)>100 or _kind(label) or IGNORE.fullmatch(label):continue
            if link.find_parent(['script','noscript']):continue
            target=urldefrag(urljoin(url,link.get('href','')))[0];parsed=urlsplit(target)
            if parsed.scheme not in {'http','https'} or parsed.username or parsed.password or (parsed.hostname or '').removeprefix('www.')!=host:continue
            if parsed.query or re.search(r'\.(?:pdf|png|jpe?g|svg|zip)$',parsed.path,re.I):continue
            kind,section=_section(link,root,url)
            if INDUSTRY_PATH.search(parsed.path):kind='industry';section=section or 'Industries'
            elif not kind and SERVICE_PATH.search(parsed.path):kind='service';section='Services'
            if not kind:continue
            if not parsed.path.strip('/') or parsed.path.rstrip('/')==origin.path.rstrip('/'):continue
            key=(kind,parsed.path.rstrip('/').casefold())
            if key in records:
                positions=records[key]['position'].split(', ')
                if position not in positions:records[key]['position']+=', '+position
                continue
            value=f'Industry solutions for {label}' if kind=='industry' else label
            records[key]={'kind':kind,'label':label,'service':value,'url':target,'sourceUrl':url,'section':section,'position':position,'method':'navigation-industry' if kind=='industry' else 'navigation-service'}
            if len(records)>=60:return list(records.values())
    return list(records.values())
