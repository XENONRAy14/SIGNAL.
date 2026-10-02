"""Bounded public-only HTTP, DNS pinning, robots.txt and distributed domain pacing."""
import http.client, ipaddress, logging, re, socket, ssl, time
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit, urljoin
from redis import Redis
from ..config import settings
from ..models import HttpCache, RobotsCache, now

AGENT='SignalBot/0.1'
TOKEN='signalbot'
MAX_BYTES=8*1024*1024
ROBOTS_TTL=timedelta(hours=24)
ROBOTS_STALE=timedelta(days=30)
ROBOTS_RETRY=timedelta(minutes=15)
ALLOW,BLOCK,RETRY,UNKNOWN='allow','block','retry','unknown'
log=logging.getLogger('signal.robots')

class FetchError(Exception): pass
class Blocked(FetchError): pass
class RetryLater(FetchError): pass

@dataclass
class Robots:
    """RFC 9309 rules: longest matching path wins, Allow wins ties, /robots.txt always allowed."""
    rules: list = field(default_factory=list)
    delay: float | None = None
    state: str = 'parsed'

    @classmethod
    def parse(cls,body,state='parsed'):
        groups=[]; agents=[]; rules=[]; delay=None; in_rules=False
        for raw in body.splitlines():
            line=raw.split('#',1)[0].strip()
            if ':' not in line: continue
            key,value=(x.strip() for x in line.split(':',1)); key=key.lower()
            if key=='user-agent':
                if in_rules: groups.append((agents,rules,delay)); agents,rules,delay=[],[],None
                agents.append(value.lower()); in_rules=False
            elif key in ('allow','disallow') and agents:
                if value: rules.append((key=='allow',value))
                in_rules=True
            elif key=='crawl-delay' and agents:
                try: delay=float(value)
                except ValueError: pass
                in_rules=True
        if agents: groups.append((agents,rules,delay))
        mine=[g for g in groups if any(a.split('/')[0]==TOKEN for a in g[0])] or [g for g in groups if '*' in g[0]]
        return cls([r for g in mine for r in g[1]],next((g[2] for g in mine if g[2] is not None),None),state)

    def allows(self,url):
        p=urlsplit(url); path=(p.path or '/')+('?'+p.query if p.query else '')
        if p.path=='/robots.txt': return True
        best=None
        for allow,pattern in self.rules:
            regex='^'+re.escape(pattern).replace(r'\*','.*')
            if regex.endswith(r'\$'): regex=regex[:-2]+'$'
            if re.match(regex,path) and (best is None or len(pattern)>len(best[1]) or len(pattern)==len(best[1]) and allow):
                best=(allow,pattern)
        return best is None or best[0]

def robots_state(status,body):
    """Map the robots.txt fetch result to an RFC 9309 state, with production caution for 401/403/429."""
    if status==200: return UNKNOWN if '<html' in body[:2000].lower() else 'parsed'
    if status==429 or status>=500: return RETRY
    if status in (401,403): return 'restricted'
    if 400<=status<500: return 'absent'
    return RETRY

def public_url(url):
    try:
        p=urlsplit(url)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):
            raise Blocked('URL publique HTTP(S) attendue')
        if len(url)>2000: raise Blocked('URL trop longue')
        addresses={x[4][0] for x in socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise Blocked('Destination privée ou réservée interdite')
        return p, sorted(addresses)[0]
    except (ValueError,OSError) as exc: raise FetchError('URL ou DNS invalide') from exc

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip,port): super().__init__(host,port,timeout=15,context=ssl.create_default_context()); self.ip=ip
    def connect(self):
        raw=socket.create_connection((self.ip,self.port),self.timeout)
        self.sock=self._context.wrap_socket(raw,server_hostname=self.host)

class SafeHTTP:
    def __init__(self,db):
        self.db=db; self.robots={}; self.pages=0
        self.redis=Redis.from_url(settings.redis_url,socket_connect_timeout=2,socket_timeout=2)

    def pace(self,domain,delay=2):
        # Redis lock and next-allowed timestamp enforce pacing across all workers.
        with self.redis.lock('crawl:pace-lock:'+domain,timeout=60,blocking_timeout=65):
            key='crawl:next:'+domain
            wait=max(0,float(self.redis.get(key) or 0)-time.time())
            if wait: time.sleep(min(wait,60))
            self.redis.set(key,time.time()+max(2,min(delay,60)),ex=120)

    def _request(self,url,headers=None,robots_check=False,depth=0):
        if depth>5: raise FetchError('Trop de redirections')
        p,ip=public_url(url)
        if robots_check: self.check_robots(url)
        rule=self.robots.get(p.scheme+'://'+p.netloc)
        delay=rule.delay if rule else None
        if delay and delay>60: raise Blocked('Crawl-delay supérieur au budget du worker ; collecte suspendue')
        self.pace(p.hostname,delay or 2)
        conn=PinnedHTTPS(p.hostname,ip,p.port or 443) if p.scheme=='https' else http.client.HTTPConnection(ip,p.port or 80,timeout=15)
        try:
            conn.request('GET',(p.path or '/')+('?' + p.query if p.query else ''),headers={'Host':p.netloc,'User-Agent':AGENT,'Accept-Encoding':'identity',**(headers or {})})
            response=conn.getresponse(); status=response.status; h=dict((k.lower(),v) for k,v in response.getheaders())
            if status in (301,302,303,307,308):
                if not h.get('location'): raise FetchError('Redirection sans destination')
                return self._request(urljoin(url,h['location']),headers,robots_check,depth+1)
            if h.get('content-encoding','identity')!='identity': raise FetchError('Compression inattendue')
            body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES: raise FetchError('Page trop volumineuse')
            return status,h,body.decode('utf-8',errors='replace')
        except (OSError,http.client.HTTPException) as exc: raise FetchError('Échec réseau : '+type(exc).__name__) from exc
        finally: conn.close()

    def load_robots(self,origin):
        cached=self.db.get(RobotsCache,origin); stamp=now()
        if cached and cached.expires_at>stamp:
            if cached.state==RETRY: raise RetryLater('robots.txt temporairement inaccessible ; nouvel essai plus tard')
            return Robots.parse(cached.body if cached.state=='parsed' else '',cached.state)
        try: status,_,body=self._request(origin+'/robots.txt')
        except Blocked: raise
        except FetchError: status,body=None,''
        state=robots_state(status,body) if status else RETRY
        if state==RETRY:
            # RFC 9309: an unreachable robots.txt means full disallow, except a recent cached copy may be reused.
            if cached and cached.state!=RETRY and stamp-cached.fetched_at<ROBOTS_STALE:
                cached.expires_at=stamp+ROBOTS_RETRY; self.db.flush()
                return Robots.parse(cached.body if cached.state=='parsed' else '',cached.state)
            if not cached: cached=RobotsCache(origin=origin); self.db.add(cached)
            cached.state=RETRY; cached.status_code=status; cached.body=''; cached.fetched_at=stamp; cached.expires_at=stamp+ROBOTS_RETRY; self.db.flush()
            raise RetryLater(f'robots.txt inaccessible ({status or "réseau"}) ; nouvel essai plus tard')
        if state==UNKNOWN: log.info('robots_unparsable origin=%s status=%s',origin,status)
        if not cached: cached=RobotsCache(origin=origin); self.db.add(cached)
        cached.state=state; cached.status_code=status; cached.body=body[:500000] if state=='parsed' else ''; cached.fetched_at=stamp; cached.expires_at=stamp+ROBOTS_TTL
        self.db.flush()
        return Robots.parse(cached.body,state)

    def decide(self,url):
        """Crawl decision for a URL: ALLOW, BLOCK, RETRY or UNKNOWN (unparsable file, treated as public access)."""
        p=urlsplit(url); origin=p.scheme+'://'+p.netloc
        if origin not in self.robots:
            try: self.robots[origin]=self.load_robots(origin)
            except RetryLater: return RETRY
        rule=self.robots[origin]
        if not rule.allows(url): return BLOCK
        return UNKNOWN if rule.state==UNKNOWN else ALLOW

    def check_robots(self,url):
        decision=self.decide(url)
        if decision==BLOCK: raise Blocked('Collecte interdite par robots.txt')
        if decision==RETRY: raise RetryLater('robots.txt temporairement inaccessible ; nouvel essai plus tard')

    def get(self,url):
        self.pages+=1
        if self.pages>100: raise FetchError('Budget de 100 pages atteint')
        cached=self.db.get(HttpCache,url)
        headers={}
        if cached:
            if cached.etag: headers['If-None-Match']=cached.etag
            if cached.modified: headers['If-Modified-Since']=cached.modified
        status,h,body=self._request(url,headers,True)
        if status==304 and cached: return cached.body
        if status==429 or status>=500: raise RetryLater('HTTP '+str(status)+' ; nouvel essai plus tard')
        if status!=200: raise FetchError('HTTP '+str(status))
        if any(x in body.lower() for x in ('cf-chl-','g-recaptcha','hcaptcha-response','verify you are human')):
            raise Blocked('Protection technique détectée ; aucune tentative de contournement')
        if not cached: cached=HttpCache(url=url,body=body); self.db.add(cached)
        cached.body=body; cached.etag=h.get('etag'); cached.modified=h.get('last-modified'); cached.updated=now()
        self.db.flush()
        return body
