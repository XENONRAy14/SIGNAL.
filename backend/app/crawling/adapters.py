"""Registry of ATS adapters. Unsupported proprietary ATS use conservative JSON-LD discovery."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import json, re
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .http import FetchError, Blocked
from .normalize import plain

@dataclass
class Batch:
    jobs: list[dict]
    complete: bool = True
    note: str = ''

class ATSAdapter(ABC):
    @abstractmethod
    def fetch(self,company,http)->Batch: ...

class Greenhouse(ATSAdapter):
    def fetch(self,c,h):
        data=json.loads(h.get(f'https://boards-api.greenhouse.io/v1/boards/{c.ats_id}/jobs?content=true'))
        jobs=data['jobs']
        return Batch([dict(external_id=j['id'],title=j['title'],source_url=j['absolute_url'],description=j.get('content',''),location=j.get('location',{}).get('name')) for j in jobs])

class Lever(ATSAdapter):
    def fetch(self,c,h):
        host='api.eu.lever.co' if c.ats_provider=='lever_eu' else 'api.lever.co'; all_jobs=[]
        for offset in range(0,10000,100):
            data=json.loads(h.get(f'https://{host}/v0/postings/{c.ats_id}?mode=json&limit=100&skip={offset}'))
            if not isinstance(data,list): raise FetchError('Réponse Lever invalide')
            for j in data:
                cat=j.get('categories',{})
                desc=j.get('descriptionPlain') or j.get('description','')
                desc+=' '+ ' '.join(plain(x.get('text','')+' '+x.get('content','')) for x in j.get('lists',[]))
                desc+=' '+plain(j.get('additional',''))
                all_jobs.append(dict(external_id=j['id'],title=j['text'],source_url=j['hostedUrl'],description=desc,location=cat.get('location'),employment_type=cat.get('commitment'),remote_type={'remote':'remote','hybrid':'hybrid','on-site':'onsite'}.get(j.get('workplaceType'),'unknown')))
            if len(data)<100: return Batch(all_jobs)
        raise FetchError('Pagination Lever dépassée')

class Ashby(ATSAdapter):
    def fetch(self,c,h):
        data=json.loads(h.get(f'https://api.ashbyhq.com/posting-api/job-board/{c.ats_id}?includeCompensation=true'))
        return Batch([dict(external_id=j.get('id') or urlsplit(j['jobUrl']).path.rstrip('/').split('/')[-1],title=j['title'],source_url=j['jobUrl'],description=j.get('descriptionPlain') or j.get('descriptionHtml',''),location=j.get('location'),city=(j.get('address') or {}).get('postalAddress',{}).get('addressLocality'),country=(j.get('address') or {}).get('postalAddress',{}).get('addressCountry'),region=(j.get('address') or {}).get('postalAddress',{}).get('addressRegion'),employment_type=j.get('employmentType'),publication_date=j.get('publishedAt'),remote_type={'Remote':'remote','Hybrid':'hybrid','OnSite':'onsite'}.get(j.get('workplaceType'),'remote' if j.get('isRemote') else 'unknown')) for j in data['jobs'] if j.get('isListed',True)])

class Recruitee(ATSAdapter):
    def fetch(self,c,h):
        data=json.loads(h.get(f'https://{c.ats_id}.recruitee.com/api/offers/'))
        return Batch([dict(external_id=j['id'],title=j['title'],source_url=j['careers_url'],description=(j.get('description') or '')+' '+(j.get('requirements') or ''),location=j.get('location'),city=j.get('city'),country=j.get('country'),employment_type=j.get('employment_type_code')) for j in data['offers']])

class SmartRecruiters(ATSAdapter):
    def fetch(self,c,h):
        rows=[]
        for offset in range(0,10000,100):
            data=json.loads(h.get(f'https://api.smartrecruiters.com/v1/companies/{c.ats_id}/postings?limit=100&offset={offset}'))
            for j in data['content']:
                detail=json.loads(h.get(f'https://api.smartrecruiters.com/v1/companies/{c.ats_id}/postings/{j["id"]}'))
                loc=detail.get('location',{}); sections=detail.get('jobAd',{}).get('sections',{})
                rows.append(dict(external_id=j['id'],title=j['name'],source_url=detail.get('applyUrl') or f'https://jobs.smartrecruiters.com/{c.ats_id}/{j["id"]}',description=' '.join(plain(s.get('text','')) for s in sections.values()),location=loc.get('city'),city=loc.get('city'),country=loc.get('country'),remote_type='remote' if loc.get('remote') else 'unknown',employment_type=detail.get('typeOfEmployment',{}).get('label')))
            if offset+len(data['content'])>=data.get('totalFound',0): return Batch(rows)
        raise FetchError('Pagination SmartRecruiters dépassée')

CAREER_PATHS=['careers','career','jobs','job','recruitment','recrutement','carriere','carrieres','nous-rejoindre','join-us','work-with-us']
PATTERN=re.compile(r'career|jobs?|recruit|recrut|carri[eè]re|nous-rejoindre|join-us|work-with-us',re.I)

def detect_ats(url):
    p=urlsplit(url); host=p.hostname or ''; bits=p.path.strip('/').split('/')
    if host in ('boards.greenhouse.io','job-boards.greenhouse.io'): return 'greenhouse',bits[0]
    if host=='jobs.lever.co': return 'lever',bits[0]
    if host=='jobs.eu.lever.co': return 'lever_eu',bits[0]
    if host=='jobs.ashbyhq.com': return 'ashby',bits[0]
    if host=='jobs.smartrecruiters.com': return 'smartrecruiters',bits[0]
    if host.endswith('.recruitee.com'): return 'recruitee',host.split('.')[0]
    return None

def discover(c,h):
    origin=c.website_url.rstrip('/')
    soup=BeautifulSoup(h.get(origin),'html.parser')
    candidates=[]
    for a in soup.select('a[href]'):
        link=urljoin(origin,a['href'])
        if PATTERN.search(a.get_text(' ',strip=True)+' '+link): candidates.append(link)
    try:
        sitemap=BeautifulSoup(h.get(urljoin(origin,'/sitemap.xml')),'html.parser')
        candidates.extend(x.get_text() for x in sitemap.find_all('loc') if PATTERN.search(x.get_text()))
    except FetchError:
        # Missing optional sitemap does not prevent homepage link discovery.
        pass
    for url in candidates:
        detected=detect_ats(url)
        if detected:
            provider,slug=detected
            if re.fullmatch(r'[\w-]{1,150}',slug): return url,provider,slug
    same_host=[u for u in candidates if urlsplit(u).hostname==urlsplit(origin).hostname]
    for url in list(dict.fromkeys(same_host+[urljoin(origin,'/'+p) for p in CAREER_PATHS]))[:20]:
        try:
            page=h.get(url)
            s=BeautifulSoup(page,'html.parser')
            for a in s.select('a[href]'):
                target=urljoin(url,a['href']); detected=detect_ats(target)
                if detected and re.fullmatch(r'[\w-]{1,150}',detected[1]): return target,*detected
            if PATTERN.search(s.get_text(' ',strip=True)) or s.find('script',type='application/ld+json'):
                return url,'generic',None
        except FetchError: continue
    raise FetchError('Aucune page carrière détectée ; renseignez son URL')

def flatten(data):
    if isinstance(data,list):
        for v in data: yield from flatten(v)
    elif isinstance(data,dict):
        typ=data.get('@type',[])
        if typ=='JobPosting' or isinstance(typ,list) and 'JobPosting' in typ: yield data
        for k in ('@graph','itemListElement','item'):
            if k in data: yield from flatten(data[k])

def jsonld_jobs(page,url):
    soup=BeautifulSoup(page,'html.parser'); result=[]
    for script in soup.find_all('script',type='application/ld+json'):
        try: data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        for j in flatten(data):
            loc=j.get('jobLocation',{}); loc=loc[0] if isinstance(loc,list) and loc else loc
            address=loc.get('address',{}) if isinstance(loc,dict) else {}
            if not isinstance(address,dict): address={}
            identifier=j.get('identifier'); identifier=identifier.get('value') if isinstance(identifier,dict) else identifier
            sal=j.get('baseSalary',{}); sal=sal if isinstance(sal,dict) else {}; val=sal.get('value',{}); val=val if isinstance(val,dict) else {}
            # Only annual amounts map to the normalized annual salary fields.
            annual=val.get('unitText','').upper() in ('YEAR','ANNUAL')
            result.append(dict(title=j.get('title'),external_id=identifier,source_url=urljoin(url,j.get('url') or url),description=j.get('description',''),location=address.get('addressLocality'),city=address.get('addressLocality'),region=address.get('addressRegion'),country=address.get('addressCountry') if isinstance(address.get('addressCountry'),str) else None,employment_type=str(j.get('employmentType','')),publication_date=j.get('datePosted'),expiration_date=j.get('validThrough'),remote_type='remote' if j.get('jobLocationType')=='TELECOMMUTE' else 'unknown',salary_min=val.get('minValue') if annual else None,salary_max=val.get('maxValue') if annual else None,salary_currency=sal.get('currency')))
    return result

class Generic(ATSAdapter):
    def fetch(self,c,h):
        url=c.career_url or c.website_url; page=h.get(url); jobs=jsonld_jobs(page,url)
        soup=BeautifulSoup(page,'html.parser')
        candidates=list(dict.fromkeys(urljoin(url,a['href']) for a in soup.select('a[href]') if PATTERN.search(a['href'])))
        for link in candidates[:30]:
            if link==url or urlsplit(link).hostname!=urlsplit(url).hostname: continue
            try: jobs.extend(jsonld_jobs(h.get(link),link))
            except FetchError: continue
        if not jobs: raise FetchError('Aucun JobPosting JSON-LD exploitable. Un connecteur dédié peut être nécessaire.')
        return Batch(jobs,False,'Collecte HTML partielle : aucune fermeture automatique')

REGISTRY={'greenhouse':Greenhouse(),'lever':Lever(),'lever_eu':Lever(),'ashby':Ashby(),'recruitee':Recruitee(),'smartrecruiters':SmartRecruiters(),'generic':Generic()}
for name in ('workday','teamtailor','successfactors','taleo'): REGISTRY[name]=Generic()

def adapter_for(name):
    if name not in REGISTRY: raise FetchError('Connecteur inconnu')
    return REGISTRY[name]
