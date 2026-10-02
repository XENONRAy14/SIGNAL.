import hashlib, html, json, re
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from datetime import datetime,timezone
from bs4 import BeautifulSoup
from ..matching import norm

TECH=['Python','SQL','React','TypeScript','JavaScript','Java','C++','C#','Go','Rust','Docker','Kubernetes','AWS','Azure','GCP','Terraform','Linux','Git','PostgreSQL','Spark','Power BI','TensorFlow','PyTorch','n8n','Excel','Figma','Salesforce','SEO','SAP']
DOMAINS={'Cybersecurity':['cyber','pentest','soc analyst','sécurité informatique'], 'Data':['data','données','analytics','business intelligence'], 'IA':['machine learning','artificial intelligence','intelligence artificielle','deep learning'], 'DevOps':['devops','cloud','sre','platform engineer'], 'Automation':['automation','automatisation','rpa'], 'Software':['software','développeur','developer','frontend','backend','fullstack','full stack'], 'Finance':['finance','comptable','audit'], 'Marketing':['marketing','seo','growth'], 'Commerce':['commercial','sales','vente'], 'RH':['ressources humaines','recruteur','recruiter'], 'Droit':['juriste','legal','avocat'], 'Santé':['infirmier','médecin','santé'], 'Industrie':['industrie','production','maintenance'], 'Ingénierie':['ingénieur','engineer'], 'Communication':['communication','rédacteur'], 'Design':['design','ux','ui']}

def plain(s): return BeautifulSoup(html.unescape(str(s or '')),'html.parser').get_text(' ',strip=True)
def hashstr(s): return hashlib.sha256(s.encode()).hexdigest()
def canonical_url(url):
    p=urlsplit(url)
    q=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('ref','source','gh_src')]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/') or '/',urlencode(sorted(q)),''))
def date(value):
    if not value: return None
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    except (ValueError,TypeError): return None

def normalize(raw,provider):
    title=plain(raw.get('title'))[:500]; desc=plain(raw.get('description'))[:150000]
    if not title or not raw.get('source_url'): raise ValueError('Annonce sans titre ou URL')
    p=urlsplit(raw['source_url'])
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password: raise ValueError('URL annonce invalide')
    text=norm(title+' '+desc); title_norm=norm(title)
    tech=[x for x in TECH if re.search(r'(?<!\w)'+re.escape(norm(x))+r'(?!\w)',text)]
    contract=raw.get('contract_type','unknown')
    if contract=='unknown':
        for val,words in [('alternance',['alternance','apprentice','apprentissage']),('stage',['internship','intern ','stage ']),('CDI',['cdi','permanent']),('CDD',['cdd','fixed term']),('graduate',['graduate']),('freelance',['freelance','contractor'])]:
            if any(re.search(r'(?<!\w)'+re.escape(w.strip())+r'(?!\w)',norm(title+' '+str(raw.get('employment_type','')))) for w in words): contract=val; break
    domain=raw.get('domain') or next((k for k,words in DOMAINS.items() if any(norm(w) in title_norm for w in words)),'Autre')
    remote=raw.get('remote_type','unknown')
    if remote=='unknown':
        if any(w in title_norm for w in ['hybrid','hybride']): remote='hybrid'
        elif any(w in title_norm for w in ['remote','télétravail']): remote='remote'
    location=plain(raw.get('location'))[:500] or None
    city=raw.get('city')  # Never invent a city from an unstructured multi-location string.
    senior=raw.get('seniority')
    if not senior:
        if any(w in title_norm for w in ('senior','lead','principal','staff')): senior='senior'
        elif contract in ('stage','alternance','graduate') or 'junior' in title_norm: senior='junior'
    identity=norm(title)+'|'+norm(location or city or '')+'|'+contract
    d=dict(source=provider,source_url=canonical_url(raw['source_url']),external_id=str(raw['external_id']) if raw.get('external_id') is not None else None,
        title=title,normalized_title=title_norm,description=desc,short_description=desc[:280],contract_type=contract,
        employment_type=raw.get('employment_type'),apprenticeship=contract=='alternance',internship=contract=='stage',
        seniority=senior,education_level=raw.get('education_level'),location=location,city=city,region=raw.get('region'),country=raw.get('country'),
        latitude=raw.get('latitude'),longitude=raw.get('longitude'),remote_type=remote,salary_min=raw.get('salary_min'),salary_max=raw.get('salary_max'),salary_currency=raw.get('salary_currency'),
        skills=raw.get('skills') or tech,technologies=tech,languages=raw.get('languages',[]),domain=domain,subdomain=raw.get('subdomain'),
        responsibilities=raw.get('responsibilities',[]),requirements=raw.get('requirements',[]),nice_to_have=raw.get('nice_to_have',[]),
        publication_date=date(raw.get('publication_date')),expiration_date=date(raw.get('expiration_date')),dedupe_key=hashstr(identity))
    d['content_hash']=hashstr(json.dumps(d,sort_keys=True,default=str,ensure_ascii=False))
    return d
