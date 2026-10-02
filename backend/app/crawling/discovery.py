"""Automatic company discovery: employers found in aggregator offers, then promoted to direct ATS sources when found."""
import json, logging, re, time
from datetime import timedelta
from urllib.parse import urlsplit
from sqlalchemy import select, update, func
from ..config import settings
from ..matching import norm
from ..models import Company, Job, SourceRun, now
from .adapters import discover
from .aggregators import SOURCES
from .http import SafeHTTP, FetchError, Blocked, RetryLater
from .normalize import normalize
from .service import reconcile

log=logging.getLogger('signal.discovery')
LEGAL=re.compile(r'\b(sas|sasu|sarl|sa|eurl|sci|snc|scop|ltd|limited|gmbh|inc|llc|plc|bv|ag|spa|srl|se)\b')
ANONYMOUS='employeur-non-communique'
DIRECT=('greenhouse','lever','lever_eu','ashby','recruitee','smartrecruiters','generic','workday','teamtailor','successfactors','taleo')

def name_key(name):
    key=re.sub(r'[^a-z0-9]+',' ',norm(name or ''))
    return re.sub(r'\s+',' ',LEGAL.sub(' ',key)).strip()[:200]

def website_host(url):
    try:
        p=urlsplit(url if '://' in (url or '') else 'https://'+(url or ''))
        return p.hostname.lower().removeprefix('www.') if p.hostname and '.' in p.hostname else None
    except ValueError: return None

def company_for(db,offer,source,cache):
    key=name_key(offer.company) or ANONYMOUS
    if key in cache: return cache[key]
    host=website_host(offer.website) if offer.website else None
    company=db.scalar(select(Company).where(Company.name_key==key,Company.is_demo==False).limit(1))
    if not company and host: company=db.scalar(select(Company).where(Company.domain.in_([host,'www.'+host]),Company.is_demo==False).limit(1))
    if not company:
        anonymous=key==ANONYMOUS
        domain=host if host and not db.scalar(select(Company.id).where(Company.domain==host)) else '~'+key
        company=Company(name='Employeur non communiqué' if anonymous else offer.company.strip()[:200],name_key=key,domain=domain[:254],
            website_url='https://'+host if host else '',industry=(offer.industry or 'Autre')[:100],country='France',
            ats_provider='aggregated',crawler_status='aggregated',discovered_via=source,discovery_status='skip' if anonymous else 'pending')
        db.add(company); db.flush(); cache['_new']=cache.get('_new',0)+1
    cache[key]=company
    return company

def ingest(db,source,offers,cache):
    """Attach offers to employers. Employers with a working direct ATS source are left to that source."""
    groups={}
    for offer in offers:
        try: normalize(offer.raw,source)
        except (ValueError,TypeError,KeyError): continue
        company=company_for(db,offer,source,cache)
        if company.ats_provider in DIRECT and company.last_success: continue
        groups.setdefault(company.id,(company,[]))[1].append(offer.raw)
    count=0
    for company,rows in groups.values():
        count+=reconcile(db,company,rows,False,provider=source)
        if company.cities is not None:
            cities=list(dict.fromkeys((company.cities or [])+[r['city'] for r in rows if r.get('city')]))[:20]
            if cities!=company.cities: company.cities=cities
    return count

def expire(db):
    """Aggregated offers are never part of a complete snapshot: close them after the configured TTL."""
    limit=now()-timedelta(days=settings.aggregator_ttl_days)
    stale=select(Job.company_id).where(Job.source.in_(list(SOURCES)),Job.status!='closed',Job.last_seen<limit).distinct()
    ids=list(db.scalars(stale))
    if not ids: return 0
    closed=db.execute(update(Job).where(Job.source.in_(list(SOURCES)),Job.status!='closed',Job.last_seen<limit).values(status='closed')).rowcount
    for company in db.scalars(select(Company).where(Company.id.in_(ids))):
        company.active_jobs=db.scalar(select(func.count()).select_from(Job).where(Job.company_id==company.id,Job.status=='active'))
    return closed

def run_source(db,name,budget=600,http=None):
    source=SOURCES[name]
    last=db.scalar(select(SourceRun).where(SourceRun.source==name,SourceRun.cursor.is_not(None)).order_by(SourceRun.started_at.desc()).limit(1))
    run=SourceRun(source=name,cursor=last.cursor if last else None); db.add(run); db.commit()
    if not source.configured():
        run.status='disabled'; run.finished_at=now(); run.error='Source non configurée'; db.commit(); return run
    http=http or SafeHTTP(db); cache={}; deadline=time.monotonic()+budget; finished=True
    try:
        for offers,cursor in source.fetch(run.cursor,deadline,http):
            run.count+=ingest(db,name,offers,cache); run.cursor=cursor; run.companies=cache.get('_new',0); db.commit()
            if time.monotonic()>deadline: finished=False; break
        run.status='success' if finished else 'partial'
    except Exception as exc:
        db.rollback(); run=db.get(SourceRun,run.id)
        run.status='retry' if isinstance(exc,RetryLater) else 'error'; run.error=(type(exc).__name__+': '+str(exc))[:1000]
        log.warning('source_failure source=%s error=%s',name,run.error)
    run.finished_at=now(); expire(db); db.commit()
    return run

def due(db,name):
    source=SOURCES[name]
    if not source.configured(): return False
    last=db.scalar(select(SourceRun).where(SourceRun.source==name).order_by(SourceRun.started_at.desc()).limit(1))
    if not last: return True
    if last.status=='running' and last.started_at>now()-timedelta(minutes=30): return False
    # A partial run (time budget reached during backfill) resumes at the next cycle.
    return last.status in ('partial','retry') and last.started_at<now()-timedelta(minutes=5) or last.started_at<now()-source.interval

# --- ATS probing ----------------------------------------------------------------------------
SUFFIXES={'france','group','groupe','technologies','technology','tech','labs','io','hq','app','official','sas','international','ai'}
PROBES=[
    ('greenhouse','https://boards-api.greenhouse.io/v1/boards/{s}','https://job-boards.greenhouse.io/{s}'),
    ('lever','https://api.lever.co/v0/postings/{s}?mode=json&limit=5','https://jobs.lever.co/{s}'),
    ('lever_eu','https://api.eu.lever.co/v0/postings/{s}?mode=json&limit=5','https://jobs.eu.lever.co/{s}'),
    ('ashby','https://api.ashbyhq.com/posting-api/job-board/{s}','https://jobs.ashbyhq.com/{s}'),
    ('recruitee','https://{s}.recruitee.com/api/offers/','https://{s}.recruitee.com'),
]

def slugs(name):
    words=name_key(name).split()
    if not words: return []
    out=[''.join(words),'-'.join(words)]
    if len(words)>1 and words[-1] in SUFFIXES: out+=[''.join(words[:-1]),'-'.join(words[:-1])]
    return [s for s in dict.fromkeys(out) if re.fullmatch(r'[a-z0-9][a-z0-9-]{2,59}',s)]

def same_company(key,other):
    other=name_key(other)
    return bool(other) and (other==key or other.replace(' ','')==key.replace(' ','') or len(key)>=5 and (other.startswith(key+' ') or key.startswith(other+' ')))

def mentions(key,texts):
    words=key.split(); needle=' '.join(words)
    hay=' '.join(re.sub(r'[^a-z0-9]+',' ',norm(t or '')) for t in texts)
    return bool(needle) and (' '+needle+' ' in ' '+hay+' ' or needle.replace(' ','') in hay.replace(' ',''))

def verify(provider,body,key):
    """Only accept a board whose public data names the employer, to avoid attaching a homonym's board."""
    data=json.loads(body)
    if provider=='greenhouse': return same_company(key,data.get('name'))
    if provider=='recruitee':
        offers=data.get('offers') or []
        return bool(offers) and any(same_company(key,o.get('company_name')) for o in offers)
    jobs=data if isinstance(data,list) else data.get('jobs') or []
    texts=[j.get('descriptionPlain') or j.get('description') or j.get('descriptionHtml') or '' for j in jobs[:5]]
    texts+=[j.get('additionalPlain') or '' for j in jobs[:5]]
    return bool(jobs) and mentions(key,texts)

def probe(company,http):
    """Return (provider, slug, career_url) or None. RetryLater propagates so the company is retried later."""
    key=company.name_key or name_key(company.name)
    if company.website_url:
        try:
            url,provider,slug=discover(company,http)
            if provider!='generic': return provider,slug,url
        except (Blocked,FetchError) as exc:
            if isinstance(exc,RetryLater): raise
    answered=0
    for s in slugs(company.name):
        for provider,api,board in PROBES:
            try: body=http.get(api.format(s=s))
            except RetryLater: continue
            except FetchError: answered+=1; continue
            answered+=1
            try:
                if verify(provider,body,key): return provider,s,board.format(s=s)
            except (ValueError,TypeError,AttributeError): continue
    # No definitive answer at all (network down, rate limits): keep the company pending.
    if not answered and slugs(company.name): raise RetryLater('Sondage ATS sans réponse ; nouvel essai plus tard')
    return None

def discover_batch(db,limit=None,budget=600,http_factory=None):
    stamp=now(); deadline=time.monotonic()+budget; found=0
    rows=db.scalars(select(Company).where(Company.is_demo==False,Company.ats_provider=='aggregated',
        (Company.discovery_status=='pending')|((Company.discovery_status=='not_found')&(Company.discovery_checked_at<stamp-timedelta(days=30))))
        .order_by(Company.active_jobs.desc(),Company.name).limit(limit or settings.discovery_batch)).all()
    for company in rows:
        if time.monotonic()>deadline: break
        http=http_factory() if http_factory else SafeHTTP(db)
        try: result=probe(company,http)
        except RetryLater: db.rollback(); continue
        company.discovery_checked_at=now()
        owner=result and db.scalar(select(Company).where(Company.ats_provider==result[0],Company.ats_id==result[1],Company.id!=company.id).limit(1))
        if owner:
            # Same board already followed under another name: never crawl it twice.
            company.discovery_status='duplicate'
            log.info('ats_duplicate company=%s owner=%s',company.name,owner.name)
        elif result:
            company.ats_provider,company.ats_id,company.career_url=result
            company.discovery_status='found'; company.crawler_status='idle'; company.enabled=True; found+=1
            log.info('ats_found company=%s provider=%s slug=%s',company.name,result[0],result[1])
        else: company.discovery_status='not_found'
        db.commit()
    return {'checked':len(rows),'found':found}
