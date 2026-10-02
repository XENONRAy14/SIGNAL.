"""Bounded public-only HTTP, DNS pinning, robots.txt and distributed domain pacing."""
import http.client, ipaddress, socket, ssl, time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
from redis import Redis
from ..config import settings
from ..models import HttpCache, now

AGENT='SignalBot/0.1'
MAX_BYTES=8*1024*1024

class FetchError(Exception): pass
class Blocked(FetchError): pass

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
        if depth>4: raise FetchError('Trop de redirections')
        p,ip=public_url(url)
        if robots_check: self.check_robots(url)
        rule=self.robots.get(p.scheme+'://'+p.netloc)
        delay=rule.crawl_delay(AGENT) if rule else None
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

    def check_robots(self,url):
        p=urlsplit(url); origin=p.scheme+'://'+p.netloc
        if origin not in self.robots:
            status,_,body=self._request(origin+'/robots.txt')
            rule=RobotFileParser(); rule.set_url(origin+'/robots.txt')
            if status==404: rule.parse(['User-agent: *','Allow: /'])
            elif status==200: rule.parse(body.splitlines())
            else: raise Blocked('robots.txt indisponible ; collecte suspendue')
            self.robots[origin]=rule
        if not self.robots[origin].can_fetch(AGENT,url): raise Blocked('Collecte interdite par robots.txt')

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
        if status!=200: raise FetchError('HTTP '+str(status))
        if any(x in body.lower() for x in ('cf-chl-','g-recaptcha','hcaptcha-response','verify you are human')):
            raise Blocked('Protection technique détectée ; aucune tentative de contournement')
        if not cached: cached=HttpCache(url=url,body=body); self.db.add(cached)
        cached.body=body; cached.etag=h.get('etag'); cached.modified=h.get('last-modified'); cached.updated=now()
        self.db.flush()
        return body
