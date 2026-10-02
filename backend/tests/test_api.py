from sqlalchemy import select
from app.models import User,Resume,TailoredResume,AuthSession

PASSWORD='a-valid-long-password'

def test_auth_cookie_session_and_logout(client,auth):
    assert client.get('/api/auth/me').json()['email']=='test@example.com'
    assert 'signal_session' in client.cookies
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/auth/me').status_code==401

def test_csrf_and_origin(client,auth):
    assert client.put('/api/profile',headers={'x-csrf-token':'wrong'},json={}).status_code==403
    assert client.put('/api/profile',headers={'Origin':'https://evil.example'},json={}).status_code==403
    assert client.put('/api/profile',json={'skills':['Python','SQL'],'city':'Marseille'}).status_code==200

def test_registration_cannot_set_admin(client,db):
    response=client.post('/api/auth/register',json={'email':'hacker@example.com','password':PASSWORD,'is_admin':True})
    assert response.status_code==201 and response.json()['is_admin'] is False
    assert client.get('/api/admin/crawls').status_code==403

def test_bad_password_and_duplicate(client,auth):
    assert client.post('/api/auth/login',json={'email':auth['email'],'password':'wrong-password'}).status_code==401
    assert client.post('/api/auth/register',json={'email':auth['email'],'password':PASSWORD}).status_code==409

def test_filter_match_detail_saved(client,auth,job):
    assert client.put('/api/profile',json={'skills':['Python','SQL'],'domains':['Data'],'city':'Marseille','contracts':['CDI'],'remote_types':['hybrid'],'seniority':'junior'}).status_code==200
    response=client.get('/api/jobs?q=data&contract=CDI')
    assert response.status_code==200,response.text
    result=response.json();assert result['total']==1
    assert result['items'][0]['match']['score']==100
    assert client.get('/api/jobs?contract=stage').json()['total']==0
    assert client.get('/api/jobs/'+job.id).json()['company']['name']=='Test Company'
    assert client.put('/api/saved/'+job.id,json={'stage':'applied','note':'CV envoyé'}).status_code==200
    assert client.get('/api/saved').json()[0]['stage']=='applied'
    assert client.get('/api/jobs').json()['items'][0]['saved_stage']=='applied'
    assert client.delete('/api/saved/'+job.id).status_code==200
    assert client.get('/api/saved').json()==[]

def test_pagination_validation(client,auth,job):
    assert client.get('/api/jobs?page=0').status_code==422
    assert client.get('/api/jobs?sort=recent&limit=1').json()['items'][0]['id']==job.id
    assert client.get('/api/jobs?page=2&limit=1').json()['items']==[]

def test_resume_tailor_download_and_isolation(client,auth,job,db):
    original='Camille Martin\nData Engineer\n2024 — Projet Python et SQL pour une association.\nFormation informatique — licence obtenue en 2023.'
    response=client.post('/api/resumes',json={'name':'CV Camille','text':original})
    assert response.status_code==201;rid=response.json()['id']
    variant=client.post('/api/resumes/tailor',json={'resume_id':rid,'job_id':job.id})
    assert variant.status_code==201,variant.text
    result=variant.json();assert original in result['text']
    assert all(line in original for line in result['explanation']['highlights'])
    tid=result['id'];download=client.get('/api/tailored/'+tid+'/download')
    assert download.content.startswith(b'PK')
    assert client.get('/api/tailored/'+tid+'/download?format=txt').status_code==200
    second=client.post('/api/auth/register',json={'email':'other@example.com','password':PASSWORD}).json()
    client.headers['x-csrf-token']=second['csrf']
    assert client.get('/api/resumes').json()==[]
    assert client.get('/api/tailored/'+tid+'/download').status_code==404
    assert client.post('/api/resumes/tailor',json={'resume_id':rid,'job_id':job.id}).status_code==404
    assert client.delete('/api/resumes/'+rid).status_code==404

def test_resume_file_and_validation(client,auth):
    assert client.post('/api/resumes/upload',files={'file':('cv.txt',b'Python engineer. Long enough resume text with projects and experience.','text/plain')}).status_code==201
    assert client.post('/api/resumes/upload',files={'file':('cv.txt',b'tiny','text/plain')}).status_code==400
    assert client.post('/api/resumes/upload',files={'file':('cv.exe',b'x'*100,'application/octet-stream')}).status_code==400
    assert client.put('/api/profile',json={'latitude':123}).status_code==422

def test_account_export_delete_cascade(client,auth,job,db):
    rid=client.post('/api/resumes',json={'text':'An engineer with Python experience and SQL projects since 2020.'}).json()['id']
    client.post('/api/resumes/tailor',json={'resume_id':rid,'job_id':job.id})
    assert len(client.get('/api/account/export').json()['resumes'])==1
    assert client.delete('/api/account').status_code==200
    assert db.scalar(select(Resume)) is None
    assert db.scalar(select(TailoredResume)) is None
    assert db.scalar(select(AuthSession)) is None
    assert db.scalar(select(User)) is None
    assert client.get('/api/auth/me').status_code==401

def test_company_list_stats_connectors(client,auth,company,job):
    assert client.get('/api/companies').json()['total']==1
    assert client.get('/api/stats').json()['real_jobs']==1
    connectors=client.get('/api/connectors').json()
    assert next(c for c in connectors if c['name']=='workday')['closure_supported'] is False

def test_admin_private_url_rejected(client,auth,db,monkeypatch):
    user=db.get(User,auth['id']);user.is_admin=True;db.commit()
    monkeypatch.setattr('app.crawling.http.socket.getaddrinfo',lambda *a,**k:[(None,None,None,None,('127.0.0.1',80))])
    assert client.post('/api/admin/companies',json={'name':'Internal','website_url':'http://localhost'}).status_code==400

def test_rate_limiter(monkeypatch):
    from app.security import rate_limit,_local,redis
    from fastapi import HTTPException
    import pytest
    _local.clear()
    monkeypatch.setattr(redis,'pipeline',lambda:(_ for _ in ()).throw(ConnectionError()))
    rate_limit('test-rate',limit=1)
    with pytest.raises(HTTPException) as e:rate_limit('test-rate',limit=1)
    assert e.value.status_code==429
