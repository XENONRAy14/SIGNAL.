"""Verify candidate employers against public ATS boards and write the starter catalog.

Usage: python -m app.build_catalog app/data/catalog_candidates.csv app/data/catalog.csv
Each candidate row is probed live (robots.txt and pacing apply); only boards that name the employer are kept.
"""
import argparse, csv, sys
from .db import SessionLocal
from .models import Company
from .crawling.http import SafeHTTP, RetryLater
from .crawling.discovery import probe, name_key

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidates'); parser.add_argument('output'); args=parser.parse_args()
    with open(args.candidates,encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    found=[]
    with SessionLocal() as db:
        for i,row in enumerate(rows,1):
            company=Company(name=row['name'],name_key=name_key(row['name']),website_url='')
            try: result=probe(company,SafeHTTP(db))
            except RetryLater as exc: result=None; print(f'[{i}/{len(rows)}] {row["name"]}: réessayer plus tard ({exc})',file=sys.stderr)
            db.commit()
            if result:
                provider,slug,url=result
                found.append({'name':row['name'],'website_url':row['website_url'],'career_url':url,'ats_provider':provider,'ats_id':slug,'industry':row.get('industry','')})
            print(f'[{i}/{len(rows)}] {row["name"]}: {result[0]+"/"+result[1] if result else "—"}',file=sys.stderr,flush=True)
    with open(args.output,'w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['name','website_url','career_url','ats_provider','ats_id','industry'],lineterminator='\n')
        writer.writeheader(); writer.writerows(sorted(found,key=lambda r:r['name'].lower()))
    print(f'{len(found)}/{len(rows)} entreprises vérifiées -> {args.output}')

if __name__=='__main__': main()
