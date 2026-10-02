"""Run against an isolated migrated DB and Redis URL, e.g. Redis DB 14. Starts its own worker."""
import subprocess,sys,time,uuid
from sqlalchemy import select,delete
from app.db import SessionLocal
from app.models import Company,CrawlRun,Job,JobSource
from app.tasks import enqueue

def main():
    worker=subprocess.Popen([sys.executable,'-m','celery','-A','tests.queue_fixture_worker.celery','worker','--pool=solo','--concurrency=1','--loglevel=warning'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    company_id=None
    try:
        with SessionLocal() as db:
            c=Company(name='QUEUE FIXTURE',domain=str(uuid.uuid4())+'.example',website_url='https://example.com',ats_provider='greenhouse',ats_id='signal-fixture',enabled=False)
            db.add(c);db.commit();company_id=c.id;run=enqueue(db,c);run_id=run.id
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            with SessionLocal() as db:
                run=db.get(CrawlRun,run_id)
                if run.status=='success':
                    assert run.count==1 and run.complete
                    assert db.scalar(select(Job).where(Job.company_id==company_id)).title=='Data Engineer junior'
                    print('PASS Redis -> Celery -> adapter fixture -> PostgreSQL: successful persisted collection')
                    return
                if run.status in ('error','blocked'): raise AssertionError(run.error)
            time.sleep(.2)
        raise AssertionError('Worker did not complete within 30s')
    finally:
        worker.terminate();worker.wait(timeout=10)
        if company_id:
            with SessionLocal() as db:
                for model in (JobSource,Job,CrawlRun):db.execute(delete(model).where(model.company_id==company_id))
                db.execute(delete(Company).where(Company.id==company_id));db.commit()
if __name__=='__main__':main()
