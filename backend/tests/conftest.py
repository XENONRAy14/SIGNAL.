import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base,get_db
from app.main import app
from app.models import Company,Job,User
from app.crawling.normalize import normalize
from app import security

@pytest.fixture
def db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(engine,expire_on_commit=False)() as session: yield session
    engine.dispose()

@pytest.fixture
def client(db,monkeypatch):
    app.dependency_overrides[get_db]=lambda:db
    monkeypatch.setattr(security.redis,'pipeline',lambda:(_ for _ in ()).throw(ConnectionError()))
    security._local.clear()
    with TestClient(app) as client: yield client
    app.dependency_overrides.clear()

@pytest.fixture
def auth(client):
    response=client.post('/api/auth/register',json={'name':'Test','email':'test@example.com','password':'a-valid-long-password'})
    assert response.status_code==201
    client.headers['x-csrf-token']=response.json()['csrf']
    return response.json()

@pytest.fixture
def company(db):
    company=Company(name='Test Company',domain='example.com',website_url='https://example.com',ats_provider='greenhouse',ats_id='test')
    db.add(company);db.commit();return company

@pytest.fixture
def raw():
    return {'title':'Data Engineer junior','description':'Python SQL Docker, construire une plateforme de données.','source_url':'https://example.com/jobs/1','external_id':'1','location':'Marseille','city':'Marseille','country':'France','contract_type':'CDI','remote_type':'hybrid','latitude':43.2965,'longitude':5.3698}

@pytest.fixture
def job(db,company,raw):
    job=Job(company_id=company.id,**normalize(raw,'greenhouse'));db.add(job);db.commit();return job
