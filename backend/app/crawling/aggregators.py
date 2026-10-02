"""Job aggregator sources used to discover employers and offers automatically.

France Travail and Adzuna are contractual APIs used with the operator's own credentials under their licence.
Keyless public feeds (Arbeitnow) go through SafeHTTP, so robots.txt and pacing still apply.
"""
import json, logging, re, time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import httpx
from ..config import settings
from ..models import now
from .http import FetchError, RetryLater

log=logging.getLogger('signal.aggregators')
UA='SignalBot/0.1 (self-hosted job radar)'

@dataclass
class Offer:
    company: str | None
    raw: dict
    website: str | None = None
    industry: str | None = None

def iso(d): return d.strftime('%Y-%m-%dT%H:%M:%SZ')
def from_unix(v):
    try: return datetime.fromtimestamp(int(v),timezone.utc).replace(tzinfo=None).isoformat()
    except (TypeError,ValueError): return None

class ApiClient:
    """Minimal client for credentialed APIs: bounded timeouts, no redirects, 429 backoff, transient errors retried later."""
    def __init__(self,min_interval=0.12,transport=None):
        self.http=httpx.Client(timeout=20,follow_redirects=False,headers={'User-Agent':UA,'Accept':'application/json'},transport=transport)
        self.min_interval=min_interval; self.last=0.0

    def request(self,method,url,**kw):
        for attempt in range(4):
            wait=self.min_interval-(time.monotonic()-self.last)
            if wait>0: time.sleep(wait)
            self.last=time.monotonic()
            try: r=self.http.request(method,url,**kw)
            except httpx.HTTPError as exc: raise RetryLater('Échec réseau : '+type(exc).__name__) from exc
            if r.status_code==429 and attempt<3:
                time.sleep(min(float(r.headers.get('retry-after') or 2**attempt),30)); continue
            if r.status_code==429 or r.status_code>=500: raise RetryLater(f'HTTP {r.status_code} ; nouvel essai plus tard')
            return r
        raise RetryLater('Limite de débit atteinte')

class Source:
    name=''; label=''; interval=timedelta(hours=6); attribution=''
    def configured(self): return True
    def fetch(self,cursor,deadline,http):
        """Yield (offers, cursor) chunks; each chunk is ingested and committed before the next one."""
        raise NotImplementedError

# --- France Travail -------------------------------------------------------------------------
FT_TOKEN='https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire'
FT_SEARCH='https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search'
FT_DEPARTEMENTS=[f'{i:02d}' for i in range(1,96) if i!=20]+['2A','2B','971','972','973','974','976']
FT_WINDOW=1150  # API ceiling: range start <= 1000, end <= 1149
FT_CONTRACTS={'CDI':'CDI','DIN':'CDI','CDD':'CDD','DDI':'CDD','SAI':'CDD','MIS':'intérim','LIB':'freelance','REP':'freelance','FRA':'freelance','CCE':'freelance'}
SALARY=re.compile(r'annuel de ([\d.,]+)\s*euros?(?:\s*à\s*([\d.,]+)\s*euros?)?',re.I)

def ft_offer(o):
    lieu=o.get('lieuTravail') or {}; ent=o.get('entreprise') or {}; sal=(o.get('salaire') or {}).get('libelle') or ''
    contract='alternance' if o.get('alternance') or re.search(r'apprentissage|professionnalisation',o.get('natureContrat') or '',re.I) else FT_CONTRACTS.get(o.get('typeContrat'),'unknown')
    m=SALARY.search(sal); num=lambda s: float(s.replace(',','.')) if s else None
    extra=[f'{k} : {v}' for k,v in [('Contrat',o.get('typeContratLibelle')),('Durée',o.get('dureeTravailLibelle')),('Salaire',sal),('Expérience',o.get('experienceLibelle')),('Qualification',o.get('qualificationLibelle')),('Secteur',o.get('secteurActiviteLibelle')),('Métier',o.get('appellationlibelle') or o.get('romeLibelle'))] if v]
    extra+=['Compétences : '+', '.join(c['libelle'] for c in o.get('competences') or [] if c.get('libelle'))] if o.get('competences') else []
    extra+=['Formation : '+', '.join(f.get('domaineLibelle') or f.get('niveauLibelle') or '' for f in o.get('formations') or [])] if o.get('formations') else []
    city=re.sub(r'^\s*\w{2,3}\s*-\s*','',lieu.get('libelle') or '').strip() or None
    url=(o.get('origineOffre') or {}).get('urlOrigine') or f"https://candidat.francetravail.fr/offres/recherche/detail/{o['id']}"
    raw=dict(external_id=o['id'],title=o.get('intitule'),source_url=url,description=(o.get('description') or '')+'\n\n'+'\n'.join(extra),
        location=lieu.get('libelle'),city=city,country='France',latitude=lieu.get('latitude'),longitude=lieu.get('longitude'),
        contract_type=contract,employment_type=o.get('dureeTravailLibelleConverti'),publication_date=o.get('dateCreation'),
        salary_min=num(m.group(1)) if m else None,salary_max=num(m.group(2) or m.group(1)) if m else None,salary_currency='EUR' if m else None,
        education_level=(o.get('formations') or [{}])[0].get('niveauLibelle'))
    return Offer(ent.get('nom'),raw,ent.get('url'),o.get('secteurActiviteLibelle'))

class FranceTravail(Source):
    name='francetravail'; label='France Travail'; interval=timedelta(hours=1)
    attribution='Source : France Travail. Réutilisation soumise à la licence de l’API Offres d’emploi.'
    def __init__(self): self.token=None; self.expires=0.0
    def configured(self): return bool(settings.ft_client_id and settings.ft_client_secret)

    def auth(self,api):
        if self.token and time.time()<self.expires-60: return self.token
        r=api.request('POST',FT_TOKEN,data={'grant_type':'client_credentials','client_id':settings.ft_client_id,'client_secret':settings.ft_client_secret,'scope':'api_offresdemploiv2 o2dsoffre'})
        if r.status_code!=200: raise FetchError(f'Authentification France Travail refusée (HTTP {r.status_code}). Vérifiez FT_CLIENT_ID/FT_CLIENT_SECRET et l’abonnement à l’API Offres d’emploi v2.')
        data=r.json(); self.token=data['access_token']; self.expires=time.time()+int(data.get('expires_in',1400))
        return self.token

    def page(self,api,start,stop,dept,first):
        params={'minCreationDate':iso(start),'maxCreationDate':iso(stop),'range':f'{first}-{min(first+149,FT_WINDOW-1)}','sort':'1'}
        if dept: params['departement']=dept
        r=api.request('GET',FT_SEARCH,params=params,headers={'Authorization':'Bearer '+self.auth(api)})
        if r.status_code==401: self.token=None; r=api.request('GET',FT_SEARCH,params=params,headers={'Authorization':'Bearer '+self.auth(api)})
        if r.status_code==204: return [],0
        if r.status_code not in (200,206): raise FetchError(f'France Travail HTTP {r.status_code}')
        total=re.search(r'/(\d+)',r.headers.get('content-range',''))
        rows=r.json().get('resultats',[])
        return rows,int(total.group(1)) if total else len(rows)

    def window(self,api,start,stop,dept=None):
        rows,total=self.page(api,start,stop,dept,0)
        if total>FT_WINDOW:
            if dept is None: return [o for d in FT_DEPARTEMENTS for o in self.window(api,start,stop,d)]
            if stop-start>timedelta(minutes=10):
                mid=start+(stop-start)/2
                return self.window(api,start,mid,dept)+self.window(api,mid,stop,dept)
            log.warning('ft_window_truncated dept=%s start=%s total=%d',dept,start,total)
        for first in dict.fromkeys(min(f,1000) for f in range(150,min(total,FT_WINDOW),150)):
            more,_=self.page(api,start,stop,dept,first); rows+=more
            if not more: break
        return list({o.get('id'):o for o in rows}.values())

    def fetch(self,cursor,deadline,http):
        api=ApiClient(); end=now()-timedelta(minutes=30)
        start=cursor or end-timedelta(hours=settings.ft_backfill_hours)
        depts=[d.strip().upper() for d in settings.ft_departements.split(',') if d.strip()]
        while start<end and time.monotonic()<deadline:
            stop=min(start+timedelta(hours=1),end)
            rows=[o for d in depts for o in self.window(api,start,stop,d)] if depts else self.window(api,start,stop)
            yield [ft_offer(o) for o in rows if o.get('id') and o.get('intitule')],stop
            start=stop

# --- Adzuna ---------------------------------------------------------------------------------
class Adzuna(Source):
    name='adzuna'; label='Adzuna'; interval=timedelta(hours=6); attribution='Offres fournies par Adzuna.'
    def configured(self): return bool(settings.adzuna_app_id and settings.adzuna_app_key)
    def fetch(self,cursor,deadline,http):
        api=ApiClient(min_interval=2.5); started=now()
        days=max(1,min(30,-(-int(((started-cursor).total_seconds() if cursor else 3*86400))//86400)))
        for page in range(1,21):
            if time.monotonic()>deadline: return
            r=api.request('GET',f'https://api.adzuna.com/v1/api/jobs/fr/search/{page}',params={'app_id':settings.adzuna_app_id,'app_key':settings.adzuna_app_key,'results_per_page':50,'max_days_old':days,'sort_by':'date','content-type':'application/json'})
            if r.status_code in (401,403): raise FetchError(f'Clés Adzuna refusées (HTTP {r.status_code})')
            if r.status_code!=200: raise FetchError(f'Adzuna HTTP {r.status_code}')
            rows=r.json().get('results',[])
            offers=[]
            for j in rows:
                loc=j.get('location') or {}; area=loc.get('area') or []
                predicted=str(j.get('salary_is_predicted'))=='1'
                offers.append(Offer((j.get('company') or {}).get('display_name'),dict(external_id=j.get('id'),title=j.get('title'),source_url=j.get('redirect_url'),description=j.get('description',''),
                    location=loc.get('display_name'),city=area[-1] if len(area)>2 else None,region=area[1] if len(area)>1 else None,country='France',latitude=j.get('latitude'),longitude=j.get('longitude'),
                    contract_type={'permanent':'CDI','contract':'CDD'}.get(j.get('contract_type'),'unknown'),employment_type=j.get('contract_time'),publication_date=j.get('created'),
                    salary_min=None if predicted else j.get('salary_min'),salary_max=None if predicted else j.get('salary_max'),salary_currency=None if predicted or not j.get('salary_min') else 'EUR')))
            yield offers,started
            if len(rows)<50: return

# --- Keyless public feeds -------------------------------------------------------------------
FR_PLACES=re.compile(r'\b(france|paris|lyon|marseille|toulouse|lille|bordeaux|nantes|nice|strasbourg|montpellier|rennes|grenoble|rouen|toulon|dijon|angers|nancy|metz|reims|tours|caen|brest|limoges|amiens|clermont|orl[eé]ans|mulhouse|perpignan|besan[cç]on|sophia|aix|ile-de-france|[iî]le-de-france|la d[eé]fense|issy|boulogne|levallois|neuilly|nanterre|courbevoie|puteaux|saint-denis|massy|versailles|annecy|compi[eè]gne)\b',re.I)

class Arbeitnow(Source):
    name='arbeitnow'; label='Arbeitnow'; interval=timedelta(hours=6); attribution='Offres relayées par Arbeitnow.'
    def configured(self): return settings.enable_arbeitnow
    def fetch(self,cursor,deadline,http):
        started=now()
        for page in range(1,11):
            if time.monotonic()>deadline: return
            data=json.loads(http.get(f'https://www.arbeitnow.com/api/job-board-api?page={page}'))
            rows=data.get('data',[]); offers=[]; older=False
            for j in rows:
                created=from_unix(j.get('created_at'))
                if cursor and created and datetime.fromisoformat(created)<cursor-timedelta(hours=1): older=True; continue
                if not FR_PLACES.search(j.get('location') or ''): continue
                offers.append(Offer(j.get('company_name'),dict(external_id=j.get('slug'),title=j.get('title'),source_url=j.get('url'),description=j.get('description',''),location=j.get('location'),
                    country='France',remote_type='remote' if str(j.get('remote')).lower()=='true' else 'unknown',employment_type=', '.join(j.get('job_types') or [])[:60] or None,publication_date=created)))
            yield offers,started
            if older or not rows or not (data.get('links') or {}).get('next'): return

# Remotive was evaluated and left out: its robots.txt disallows /api/* for all agents.
SOURCES={s.name:s for s in (FranceTravail(),Adzuna(),Arbeitnow())}
