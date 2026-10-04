"""Offline geocoding of French locations (communes ≥ 1 000 inhabitants, geo.api.gouv.fr, Licence Ouverte)."""
import csv, math, re
from functools import lru_cache
from pathlib import Path
from .matching import norm

DATA=Path(__file__).parent/'data'/'communes.csv'
FRANCE={'france','fr','fra','french republic','republique francaise'}
FRANCE_HINTS=re.compile(r'\b(france|ile de france|provence|occitanie|bretagne|normandie|auvergne|nouvelle aquitaine|hauts de france|grand est|bourgogne|pays de la loire|centre val de loire|corse)\b')
REMOTE_FRANCE=re.compile(r'\b(france|europe|emea|eu|worldwide|anywhere|monde)\b')
# Explicit foreign markers prevent homonyms such as "St. Louis, MO" or "Montreal, Canada" from landing in France.
FOREIGN=set('''us usa united states america uk united kingdom england scotland wales canada quebec ontario germany deutschland spain espana italy italia
portugal netherlands holland belgium belgique belgie switzerland suisse schweiz ireland india brazil brasil mexico australia singapore japan china
hong kong taiwan poland romania austria sweden denmark norway finland israel uae united arab emirates dubai south africa argentina colombia chile
turkey greece czech republic czechia hungary luxembourg morocco maroc tunisia tunisie algeria algerie egypt nigeria kenya korea south korea philippines
indonesia vietnam thailand malaysia new zealand ukraine estonia latvia lithuania croatia bulgaria serbia slovakia slovenia peru latam apac amer americas
ny ca tx ma wa il co ga fl nc va pa oh mi mn az or ut nj md ct dc bc qc on mo ks ky tn in wi ia ok ar la ms al sc nv nm id mt wy nd sd ne ak hi me nh vt ri de wv'''.replace('\n',' ').split(' '))|{'united states','united kingdom','new york',
'san francisco','los angeles','south africa','new zealand','hong kong','czech republic','united arab emirates','south korea','north america','latin america','st louis mo'}
ALIASES={'paris la defense':'puteaux','la defense':'puteaux','sophia antipolis':'valbonne','marseilles':'marseille','lyons':'lyon'}

def key(value):
    k=re.sub(r'[^a-z0-9]+',' ',norm(value)).strip()
    k=re.sub(r'^saint ','st ',k); return re.sub(r' saint ',' st ',k)

@lru_cache(maxsize=1)
def communes():
    places={}
    with open(DATA,encoding='utf-8',newline='') as f:
        for row in csv.DictReader(f):  # sorted by population: the largest homonym wins
            places.setdefault(key(row['nom']),(row['nom'],float(row['lat']),float(row['lon'])))
    return places

def clean(token):
    t=key(token)
    t=re.sub(r'^\d{2,3}\s+','',t)                      # "13 - Marseille" (France Travail)
    t=re.sub(r'\s+\d{1,2}(e|er|eme)?(\s+arrondissement)?$','',t)  # "Paris 15e", "Marseille 1er Arrondissement"
    t=re.sub(r'\s+(cedex|area|region|office|hq|metropolitan area)$','',t)
    return ALIASES.get(t,t)

def locate(*texts):
    """Return (city, lat, lon, in_france) for free-text locations like 'Paris, France' or 'Remote - US'."""
    places=communes(); france=False
    for text in texts:
        if not text: continue
        keys=[clean(t) for t in re.split(r'[,;/|()\n]|\s+-\s+|\s+–\s+|\s+·\s+',str(text)) if t.strip()]
        text_france=any(k in FRANCE or FRANCE_HINTS.search(k) for k in keys)
        france=france or text_france
        if not text_france and any(k in FOREIGN or k.split(' ')[-1] in FOREIGN and len(k.split(' '))>1 and k not in places for k in keys): continue
        for k in keys:
            if len(k)>2 and k in places:
                name,lat,lon=places[k]; return name,lat,lon,True
    return None,None,None,france

def is_france(country):
    return bool(country) and key(country) in FRANCE

def distance_km(a,b,c,d):
    a,b,c,d=map(math.radians,(a,b,c,d))
    h=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(h)))
