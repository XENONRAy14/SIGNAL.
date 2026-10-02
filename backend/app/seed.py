from sqlalchemy import select
from .db import SessionLocal
from .models import User,Company,Job
from .config import settings
from .security import passwords
from .crawling.normalize import normalize

DEMO=[
 ('Nova Systems','nova.example','Software','Développeur Full Stack — alternance','Paris','alternance','hybrid',['React','TypeScript','PostgreSQL'],48.8566,2.3522),
 ('Orbite Data','orbite.example','Data','Data Analyst junior','Marseille','CDI','hybrid',['Python','SQL','Power BI'],43.2965,5.3698),
 ('Cipher Labs','cipher.example','Cybersecurity','Analyste SOC — stage','Aix-en-Provence','stage','onsite',['Linux','Python','SQL'],43.5297,5.4474),
 ('Neural Works','neural.example','IA','Machine Learning Engineer junior','Paris','CDI','remote',['Python','PyTorch','Docker'],48.8566,2.3522),
 ('Cloud District','cloud.example','DevOps','Cloud & DevOps — alternance','Lyon','alternance','hybrid',['AWS','Terraform','Kubernetes','Docker'],45.764,4.8357),
 ('Flow Studio','flow.example','Automation','Automation Engineer junior','Marseille','CDI','remote',['Python','n8n','SQL'],43.2965,5.3698),
 ('Pixel Union','pixel.example','Design','UX Designer — stage','Bordeaux','stage','hybrid',['Figma'],44.8378,-.5792),
 ('Maison Finance','finance.example','Finance','Analyste financier junior','Paris','CDI','onsite',['Excel','SQL'],48.8566,2.3522),
 ('Green Factory','factory.example','Industrie','Ingénieur production — alternance','Lille','alternance','onsite',['Excel','SAP'],50.6292,3.0573),
 ('Pulse Creative','pulse.example','Marketing','Growth Marketing — stage','Nantes','stage','hybrid',['SEO','Excel'],47.2184,-1.5536),
 ('Aster Santé','aster.example','Santé','Chargé de projet santé junior','Nice','CDD','onsite',['Excel'],43.7102,7.262),
 ('People Collective','people.example','RH','Chargé de recrutement — alternance','Toulouse','alternance','hybrid',['Excel','Salesforce'],43.6047,1.4442),
]

def seed():
    with SessionLocal() as db:
        if settings.admin_password and not db.scalar(select(User).where(User.email==settings.admin_email)):
            if len(settings.admin_password)<12 or settings.admin_password.startswith('replace_'): raise RuntimeError('ADMIN_PASSWORD doit contenir au moins 12 caractères')
            db.add(User(email=settings.admin_email.lower(),name='Administrateur',password_hash=passwords.hash(settings.admin_password),is_admin=True))
        if settings.seed_demo:
            for name,domain,sector,title,city,contract,remote,skills,lat,lon in DEMO:
                if db.scalar(select(Company).where(Company.domain==domain)): continue
                company=Company(name=name,domain=domain,website_url='https://'+domain,industry=sector,country='France',cities=[city],size='50–250',enabled=False,is_demo=True,active_jobs=1)
                db.add(company); db.flush()
                desc=f'{name} recrute pour son équipe {sector}. Vous contribuerez à des projets concrets avec un accompagnement adapté aux profils juniors.\nCompétences recherchées : '+', '.join(skills)+'.\nMissions : contribuer à la conception, travailler en équipe, documenter et améliorer les solutions.\nCette annonce est une donnée fictive de démonstration ; aucune candidature réelle.'
                data=normalize(dict(title=title,description=desc,source_url='https://'+domain+'/jobs/demo',external_id='demo',location=city,city=city,country='France',latitude=lat,longitude=lon,contract_type=contract,remote_type=remote,skills=skills,domain=sector,seniority='junior'),'demo')
                db.add(Job(company_id=company.id,is_demo=True,**data))
        db.commit()

if __name__=='__main__': seed()
