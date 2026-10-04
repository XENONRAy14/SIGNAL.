import pytest
from sqlalchemy import select
from app.geo import locate
from app.models import Company,Job,User
from app.crawling.normalize import normalize,detect_contract
from app.seed import reclassify

@pytest.mark.parametrize('text,city',[('Marseille','Marseille'),('Paris, France','Paris'),('13 - Marseille 1er Arrondissement','Marseille'),('Paris 15e','Paris'),
    ('Aix-en-Provence, Provence-Alpes-Côte d’Azur, France','Aix-en-Provence'),('Saint-Étienne','Saint-Étienne'),('Remote - France; Lyon','Lyon'),('Paris, France; London, UK','Paris')])
def test_locate_french_places(text,city):
    name,lat,lon,france=locate(text)
    assert name==city and lat and lon and france

@pytest.mark.parametrize('text',['US','New York, New York, USA','London','Munich, Germany','St. Louis, MO','Montreal, Canada','Remote - US','Bangalore, India','EU'])
def test_locate_rejects_foreign_places(text):
    name,lat,lon,france=locate(text)
    assert name is None and lat is None and not france

def test_locate_country_only():
    assert locate('France')==(None,None,None,True)

@pytest.mark.parametrize('title,etype,desc,expected',[
    ('Alternance - Data Analyst (H/F)','','', 'alternance'),('Apprenti Développeur','','', 'alternance'),('Data Engineer Apprenticeship','','', 'alternance'),
    ('Stage - Business Developer','','', 'stage'),('Software Engineering Intern','','', 'stage'),('Stagiaire marketing','','', 'stage'),
    ('Data Analyst','Internship','', 'stage'),('Data Analyst','','Poste en alternance, rythme 3 semaines / 1 semaine.','alternance'),
    ('Data Analyst','','Stage de 6 mois à partir de janvier.','stage'),('Founding Engineer (early-stage startup)','','', 'unknown'),
    ('Internal Audit Manager','','', 'unknown'),('International Sales Lead','','', 'unknown'),('Conseiller assurance vie','','', 'unknown'),('Ingénieur de recherche : apprentissage faiblement supervisé','','', 'unknown'),('Comptable en apprentissage','','', 'alternance'),
    ('VIE - Business Developer','','', 'graduate'),('Product Manager (CDI)','','', 'CDI'),('Backend Engineer','Full-time','We are hiring.','unknown')])
def test_detect_contract(title,etype,desc,expected):
    assert detect_contract(title,etype,desc)==expected

def test_normalize_geocodes_and_sets_country():
    data=normalize({'title':'Stage Data','source_url':'https://x.fr/1','location':'Marseille, France'},'lever')
    assert data['city']=='Marseille' and data['country']=='France' and abs(data['latitude']-43.28)<0.1 and data['contract_type']=='stage'
    us=normalize({'title':'Engineer','source_url':'https://x.com/2','location':'New York, NY'},'lever')
    assert us['latitude'] is None and us['country'] is None

def add_job(db,company,title,location,**kw):
    job=Job(company_id=company.id,**normalize({'title':title,'source_url':f'https://x.fr/{len(title)}{location}','location':location,**kw},'greenhouse'))
    db.add(job); return job

@pytest.fixture
def radar_jobs(db):
    c=Company(name='Acme',domain='acme.fr',website_url='https://acme.fr'); db.add(c); db.flush()
    jobs={'aix_alt':add_job(db,c,'Alternance Data','Aix-en-Provence, France'),'aubagne_stage':add_job(db,c,'Stage Data','Aubagne'),
        'marseille_cdi':add_job(db,c,'Data Engineer (CDI)','Marseille'),'paris_alt':add_job(db,c,'Alternance Data','Paris'),
        'us_alt':add_job(db,c,'Data Apprentice','New York, NY'),'remote_fr_alt':add_job(db,c,'Alternance Data','Remote - France',remote_type='remote'),
        'remote_us_alt':add_job(db,c,'Alternance Data','Remote - US',remote_type='remote'),'marseille_unknown':add_job(db,c,'Data Engineer','Marseille')}
    db.commit(); return jobs

def titles(client,**params):
    r=client.get('/api/jobs',params={'limit':100,**params}).json()
    return {(j['title'],j['location']) for j in r['items']},r

def test_radar_applies_profile_contracts_and_radius(client,auth,db,radar_jobs):
    assert client.put('/api/profile',json={'city':'Marseille','radius_km':50,'contracts':['alternance','stage'],'remote_types':['remote','hybrid','onsite']}).status_code==200
    profile=db.get(User,auth['id']).profile
    assert abs(profile['latitude']-43.28)<0.1  # geocoded from the city on save
    found,r=titles(client)
    assert found=={('Alternance Data','Aix-en-Provence, France'),('Stage Data','Aubagne'),('Alternance Data','Remote - France')}
    assert r['total']==3 and r['unfiltered_total']==8 and set(r['profile_filter']['active'])=={'contracts','distance'}
    _,everything=titles(client,use_profile='false')
    assert everything['total']==8 and everything['profile_filter'] is None

def test_radar_without_remote_excludes_remote_elsewhere(client,auth,db,radar_jobs):
    client.put('/api/profile',json={'city':'Marseille','radius_km':50,'contracts':['alternance'],'remote_types':['onsite','hybrid']})
    found,_=titles(client)
    assert found=={('Alternance Data','Aix-en-Provence, France')}

def test_radar_country_only_profile(client,auth,db,radar_jobs):
    client.put('/api/profile',json={'city':'','country':'France','contracts':['alternance']})
    found,_=titles(client)
    assert ('Alternance Data','Paris') in found and ('Data Apprentice','New York, NY') not in found and ('Alternance Data','Remote - US') not in found

def test_profile_city_change_regeocodes(client,auth,db):
    client.put('/api/profile',json={'city':'Marseille'})
    p=db.get(User,auth['id']).profile
    client.put('/api/profile',json={'city':'Lyon','latitude':p['latitude'],'longitude':p['longitude']})
    assert abs(db.get(User,auth['id']).profile['latitude']-45.76)<0.1
    client.put('/api/profile',json={'city':'Lyon','latitude':45.0,'longitude':5.0})
    assert db.get(User,auth['id']).profile['latitude']==45.0

def test_reclassify_backfills_existing_rows(db):
    c=Company(name='Old',domain='old.fr',website_url='https://old.fr'); db.add(c); db.flush()
    old=Job(company_id=c.id,**{**normalize({'title':'Stage Data','source_url':'https://old.fr/1'},'lever'),'location':'Marseille','contract_type':'unknown','latitude':None,'longitude':None})
    db.add(old); db.commit()
    assert reclassify(db)>=1
    assert old.contract_type=='stage' and old.internship and old.country=='France' and old.latitude
