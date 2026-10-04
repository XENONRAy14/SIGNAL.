"""Hard profile criteria applied to the radar in SQL: contracts, distance and work mode. Scoring stays in matching.py."""
import math
from sqlalchemy import and_, or_
from .geo import locate
from .models import Job

ALL_MODES={'remote','hybrid','onsite'}
REMOTE_ZONES=('%france%','%europe%','%emea%','%worldwide%','%anywhere%')

def with_coords(profile):
    """Profile with coordinates: explicit values first, otherwise the offline gazetteer for its city."""
    profile=dict(profile or {})
    if profile.get('city') and (profile.get('latitude') is None or profile.get('longitude') is None):
        _,lat,lon,_=locate(profile['city'])
        if lat is not None: profile['latitude'],profile['longitude']=lat,lon
    return profile

def criteria(profile):
    p=with_coords(profile)
    contracts=list(p.get('contracts') or [])
    modes=set(p.get('remote_types') or [])
    radius=p.get('radius_km') or 50
    geo=p.get('latitude') is not None and p.get('longitude') is not None
    return {'contracts':contracts,'city':p.get('city') or None,'radius_km':radius if geo and radius<1000 else None,
        'france_only':bool(p.get('city') or (p.get('country') or '').lower()=='france'),'latitude':p.get('latitude'),'longitude':p.get('longitude'),
        'remote_types':sorted(modes) if modes and modes!=ALL_MODES else [],'remote_anywhere':not modes or 'remote' in modes,'located':geo}

def apply(query,profile):
    c=criteria(profile); active=[]
    if c['contracts']:
        query=query.where(Job.contract_type.in_(c['contracts'])); active.append('contracts')
    remote_ok=and_(Job.remote_type=='remote',or_(Job.country=='France',*[Job.location.ilike(z) for z in REMOTE_ZONES])) if c['remote_anywhere'] else None
    if c['radius_km']:
        lat0,lon0,r=c['latitude'],c['longitude'],c['radius_km']
        # Equirectangular distance: plain arithmetic, portable across PostgreSQL/SQLite, < 1 % error at this scale.
        k=math.cos(math.radians(lat0)); dlat=Job.latitude-lat0; dlon=(Job.longitude-lon0)*k
        within=and_(Job.latitude.is_not(None),Job.longitude.is_not(None),(dlat*dlat+dlon*dlon)*(111.32**2)<=r*r)
        query=query.where(or_(within,remote_ok) if remote_ok is not None else within); active.append('distance')
    elif c['france_only']:
        query=query.where(or_(Job.country=='France',remote_ok) if remote_ok is not None else Job.country=='France'); active.append('france')
    if c['remote_types']:
        query=query.where(or_(Job.remote_type.in_(c['remote_types']),Job.remote_type=='unknown')); active.append('remote_types')
    return query,{**c,'active':active}
