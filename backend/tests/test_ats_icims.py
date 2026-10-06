import httpx
import pytest
from backend.scraper.ats import icims

URL = 'https://example.icims.com/jobs/search?searchCategory=123&in_iframe=1'

def page(n=1,total=1,ident=1):
    return f'<h2 class="iCIMS_SubHeader_Jobs">Search Results Page {n} of {total}</h2><a href="https://example.icims.com/jobs/{ident}/engineer/job?in_iframe=1"><h3>Cloud Engineer</h3></a>'

@pytest.mark.parametrize('url', ['https://attacker.com/?url=example.icims.com/jobs/search', 'https://example.icims.com.attacker.com/jobs/search', 'http://example.icims.com/jobs/search', 'https://user@example.icims.com/jobs/search'])
def test_detection_rejects_untrusted_origins(url):
    assert not icims.is_icims(url)

def test_challenge_and_unknown_empty_are_errors():
    for html in ['Let\'s confirm you are human', '<html>Loading...</html>', '<h2 class="iCIMS_SubHeader_Jobs">Page 1 of 1</h2>']:
        with pytest.raises(ValueError): icims.parse_page(html, URL)

def test_explicit_empty_only():
    assert icims.parse_page('<div class="iCIMS_NoJobs">No jobs found</div>', URL)==([],1,1)

def test_job_links_are_canonical():
    jobs,current,total=icims.parse_page(page(),URL)
    assert jobs == [{'title':'Cloud Engineer','url':'https://example.icims.com/jobs/1/engineer/job'}]
    assert (current,total)==(1,1)

async def run_mock(monkeypatch, handler, max_pages=5):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return await icims.scrape(URL, debug=True, max_pages=max_pages)

@pytest.mark.asyncio
async def test_pagination_preserves_filter_and_completes(monkeypatch):
    requests=[]
    def handler(req):
        requests.append(req)
        assert req.url.params['searchCategory']=='123'
        n=int(req.url.params['pr'])+1
        return httpx.Response(200,text=page(n,2,n))
    jobs,rejected,coverage=await run_mock(monkeypatch,handler)
    assert len(jobs)==2 and not rejected and coverage['complete'] and coverage['pages']==2
    assert len(requests)==2

@pytest.mark.asyncio
@pytest.mark.parametrize('case',['limit','repeat','drift','http','redirect','challenge'])
async def test_failures_never_return_partial_success(monkeypatch,case):
    def handler(req):
        n=int(req.url.params['pr'])+1
        if case=='http':return httpx.Response(503)
        if case=='redirect':return httpx.Response(302,headers={'location':'https://attacker.com/'})
        if case=='challenge':return httpx.Response(200,text='Confirm you are human')
        if case=='limit':return httpx.Response(200,text=page(1,6,1))
        if case=='repeat':return httpx.Response(200,text=page(n,2,1))
        return httpx.Response(200,text=page(n,2 if n==1 else 3,n))
    with pytest.raises(ValueError):await run_mock(monkeypatch,handler)
