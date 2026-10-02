import json
from datetime import timedelta
from sqlalchemy import select
import pytest
from app.models import Job,JobSource,CrawlRun,now
from app.crawling.normalize import normalize,canonical_url
from app.crawling.service import reconcile,collect
from app.crawling.adapters import Greenhouse,Lever,Ashby,Generic,jsonld_jobs,discover,detect_ats,Batch,REGISTRY
from app.crawling.http import public_url,Blocked,FetchError,SafeHTTP
from app.matching import match,distance_km

class FakeHTTP:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def get(self,url):
        self.calls.append(url)
        if url not in self.pages:raise FetchError('missing fixture')
        return self.pages[url] if isinstance(self.pages[url],str) else json.dumps(self.pages[url])

def test_normalization(raw):
    data=normalize(raw,'greenhouse')
    assert data['domain']=='Data' and data['technologies']==['Python','SQL','Docker']
    assert data['contract_type']=='CDI' and data['seniority']=='junior'
    assert canonical_url('https://Example.com/jobs/1/?utm_source=x&lang=fr#top')=='https://example.com/jobs/1?lang=fr'
    with pytest.raises(ValueError):normalize({'title':'X','source_url':'javascript:alert(1)'},'generic')

def test_dedupe_same_id_url_and_semantics(db,company,raw):
    assert reconcile(db,company,[raw,raw],True)==1;db.commit()
    assert len(db.scalars(select(Job)).all())==1
    changed={**raw,'external_id':'different','source_url':'https://example.com/other/1'}
    reconcile(db,company,[changed],True);db.commit()
    assert len(db.scalars(select(Job)).all())==1
    assert len(db.scalars(select(JobSource)).all())==2
    edited={**raw,'title':'Data Engineer junior — updated','description':'Updated Python SQL'}
    reconcile(db,company,[edited],True);db.commit()
    assert len(db.scalars(select(Job)).all())==1
    assert db.scalar(select(Job)).title.endswith('updated')

def test_lifecycle_requires_complete_success_and_elapsed_time(db,company,raw):
    reconcile(db,company,[raw],True);db.commit();job=db.scalar(select(Job))
    reconcile(db,company,[],False);assert job.status=='active'
    reconcile(db,company,[],True);assert job.status=='potentially_closed'
    reconcile(db,company,[],True);reconcile(db,company,[],True);assert job.status=='potentially_closed'
    job.last_seen=now()-timedelta(days=3)
    reconcile(db,company,[],True);assert job.status=='closed'
    reconcile(db,company,[raw],True);assert job.status=='active' and job.missing_count==0

def test_failed_normalization_does_not_close_jobs(db,company,raw):
    reconcile(db,company,[raw],True);db.commit()
    with pytest.raises(ValueError):reconcile(db,company,[{'title':'bad'}],True)
    assert db.scalar(select(Job)).status=='active'

def test_expiration(db,company,raw):
    reconcile(db,company,[{**raw,'expiration_date':'2000-01-01'}],True)
    assert db.scalar(select(Job)).status=='closed'
    assert company.active_jobs==0

def test_greenhouse_and_collect(db,company):
    h=FakeHTTP({'https://boards-api.greenhouse.io/v1/boards/test/jobs?content=true':{'jobs':[{'id':123,'title':'Python junior','absolute_url':'https://example.com/jobs/123','content':'<p>Python SQL</p>','location':{'name':'Paris'}}]}})
    run=CrawlRun(company_id=company.id);db.add(run);db.flush()
    collect(db,company,run,h)
    assert run.status=='success' and run.count==1 and run.complete
    assert db.scalar(select(Job)).description=='Python SQL'

def test_lever_pagination(company):
    company.ats_provider='lever'
    first=[{'id':str(i),'text':'Developer','hostedUrl':f'https://jobs.lever.co/test/{i}','descriptionPlain':'Python','categories':{}} for i in range(100)]
    h=FakeHTTP({'https://api.lever.co/v0/postings/test?mode=json&limit=100&skip=0':first,'https://api.lever.co/v0/postings/test?mode=json&limit=100&skip=100':[]})
    batch=Lever().fetch(company,h)
    assert len(batch.jobs)==100 and batch.complete and len(h.calls)==2

def test_ashby_filters_unlisted(company):
    h=FakeHTTP({'https://api.ashbyhq.com/posting-api/job-board/test?includeCompensation=true':{'jobs':[{'id':'1','title':'Data','jobUrl':'https://example.com/1','isRemote':True},{'id':'2','isListed':False}]}})
    assert Ashby().fetch(company,h).jobs[0]['remote_type']=='remote'

def test_jsonld_arrays_graph_and_salary():
    data={'@graph':[{'@type':'JobPosting','title':'Analyste','description':'<p>SQL</p>','identifier':{'value':'abc'},'jobLocation':{'address':{'addressLocality':'Paris','addressCountry':'FR'}},'baseSalary':{'currency':'EUR','value':{'unitText':'YEAR','minValue':40000,'maxValue':45000}},'datePosted':'2026-01-02','url':'/jobs/1'}]}
    page='<script type="application/ld+json">'+json.dumps(data)+'</script>'
    result=jsonld_jobs(page,'https://example.com/careers')[0]
    assert result['city']=='Paris' and result['salary_min']==40000 and result['source_url']=='https://example.com/jobs/1'

def test_discovery_external_ats(company):
    h=FakeHTTP({'https://example.com':'<a href="https://jobs.ashbyhq.com/acme">Careers</a>'})
    assert discover(company,h)==('https://jobs.ashbyhq.com/acme','ashby','acme')
    assert detect_ats('https://job-boards.greenhouse.io/acme')==('greenhouse','acme')

def test_generic_is_never_complete(company):
    company.career_url='https://example.com/careers'
    h=FakeHTTP({company.career_url:'<script type="application/ld+json">{"@type":"JobPosting","title":"Dev","url":"https://example.com/jobs/1","description":"Python"}</script>'})
    assert Generic().fetch(company,h).complete is False

@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1','192.168.0.1','0.0.0.0','100.64.0.1'])
def test_ssrf_private_addresses(address,monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(None,None,None,None,(address,80))])
    with pytest.raises(Blocked):public_url('http://example.com')

@pytest.mark.parametrize('url',['file:///etc/passwd','http://a:b@example.com','http://example.com:8000'])
def test_ssrf_schemes_credentials_ports(url):
    with pytest.raises(Blocked):public_url(url)

def test_dns_mixed_public_private_rejected(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(None,None,None,None,('1.1.1.1',80)),(None,None,None,None,('127.0.0.1',80))])
    with pytest.raises(Blocked):public_url('https://example.com')

def test_conditional_cache(db,monkeypatch):
    h=SafeHTTP(db);seen=[]
    def request(url,headers,check):
        seen.append(headers)
        return (200,{'etag':'v1'},'hello') if len(seen)==1 else (304,{},'')
    monkeypatch.setattr(h,'_request',request)
    assert h.get('https://example.com')=='hello'
    assert h.get('https://example.com')=='hello'
    assert seen[1]['If-None-Match']=='v1'

def test_robots_denies(db,monkeypatch):
    h=SafeHTTP(db)
    monkeypatch.setattr(h,'_request',lambda *a:(200,{},'User-agent: *\nDisallow: /private'))
    with pytest.raises(Blocked):h.check_robots('https://example.com/private/jobs')
    h.check_robots('https://example.com/careers')

def test_match_unknown_distance_and_empty(job):
    assert match({},job)['score'] is None
    assert distance_km(43.2965,5.3698,43.5297,5.4474)<30
    profile={'city':'Aix-en-Provence','latitude':43.5297,'longitude':5.4474,'radius_km':30}
    assert match(profile,job)['score']==100
    job.remote_type='unknown'
    assert match({'remote_types':['remote']},job)['score']==50

def test_ashby_public_schema_without_id(company):
    h=FakeHTTP({'https://api.ashbyhq.com/posting-api/job-board/test?includeCompensation=true':{'jobs':[{'title':'Data Engineer','jobUrl':'https://jobs.ashbyhq.com/test/uuid-1','address':{'postalAddress':{'addressLocality':'Paris','addressCountry':'FR'}},'workplaceType':'Hybrid'}]}})
    job=Ashby().fetch(company,h).jobs[0]
    assert job['external_id']=='uuid-1' and job['city']=='Paris' and job['remote_type']=='hybrid'

def test_contract_at_end_of_title(raw):
    data=normalize({**raw,'title':'Développeur stage','contract_type':'unknown'},'generic')
    assert data['internship'] is True
