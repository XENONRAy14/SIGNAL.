import math, re, unicodedata

def norm(value):
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFKD', value or '').lower() if not unicodedata.combining(c))).strip()

def distance_km(a,b,c,d):
    a,b,c,d = map(math.radians, (a,b,c,d))
    h = math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 6371*2*math.asin(min(1, math.sqrt(h)))

def match(profile, job):
    parts, warnings = [], []
    def part(label, weight, value): parts.append({'label':label,'weight':weight,'value':round(value,3)})
    wanted = {norm(x) for x in profile.get('skills',[])+profile.get('technologies',[])}
    available = {norm(x) for x in (job.skills or [])+(job.technologies or [])}
    common = sorted(wanted & available)
    if wanted: part('Compétences', 45, len(common)/len(wanted))
    if profile.get('domains'): part('Domaine',15, float(norm(job.domain) in map(norm,profile['domains'])))
    for key,attr,label,weight in [('contracts','contract_type','Contrat',15),('remote_types','remote_type','Mode de travail',10)]:
        if profile.get(key):
            val=getattr(job,attr)
            part(label,weight,0.5 if val=='unknown' else float(val in profile[key]))
            if val=='unknown': warnings.append(label+' non renseigné')
    dist=None
    if profile.get('city'):
        coords=[profile.get('latitude'),profile.get('longitude'),job.latitude,job.longitude]
        if job.remote_type=='remote': value=1
        elif all(v is not None for v in coords):
            dist=round(distance_km(*coords),1); value=float(dist<=profile.get('radius_km',50))
        elif job.city: value=float(norm(job.city)==norm(profile['city'])); warnings.append('Rayon non calculé : coordonnées indisponibles')
        else: value=.5; warnings.append('Localisation non renseignée')
        part('Localisation',10,value)
    if profile.get('seniority'):
        part('Expérience',5, .5 if not job.seniority else float(norm(job.seniority)==norm(profile['seniority'])))
    total=sum(x['weight'] for x in parts)
    return {'score':round(100*sum(x['weight']*x['value'] for x in parts)/total) if total else None,
        'breakdown':parts,'matched_skills':common,'missing_skills':sorted(available-wanted),
        'warnings':warnings,'distance_km':dist,'method':'weighted-rules-v1'}
