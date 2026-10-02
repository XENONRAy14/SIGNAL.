import json
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.models import Company,Job,RobotsCache,SourceRun,User,now
from app.crawling import aggregators,discovery
from app.crawling.aggregators import Offer,FranceTravail,ft_offer
from app.crawling.discovery import name_key,ingest,expire,probe,discover_batch,run_source,due,slugs
from app.crawling.http import Robots,robots_state,SafeHTTP,Blocked,RetryLater,FetchError,ALLOW,BLOCK,RETRY

class FakeHTTP:
    def __init__(self,pages,retry=()):self.pages=pages;self.retry=retry;self.calls=[]
    def get(self,url):
        self.calls.append(url)
        if any(r in url for r in self.retry): raise RetryLater('busy')
        if url not in self.pages: raise FetchError('HTTP 404')
        return self.pages[url] if isinstance(self.pages[url],str) else json.dumps(self.pages[url])

def offer(company,title='Développeur Python',i='1',**kw):
    return Offer(company,{'external_id':i,'title':title,'description':'Python SQL','source_url':f'https://candidat.francetravail.fr/offres/{i}','city':'Lyon','country':'France',**kw})

# --- robots.txt (RFC 9309 + production caution) ---------------------------------------------

def test_robots_longest_match_wildcards_and_agent_group():
    r=Robots.parse('User-agent: *\nDisallow: /api/\nAllow: /api/public\nDisallow: /*.pdf$\n\nUser-agent: OtherBot\nDisallow: /')
    assert r.allows('https://x.com/jobs/1') and r.allows('https://x.com/api/public/jobs')
    assert not r.allows('https://x.com/api/private') and not r.allows('https://x.com/cv.pdf') and r.allows('https://x.com/cv.pdf?x=1')
    mine=Robots.parse('User-agent: *\nDisallow: /\n\nUser-agent: SignalBot\nDisallow: /private')
    assert mine.allows('https://x.com/jobs') and not mine.allows('https://x.com/private/1')
    assert Robots.parse('User-agent: *\nDisallow: /').allows('https://x.com/robots.txt')

@pytest.mark.parametrize('status,state',[(200,'parsed'),(404,'absent'),(410,'absent'),(401,'restricted'),(403,'restricted'),(429,RETRY),(500,RETRY),(503,RETRY)])
def test_robots_state_mapping(status,state):
    assert robots_state(status,'User-agent: *')==state

@pytest.mark.parametrize('status,decision',[(404,ALLOW),(401,ALLOW),(403,ALLOW),(429,RETRY),(503,RETRY)])
def test_robots_decisions_and_cache(db,monkeypatch,status,decision):
    h=SafeHTTP(db);calls=[]
    monkeypatch.setattr(h,'_request',lambda *a:calls.append(a) or (status,{},''))
    assert h.decide('https://api.example.com/jobs')==decision
    assert db.get(RobotsCache,'https://api.example.com').state==({404:'absent',401:'restricted',403:'restricted'}.get(status,RETRY))
    # Cached for the next worker: no second fetch.
    assert SafeHTTP(db).decide('https://api.example.com/other')==decision and len(calls)==1
    if decision==RETRY:
        with pytest.raises(RetryLater): SafeHTTP(db).check_robots('https://api.example.com/jobs')

def test_robots_network_error_retries_and_stale_copy_is_reused(db,monkeypatch):
    h=SafeHTTP(db)
    monkeypatch.setattr(h,'_request',lambda *a:(_ for _ in ()).throw(FetchError('Échec réseau')))
    assert h.decide('https://down.example.com/jobs')==RETRY
    db.add(RobotsCache(origin='https://old.example.com',state='parsed',status_code=200,body='User-agent: *\nDisallow: /private',fetched_at=now()-timedelta(days=2),expires_at=now()-timedelta(days=1)))
    db.flush()
    h2=SafeHTTP(db); monkeypatch.setattr(h2,'_request',lambda *a:(503,{},''))
    assert h2.decide('https://old.example.com/jobs')==ALLOW and h2.decide('https://old.example.com/private/x')==BLOCK

def test_explicit_disallow_still_blocks(db,monkeypatch):
    h=SafeHTTP(db); monkeypatch.setattr(h,'_request',lambda *a:(200,{},'User-agent: *\nDisallow: /'))
    with pytest.raises(Blocked): h.check_robots('https://api.example.com/v1/jobs')

# --- France Travail ---------------------------------------------------------------------------

FT_RAW={'id':'190XYZ','intitule':'Développeur web en alternance (H/F)','description':'Vous développerez en Python et React.','dateCreation':'2026-10-01T08:00:00.000Z',
    'lieuTravail':{'libelle':'13 - Marseille 1er Arrondissement','latitude':43.3,'longitude':5.38},'entreprise':{'nom':'ACME SAS','url':'https://www.acme.fr'},
    'typeContrat':'CDD','natureContrat':'Contrat apprentissage','salaire':{'libelle':'Annuel de 18000.00 Euros à 21000.00 Euros sur 12 mois'},
    'experienceLibelle':'Débutant accepté','secteurActiviteLibelle':'Programmation informatique','competences':[{'libelle':'Développer une application'}],
    'origineOffre':{'urlOrigine':'https://candidat.francetravail.fr/offres/recherche/detail/190XYZ'}}

def test_ft_offer_mapping():
    o=ft_offer(FT_RAW)
    assert o.company=='ACME SAS' and o.website=='https://www.acme.fr' and o.industry=='Programmation informatique'
    r=o.raw
    assert r['contract_type']=='alternance' and r['city']=='Marseille 1er Arrondissement' and r['latitude']==43.3
    assert r['salary_min']==18000 and r['salary_max']==21000 and r['salary_currency']=='EUR'
    assert 'Débutant accepté' in r['description'] and 'Développer une application' in r['description']
    assert ft_offer({**FT_RAW,'natureContrat':'Contrat travail','typeContrat':'MIS','salaire':{'libelle':'Mensuel de 1801 Euros'}}).raw['contract_type']=='intérim'

class FakeResponse:
    def __init__(self,status,body=None,total=None):self.status_code=status;self.body=body or {};self.headers={'content-range':f'offres 0-149/{total}'} if total is not None else {}
    def json(self):return self.body

class FakeFT:
    """Simulates the 1150-result ceiling: the national query is too large, each department fits."""
    def __init__(self):self.calls=[]
    def request(self,method,url,**kw):
        self.calls.append((method,kw.get('params')))
        if method=='POST':return FakeResponse(200,{'access_token':'t','expires_in':1500})
        dept=kw['params'].get('departement')
        if not dept:return FakeResponse(206,{'resultats':[]},total=5000)
        first=int(kw['params']['range'].split('-')[0])
        rows=[{'id':f'{dept}-{i}','intitule':'Job'} for i in range(first,min(first+150,200))]
        return FakeResponse(206 if rows else 204,{'resultats':rows},total=200)

def test_ft_window_splits_by_department(monkeypatch):
    monkeypatch.setattr(aggregators,'FT_DEPARTEMENTS',['13','75'])
    api=FakeFT(); ft=FranceTravail(); start=now()-timedelta(hours=1)
    rows=ft.window(api,start,now())
    assert len(rows)==400 and {r['id'].split('-')[0] for r in rows}=={'13','75'}
    ranges=[p['range'] for m,p in api.calls if m=='GET' and p.get('departement')=='13']
    assert ranges==['0-149','150-299']

def test_ft_range_never_exceeds_api_ceiling(monkeypatch):
    api=FakeFT(); ft=FranceTravail(); seen=[]
    monkeypatch.setattr(ft,'page',lambda api,s,e,d,first:(seen.append(first) or ([{'id':str(first+i)} for i in range(150)],1149)))
    ft.window(api,now()-timedelta(hours=1),now(),'75')
    assert seen==[0,150,300,450,600,750,900,1000]

# --- Ingestion ---------------------------------------------------------------------------------

def test_name_key_and_slugs():
    assert name_key('DOCTOLIB SAS')==name_key('Doctolib')=='doctolib'
    assert name_key('Back Market')=='back market' and slugs('Back Market')==['backmarket','back-market']
    assert 'mistral' in slugs('Mistral Technologies')

def test_ingest_creates_and_dedupes_companies(db):
    cache={}
    count=ingest(db,'francetravail',[offer('DOCTOLIB SAS',i='1'),offer('Doctolib',title='Data Engineer',i='2'),offer(None,i='3'),offer('Boulangerie Martin',i='4',title='')],cache)
    db.commit()
    assert count==3
    companies={c.name_key:c for c in db.scalars(select(Company))}
    assert set(companies)=={'doctolib','employeur-non-communique'}
    doc=companies['doctolib']
    assert doc.ats_provider=='aggregated' and doc.discovery_status=='pending' and doc.discovered_via=='francetravail' and doc.domain=='~doctolib' and doc.active_jobs==2
    assert companies['employeur-non-communique'].discovery_status=='skip'
    assert {j.source for j in db.scalars(select(Job))}=={'francetravail'}

def test_ingest_uses_website_domain_and_skips_direct_sources(db):
    direct=Company(name='Qonto',name_key='qonto',domain='qonto.com',website_url='https://qonto.com',ats_provider='lever',ats_id='qonto',last_success=now())
    db.add(direct); db.commit()
    assert ingest(db,'adzuna',[offer('Qonto'),Offer('Acme',offer('Acme').raw,'https://www.acme.fr')],{})==1
    db.commit()
    acme=db.scalar(select(Company).where(Company.name_key=='acme'))
    assert acme.domain=='acme.fr' and acme.website_url=='https://acme.fr'
    assert db.scalar(select(Job).where(Job.company_id==direct.id)) is None

def test_aggregated_jobs_expire(db):
    ingest(db,'francetravail',[offer('Acme')],{}); db.commit()
    job=db.scalar(select(Job)); job.last_seen=now()-timedelta(days=31); db.commit()
    assert expire(db)==1 and job.status=='closed' and db.scalar(select(Company)).active_jobs==0

class StubSource(aggregators.Source):
    name='stub'; label='Stub'; interval=timedelta(hours=1)
    def __init__(self,chunks,fail=None):self.chunks=chunks;self.fail=fail;self.cursors=[]
    def fetch(self,cursor,deadline,http):
        self.cursors.append(cursor)
        for i,chunk in enumerate(self.chunks): yield chunk,now()-timedelta(hours=len(self.chunks)-i)
        if self.fail: raise self.fail

def test_run_source_commits_chunks_and_resumes_cursor(db,monkeypatch):
    stub=StubSource([[offer('Acme',i='1')],[offer('Beta',i='2')]])
    monkeypatch.setitem(aggregators.SOURCES,'stub',stub)
    run=run_source(db,'stub',http=object())
    assert run.status=='success' and run.count==2 and run.companies==2 and run.cursor
    run_source(db,'stub',http=object())
    assert stub.cursors[1]==run.cursor
    failing=StubSource([[offer('Gamma',i='3')]],fail=RetryLater('HTTP 503'))
    monkeypatch.setitem(aggregators.SOURCES,'stub',failing)
    bad=run_source(db,'stub',http=object())
    assert bad.status=='retry' and 'HTTP 503' in bad.error
    assert db.scalar(select(Company).where(Company.name_key=='gamma')) is not None  # committed chunk kept

def test_unconfigured_source_is_disabled_and_not_due(db,monkeypatch):
    monkeypatch.setattr(aggregators.settings,'ft_client_id','')
    assert not due(db,'francetravail')
    assert run_source(db,'francetravail').status=='disabled'

def test_due_resumes_partial_runs(db):
    db.add(SourceRun(source='arbeitnow',status='success',started_at=now()-timedelta(hours=1))); db.commit()
    assert not due(db,'arbeitnow')
    db.add(SourceRun(source='arbeitnow',status='partial',started_at=now()-timedelta(minutes=10))); db.commit()
    assert due(db,'arbeitnow')

# --- ATS probing -------------------------------------------------------------------------------

GH='https://boards-api.greenhouse.io/v1/boards/{}'
LEVER='https://api.lever.co/v0/postings/{}?mode=json&limit=5'

def company_named(db,name,**kw):
    c=Company(name=name,name_key=name_key(name),domain='~'+name_key(name),website_url='',ats_provider='aggregated',**kw); db.add(c); db.commit(); return c

def test_probe_greenhouse_requires_matching_board_name(db):
    c=company_named(db,'Back Market')
    assert probe(c,FakeHTTP({GH.format('backmarket'):{'name':'Back Market'}}))==('greenhouse','backmarket','https://job-boards.greenhouse.io/backmarket')
    assert probe(c,FakeHTTP({GH.format('backmarket'):{'name':'Backmarket Plumbing Ltd Australia'}})) is None

def test_probe_lever_checks_descriptions(db):
    c=company_named(db,'Qonto')
    good=[{'id':'1','text':'Dev','descriptionPlain':'Join Qonto, the business account...'}]
    assert probe(c,FakeHTTP({LEVER.format('qonto'):good}))[0]=='lever'
    assert probe(c,FakeHTTP({LEVER.format('qonto'):[{'id':'1','descriptionPlain':'Another company'}]})) is None

def test_discover_batch_promotes_found_and_marks_not_found(db):
    found=company_named(db,'Back Market',active_jobs=5); missing=company_named(db,'Boulangerie Martin',active_jobs=1)
    company_named(db,'Employeur',discovery_status='skip')
    pages={GH.format('backmarket'):{'name':'Back Market'}}
    result=discover_batch(db,http_factory=lambda:FakeHTTP(pages))
    assert result=={'checked':2,'found':1}
    assert found.ats_provider=='greenhouse' and found.ats_id=='backmarket' and found.discovery_status=='found' and found.crawler_status=='idle'
    assert missing.discovery_status=='not_found' and missing.discovery_checked_at
    assert discover_batch(db,http_factory=lambda:FakeHTTP(pages))['checked']==0

def test_discover_batch_marks_duplicate_board(db):
    db.add(Company(name='Back Market',name_key='back market',domain='backmarket.com',website_url='https://backmarket.com',ats_provider='greenhouse',ats_id='backmarket')); db.commit()
    alias=company_named(db,'BackMarket')
    discover_batch(db,http_factory=lambda:FakeHTTP({GH.format('backmarket'):{'name':'Back Market'}}))
    assert alias.discovery_status=='duplicate' and alias.ats_provider=='aggregated'

def test_discover_batch_keeps_pending_when_nothing_answers(db):
    c=company_named(db,'Acme')
    discover_batch(db,http_factory=lambda:FakeHTTP({},retry=('',)))
    assert c.discovery_status=='pending'

# --- API -------------------------------------------------------------------------------------

def test_admin_sources_and_upgrade_aggregated_company(client,auth,db,monkeypatch):
    assert client.get('/api/admin/sources').status_code==403
    user=db.get(User,auth['id']);user.is_admin=True;db.commit()
    ingest(db,'francetravail',[offer('Acme')],{}); db.commit()
    data=client.get('/api/admin/sources').json()
    assert {s['name'] for s in data['sources']}=={'francetravail','adzuna','arbeitnow'}
    assert next(s for s in data['sources'] if s['name']=='francetravail')['active_jobs']==1
    assert data['discovery']['pending']==1
    monkeypatch.setattr('app.main.public_url',lambda url:(__import__('urllib.parse').parse.urlsplit(url),'1.1.1.1'))
    r=client.post('/api/admin/companies',json={'name':'ACME','website_url':'https://acme.fr','ats_provider':'greenhouse','ats_id':'acme'})
    assert r.status_code==201 and len(db.scalars(select(Company)).all())==1
    acme=db.scalar(select(Company)); assert acme.ats_provider=='greenhouse' and acme.domain=='acme.fr' and acme.discovery_status=='manual'
    detail=client.get('/api/jobs/'+db.scalar(select(Job)).id).json()
    assert detail['source_label']=='France Travail' and 'France Travail' in detail['attribution']
