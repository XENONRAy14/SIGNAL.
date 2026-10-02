import io, logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI,Depends,HTTPException,Request,Response,UploadFile,File,Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select,func,or_,delete,text
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import get_db
from .models import User,AuthSession,Company,Job,JobSource,CrawlRun,Resume,TailoredResume,SavedJob
from .schemas import Credentials,Profile,CompanyInput,ResumeInput,TailorInput,SavedInput
from .security import current_user,admin,passwords,dummy_hash,new_session,rate_limit,redis
from .matching import match
from .crawling.http import public_url,FetchError
from .resumes import extract_file,tailor,as_docx

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
app=FastAPI(title='SIGNAL API',version='0.1.0',docs_url='/api/docs',openapi_url='/api/openapi.json')

def serialize(obj,exclude=()):
    return {col.name:getattr(obj,col.name) for col in obj.__table__.columns if col.name not in exclude}

def user_public(user): return serialize(user,('password_hash',))

@app.middleware('http')
async def security_headers(request,call_next):
    # Same-origin deployment. CSRF token additionally required for authenticated mutations.
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin!=settings.app_origin:
        return Response('Origin not allowed',status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Cache-Control']='no-store'
    return response

@app.get('/api/health')
def health(db=Depends(get_db)):
    db.execute(text('SELECT 1'))
    try: redis.ping(); queue='ok'
    except Exception: queue='unavailable'
    return {'status':'ok' if queue=='ok' else 'degraded','database':'ok','queue':queue}

@app.post('/api/auth/register',status_code=201)
def register(data:Credentials,request:Request,response:Response,db=Depends(get_db)):
    rate_limit('signup:'+request.client.host,5)
    user=User(email=data.email,name=data.name,password_hash=passwords.hash(data.password))
    db.add(user)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,'Impossible de créer ce compte. Essayez de vous connecter.')
    return new_session(db,user,response)

@app.post('/api/auth/login')
def login(data:Credentials,request:Request,response:Response,db=Depends(get_db)):
    rate_limit('login:'+request.client.host,15)
    rate_limit('login-email:'+data.email,10)
    user=db.scalar(select(User).where(User.email==data.email))
    valid=passwords.verify(data.password,user.password_hash if user else dummy_hash)
    if not user or not valid: raise HTTPException(401,'Email ou mot de passe incorrect')
    return new_session(db,user,response)

@app.get('/api/auth/me')
def me(request:Request,user=Depends(current_user)):
    return {**user_public(user),'csrf':request.state.auth_session.csrf,'ai_available':bool(settings.openai_api_key)}

@app.post('/api/auth/logout')
def logout(request:Request,response:Response,user=Depends(current_user),db=Depends(get_db)):
    db.delete(request.state.auth_session); db.commit(); response.delete_cookie('signal_session',path='/'); return {'ok':True}

@app.put('/api/profile')
def update_profile(data:Profile,user=Depends(current_user),db=Depends(get_db)):
    user.profile=data.model_dump(); db.commit(); return user_public(user)

@app.get('/api/account/export')
def export_account(user=Depends(current_user),db=Depends(get_db)):
    return {'user':user_public(user),'resumes':[serialize(x) for x in db.scalars(select(Resume).where(Resume.user_id==user.id))],
        'tailored_resumes':[serialize(x) for x in db.scalars(select(TailoredResume).where(TailoredResume.user_id==user.id))],
        'saved_jobs':[serialize(x) for x in db.scalars(select(SavedJob).where(SavedJob.user_id==user.id))]}

@app.delete('/api/account')
def delete_account(response:Response,user=Depends(current_user),db=Depends(get_db)):
    for model in (TailoredResume,Resume,SavedJob,AuthSession): db.execute(delete(model).where(model.user_id==user.id))
    db.delete(user); db.commit(); response.delete_cookie('signal_session',path='/'); return {'ok':True}

@app.get('/api/stats')
def stats(user=Depends(current_user),db=Depends(get_db)):
    return {'active_jobs':db.scalar(select(func.count()).select_from(Job).where(Job.status=='active')),
        'real_jobs':db.scalar(select(func.count()).select_from(Job).where(Job.status=='active',Job.is_demo==False)),
        'demo_jobs':db.scalar(select(func.count()).select_from(Job).where(Job.status=='active',Job.is_demo==True)),
        'companies':db.scalar(select(func.count()).select_from(Company)),
        'saved':db.scalar(select(func.count()).select_from(SavedJob).where(SavedJob.user_id==user.id)),
        'domains':db.scalars(select(Job.domain).distinct().order_by(Job.domain)).all()}

@app.get('/api/jobs')
def jobs(q:str=Query('',max_length=200),domain:str='',contract:str='',remote:str='',city:str='',status:str='active',
        demo:str='all',sort:str='match',page:int=Query(1,ge=1),limit:int=Query(24,ge=1,le=100),
        user=Depends(current_user),db=Depends(get_db)):
    query=select(Job).join(Company)
    if q:
        escaped=q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        query=query.where(or_(Job.title.ilike('%'+escaped+'%',escape='\\'),Job.description.ilike('%'+escaped+'%',escape='\\'),Company.name.ilike('%'+escaped+'%',escape='\\')))
    for value,col in [(domain,Job.domain),(contract,Job.contract_type),(remote,Job.remote_type),(status,Job.status)]:
        if value: query=query.where(col==value)
    if city: query=query.where(Job.location.ilike('%'+city[:200]+'%'))
    if demo in ('real','demo'): query=query.where(Job.is_demo==(demo=='demo'))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    saved={x.job_id:x.stage for x in db.scalars(select(SavedJob).where(SavedJob.user_id==user.id))}
    companies={c.id:c for c in db.scalars(select(Company).where(Company.id.in_(select(Job.company_id).where(Job.id.in_(query.with_only_columns(Job.id))))))}
    def item(job): return {**serialize(job,('description','content_hash','dedupe_key','requirements','responsibilities','nice_to_have')),'company':companies[job.company_id].name,'match':match(user.profile,job),'saved_stage':saved.get(job.id)}
    query=query.order_by(Job.first_seen.desc(),Job.id)
    if sort=='match':
        # Explicit bounded candidate window. This avoids unbounded per-request Python ranking.
        candidates=[item(j) for j in db.scalars(query.limit(2000))]
        candidates.sort(key=lambda x:(x['match']['score'] if x['match']['score'] is not None else -1),reverse=True)
        items=candidates[(page-1)*limit:page*limit]; ranked_total=len(candidates)
    else:
        items=[item(j) for j in db.scalars(query.offset((page-1)*limit).limit(limit))]; ranked_total=total
    return {'items':items,'total':total,'ranked_total':ranked_total,'page':page,'limit':limit,'ranking_window':2000 if sort=='match' else None}

@app.get('/api/jobs/{job_id}')
def job_detail(job_id:str,user=Depends(current_user),db=Depends(get_db)):
    job=db.get(Job,job_id)
    if not job: raise HTTPException(404,'Offre introuvable')
    return {**serialize(job),'company':serialize(db.get(Company,job.company_id)),'match':match(user.profile,job),
        'sources':[serialize(s) for s in db.scalars(select(JobSource).where(JobSource.job_id==job.id))]}

@app.get('/api/saved')
def saved(user=Depends(current_user),db=Depends(get_db)):
    rows=db.execute(select(SavedJob,Job,Company).join(Job,SavedJob.job_id==Job.id).join(Company,Job.company_id==Company.id).where(SavedJob.user_id==user.id).order_by(SavedJob.created_at.desc())).all()
    return [{**serialize(s),'job':serialize(j),'company':c.name,'match':match(user.profile,j)} for s,j,c in rows]

@app.put('/api/saved/{job_id}')
def save(job_id:str,data:SavedInput,user=Depends(current_user),db=Depends(get_db)):
    if not db.get(Job,job_id): raise HTTPException(404,'Offre introuvable')
    row=db.scalar(select(SavedJob).where(SavedJob.user_id==user.id,SavedJob.job_id==job_id))
    if not row: row=SavedJob(user_id=user.id,job_id=job_id); db.add(row)
    row.stage=data.stage; row.note=data.note
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,'Modification concurrente, réessayez')
    return serialize(row)

@app.delete('/api/saved/{job_id}')
def unsave(job_id:str,user=Depends(current_user),db=Depends(get_db)):
    db.execute(delete(SavedJob).where(SavedJob.user_id==user.id,SavedJob.job_id==job_id)); db.commit(); return {'ok':True}

@app.get('/api/companies')
def companies(q:str='',page:int=Query(1,ge=1),limit:int=Query(30,ge=1,le=100),user=Depends(current_user),db=Depends(get_db)):
    query=select(Company).where(Company.name.ilike('%'+q[:200]+'%'))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return {'items':[serialize(c) for c in db.scalars(query.order_by(Company.active_jobs.desc(),Company.name).offset((page-1)*limit).limit(limit))],'total':total}

@app.post('/api/admin/companies',status_code=201)
def add_company(data:CompanyInput,user=Depends(admin),db=Depends(get_db)):
    try:
        p,_=public_url(data.website_url)
        if data.career_url: public_url(data.career_url)
    except FetchError as exc: raise HTTPException(400,str(exc))
    company=Company(**data.model_dump(),domain=p.hostname.lower())
    db.add(company)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,'Cette entreprise est déjà suivie')
    return serialize(company)

@app.post('/api/admin/companies/{company_id}/crawl',status_code=202)
def crawl(company_id:str,user=Depends(admin),db=Depends(get_db)):
    company=db.get(Company,company_id)
    if not company or company.is_demo: raise HTTPException(400,'Choisissez une entreprise réelle')
    rate_limit('crawl-admin:'+user.id,20)
    from .tasks import enqueue
    try: run=enqueue(db,company)
    except Exception: raise HTTPException(503,'Queue indisponible. Vérifiez Redis et le worker.')
    return serialize(run)

@app.patch('/api/admin/companies/{company_id}/enabled')
def toggle(company_id:str,enabled:bool,user=Depends(admin),db=Depends(get_db)):
    company=db.get(Company,company_id)
    if not company or company.is_demo: raise HTTPException(404,'Entreprise réelle introuvable')
    company.enabled=enabled; db.commit(); return serialize(company)

@app.get('/api/admin/crawls')
def crawls(user=Depends(admin),db=Depends(get_db)):
    rows=db.execute(select(CrawlRun,Company.name).join(Company).order_by(CrawlRun.started_at.desc()).limit(100))
    return [{**serialize(run),'company':name} for run,name in rows]

@app.get('/api/connectors')
def connectors(user=Depends(current_user)):
    return [{'name':n,'mode':'API publique' if n in ('greenhouse','lever','lever_eu','ashby','recruitee','smartrecruiters') else 'JSON-LD uniquement','closure_supported':n in ('greenhouse','lever','lever_eu','ashby','recruitee','smartrecruiters')} for n in ['greenhouse','lever','lever_eu','ashby','recruitee','smartrecruiters','generic','workday','teamtailor','successfactors','taleo']]

@app.get('/api/resumes')
def resumes(user=Depends(current_user),db=Depends(get_db)):
    return [serialize(r) for r in db.scalars(select(Resume).where(Resume.user_id==user.id).order_by(Resume.created_at.desc()))]

@app.post('/api/resumes',status_code=201)
def create_resume(data:ResumeInput,user=Depends(current_user),db=Depends(get_db)):
    rate_limit('resume:'+user.id,20)
    resume=Resume(user_id=user.id,**data.model_dump()); db.add(resume); db.commit(); return serialize(resume)

@app.post('/api/resumes/upload',status_code=201)
def upload(file:UploadFile=File(...),user=Depends(current_user),db=Depends(get_db)):
    rate_limit('resume:'+user.id,20)
    data=file.file.read(5*1024*1024+1)
    text_value=extract_file(file.filename or '',data)
    resume=Resume(user_id=user.id,name=(file.filename or 'CV')[:200],text=text_value); db.add(resume); db.commit(); return serialize(resume)

@app.delete('/api/resumes/{resume_id}')
def delete_resume(resume_id:str,user=Depends(current_user),db=Depends(get_db)):
    row=db.get(Resume,resume_id)
    if not row or row.user_id!=user.id: raise HTTPException(404,'CV introuvable')
    db.execute(delete(TailoredResume).where(TailoredResume.resume_id==row.id)); db.delete(row); db.commit(); return {'ok':True}

@app.post('/api/resumes/tailor',status_code=201)
def tailor_resume(data:TailorInput,user=Depends(current_user),db=Depends(get_db)):
    rate_limit('tailor:'+user.id,10)
    resume=db.get(Resume,data.resume_id); job=db.get(Job,data.job_id)
    if not resume or resume.user_id!=user.id or not job: raise HTTPException(404,'CV ou offre introuvable')
    text_value,explanation=tailor(resume.text,job,data.use_ai)
    result=TailoredResume(user_id=user.id,resume_id=resume.id,job_id=job.id,text=text_value,explanation=explanation)
    db.add(result); db.commit(); return serialize(result)

@app.get('/api/tailored')
def tailored(user=Depends(current_user),db=Depends(get_db)):
    return [serialize(r) for r in db.scalars(select(TailoredResume).where(TailoredResume.user_id==user.id).order_by(TailoredResume.created_at.desc()).limit(100))]

@app.get('/api/tailored/{resume_id}/download')
def download(resume_id:str,format:str='docx',user=Depends(current_user),db=Depends(get_db)):
    row=db.get(TailoredResume,resume_id)
    if not row or row.user_id!=user.id: raise HTTPException(404,'CV introuvable')
    if format=='docx': content=as_docx(row.text); mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document'; ext='docx'
    else: content=row.text.encode(); mime='text/plain; charset=utf-8'; ext='txt'
    return StreamingResponse(io.BytesIO(content),media_type=mime,headers={'Content-Disposition':f'attachment; filename="signal-cv-{row.id[:8]}.{ext}"'})
