import logging
from datetime import timedelta
from celery import Celery
from sqlalchemy import select,delete
from redis import Redis
from .config import settings
from .db import SessionLocal
from .models import Company,CrawlRun,AuthSession,now
from .crawling.service import collect
from .crawling.http import Blocked

log=logging.getLogger('signal.crawler')
celery=Celery('signal',broker=settings.redis_url,backend=settings.redis_url)
celery.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',timezone='UTC',
    task_acks_late=True,worker_prefetch_multiplier=1,task_soft_time_limit=840,task_time_limit=900,
    broker_connection_retry_on_startup=True,result_expires=3600,
    beat_schedule={'schedule-due-companies':{'task':'signal.schedule','schedule':300.0}})
r=Redis.from_url(settings.redis_url,socket_connect_timeout=3,socket_timeout=3)

def enqueue(db,company):
    # Serialize enqueue for API + scheduler to avoid duplicate concurrent dispatch.
    with r.lock('enqueue:'+company.id,timeout=20,blocking_timeout=2):
        current=db.scalar(select(CrawlRun).where(CrawlRun.company_id==company.id,CrawlRun.status.in_(['queued','running','retry']),CrawlRun.started_at>now()-timedelta(hours=2)))
        if current: return current
        run=CrawlRun(company_id=company.id); db.add(run); company.crawler_status='queued'; db.commit()
        try: crawl_company.apply_async(args=[run.id],priority=0 if company.active_jobs else 6)
        except Exception:
            run.status='error'; run.error='Queue indisponible'; run.finished_at=now(); company.crawler_status='error'; db.commit(); raise
        return run

@celery.task(bind=True,name='signal.crawl',max_retries=3)
def crawl_company(self,run_id):
    with SessionLocal() as db:
        run=db.get(CrawlRun,run_id)
        if not run or run.status=='success': return
        company=db.get(Company,run.company_id)
        lock=r.lock('crawl:company:'+company.id,timeout=950,blocking_timeout=1)
        if not lock.acquire(blocking=True): raise self.retry(countdown=60)
        try:
            run.status='running'; run.attempts+=1; company.crawler_status='running'; db.commit()
            try:
                collect(db,company,run); db.commit()
                log.info('crawl_success company=%s run=%s count=%d complete=%s',company.id,run_id,run.count,run.complete)
            except Exception as exc:
                db.rollback(); run=db.get(CrawlRun,run_id); company=db.get(Company,run.company_id)
                blocked=isinstance(exc,Blocked)
                retry=not blocked and self.request.retries<self.max_retries
                run.status='retry' if retry else ('blocked' if blocked else 'error')
                run.error=(type(exc).__name__+': '+str(exc))[:1000]
                run.finished_at=None if retry else now(); company.crawler_status=run.status; company.last_crawl=now(); db.commit()
                log.warning('crawl_failure company=%s run=%s error=%s',company.id,run_id,run.error)
                if retry: raise self.retry(exc=exc,countdown=30*(2**self.request.retries))
        finally:
            try: lock.release()
            except Exception: pass

@celery.task(name='signal.schedule')
def schedule():
    with r.lock('scheduler:singleton',timeout=240,blocking_timeout=1):
        with SessionLocal() as db:
            # Recover orphaned tasks; they must not block the queue forever.
            for run in db.scalars(select(CrawlRun).where(CrawlRun.status.in_(['queued','running','retry']),CrawlRun.started_at<now()-timedelta(hours=2))):
                run.status='error'; run.error='Tâche expirée, replanification'; run.finished_at=now()
            db.execute(delete(AuthSession).where(AuthSession.expires<now())); db.commit()
            companies=db.scalars(select(Company).where(Company.enabled==True,Company.is_demo==False).order_by(Company.active_jobs.desc())).all()
            for company in companies:
                interval=timedelta(hours=6 if company.active_jobs else 24)
                if company.last_crawl is None or now()-company.last_crawl>interval: enqueue(db,company)
