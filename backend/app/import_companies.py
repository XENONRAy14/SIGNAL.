"""CLI import of an explicitly supplied company CSV; no unbounded company scraping."""
import argparse,csv
from sqlalchemy import select
from .schemas import CompanyInput
from .models import Company
from .db import SessionLocal
from .crawling.http import public_url
from .crawling.discovery import name_key

def main():
    parser=argparse.ArgumentParser(description='Importer des entreprises depuis un CSV UTF-8')
    parser.add_argument('csv_path');args=parser.parse_args()
    with open(args.csv_path,encoding='utf-8-sig',newline='') as f, SessionLocal() as db:
        created=skipped=0
        for line,row in enumerate(csv.DictReader(f),2):
            try:
                data=CompanyInput(**{k:v for k,v in row.items() if v})
                p,_=public_url(data.website_url)
                if data.career_url:public_url(data.career_url)
                if db.scalar(select(Company).where(Company.domain==p.hostname.lower())):skipped+=1;continue
                db.add(Company(domain=p.hostname.lower(),name_key=name_key(data.name),discovered_via='manual',discovery_status='manual',**data.model_dump()));db.commit();created+=1
            except Exception as e:
                db.rollback();raise SystemExit(f'Ligne {line} rejetée : {e}. {created} entreprises déjà importées.')
        print(f'{created} entreprises ajoutées, {skipped} déjà présentes. Collecte automatique au prochain cycle.')
if __name__=='__main__':main()
