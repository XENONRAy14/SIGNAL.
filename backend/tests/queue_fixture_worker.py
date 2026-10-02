"""TEST-ONLY worker transport fixture. Never used by Docker or production commands."""
import json
from app.crawling.http import SafeHTTP,FetchError
from app.tasks import celery

def fixture_get(self,url):
    if url!='https://boards-api.greenhouse.io/v1/boards/signal-fixture/jobs?content=true':
        raise FetchError('Test worker only accepts its fixed fixture URL')
    return json.dumps({'jobs':[{'id':'fixture-1','title':'Data Engineer junior','absolute_url':'https://example.com/jobs/fixture-1','content':'Python SQL Docker. Test fixture, not a real opening.','location':{'name':'Paris'}}]})
SafeHTTP.get=fixture_get
