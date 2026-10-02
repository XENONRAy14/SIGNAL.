import hashlib, secrets, time
from collections import defaultdict
from datetime import timedelta
from fastapi import Request, Depends, HTTPException
from sqlalchemy import select
from pwdlib import PasswordHash
from redis import Redis
from .db import get_db
from .models import User, AuthSession, now
from .config import settings

passwords=PasswordHash.recommended()
dummy_hash=passwords.hash('constant-dummy-password')
redis=Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
_local=defaultdict(list)

def digest(token): return hashlib.sha256(token.encode()).hexdigest()

def rate_limit(key, limit=10, window=60):
    try:
        bucket='rate:'+key+':'+str(int(time.time())//window)
        with redis.pipeline() as pipe:
            count,_=pipe.incr(bucket).expire(bucket,window+1).execute()
        if count>limit: raise HTTPException(429,'Trop de tentatives. Réessayez dans une minute.')
    except HTTPException: raise
    except Exception:
        if settings.environment=='production': raise HTTPException(503,'Service de sécurité indisponible')
        t=time.time(); _local[key]=[x for x in _local[key] if x>t-window]
        if len(_local[key])>=limit: raise HTTPException(429,'Trop de tentatives')
        _local[key].append(t)

def current_user(request: Request, db=Depends(get_db)):
    token=request.cookies.get('signal_session','')
    session=db.get(AuthSession,digest(token)) if token else None
    if not session or session.expires<now(): raise HTTPException(401,'Connectez-vous pour continuer')
    user=db.get(User,session.user_id)
    if not user: raise HTTPException(401,'Session invalide')
    if request.method not in ('GET','HEAD','OPTIONS'):
        if not secrets.compare_digest(request.headers.get('x-csrf-token',''),session.csrf):
            raise HTTPException(403,'Jeton CSRF invalide')
    request.state.auth_session=session
    return user

def admin(user=Depends(current_user)):
    if not user.is_admin: raise HTTPException(403,'Accès administrateur requis')
    return user

def new_session(db,user,response):
    token=secrets.token_urlsafe(48); csrf=secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=digest(token),user_id=user.id,csrf=csrf,expires=now()+timedelta(days=7)))
    db.commit()
    response.set_cookie('signal_session',token,max_age=604800,httponly=True,secure=settings.secure_cookies,samesite='lax',path='/')
    return {'id':user.id,'email':user.email,'name':user.name,'is_admin':user.is_admin,'profile':user.profile,'csrf':csrf}
