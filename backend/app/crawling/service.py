from datetime import timedelta
from sqlalchemy import select, func
from ..models import Company,Job,JobSource,CrawlRun,now
from .normalize import normalize,hashstr,canonical_url
from .adapters import adapter_for,discover
from .http import SafeHTTP,FetchError

def reconcile(db,company,raw_jobs,complete,provider=None):
    # Normalize the entire batch before changing state. Malformed data aborts atomically.
    provider=provider or company.ats_provider
    normalized=[normalize(raw,provider) for raw in raw_jobs]
    seen=set(); stamp=now()
    for data in normalized:
        identity=hashstr(provider+':'+str(data['external_id'])) if data['external_id'] else hashstr(data['source_url'])
        source=db.scalar(select(JobSource).where(JobSource.company_id==company.id,JobSource.identity==identity))
        job=db.get(Job,source.job_id) if source else None
        if not job:
            other=db.scalar(select(JobSource).where(JobSource.company_id==company.id,JobSource.url==data['source_url']))
            job=db.get(Job,other.job_id) if other else None
        if not job:
            job=db.scalar(select(Job).where(Job.company_id==company.id,Job.dedupe_key==data['dedupe_key']))
        if not job:
            job=Job(company_id=company.id,**data); db.add(job); db.flush()
        elif job.content_hash!=data['content_hash']:
            # Stable identity is retained if an existing source changes title/location.
            for key,value in data.items():
                if key!='dedupe_key': setattr(job,key,value)
        job.last_seen=stamp; job.missing_count=0
        job.status='closed' if job.expiration_date and job.expiration_date<stamp else 'active'
        if not source:
            source=JobSource(job_id=job.id,company_id=company.id,identity=identity,source=provider,url=data['source_url'],external_id=data['external_id']); db.add(source)
        source.last_seen=stamp; source.url=data['source_url']; seen.add(job.id)
    if complete:
        absent=db.scalars(select(Job).where(Job.company_id==company.id,Job.status!='closed',Job.is_demo==False)).all()
        for job in absent:
            if job.id not in seen:
                job.missing_count+=1
                # At least 3 successful complete snapshots and 48h absence before closure.
                job.status='closed' if job.missing_count>=3 and stamp-job.last_seen>=timedelta(hours=48) else 'potentially_closed'
    db.flush()
    company.active_jobs=db.scalar(select(func.count()).select_from(Job).where(Job.company_id==company.id,Job.status=='active'))
    return len(seen)

def collect(db,company,run,http=None):
    http=http or SafeHTTP(db)
    if not company.career_url and company.ats_provider=='generic':
        company.career_url,company.ats_provider,company.ats_id=discover(company,http)
        company.last_analysis=now()
    if company.ats_provider in ('greenhouse','lever','lever_eu','ashby','recruitee','smartrecruiters') and not company.ats_id:
        raise FetchError('Identifiant ATS requis')
    batch=adapter_for(company.ats_provider).fetch(company,http)
    run.count=reconcile(db,company,batch.jobs,batch.complete)
    run.complete=batch.complete; run.error=batch.note or None
    run.status='success'; run.finished_at=now()
    company.last_crawl=now(); company.last_success=now(); company.crawler_status='success' if batch.complete else 'partial'
