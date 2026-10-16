import asyncio
import httpx
from app.news import NewsFeed, parse_feed, SOURCE_URL, MAX_BYTES
import pytest

XML=b'''<rss><channel><item><title>Official release</title><link>https://www.federalreserve.gov/newsevents/pressreleases/test.htm</link><pubDate>Tue, 29 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>'''


def test_parse_and_block_unsafe_links():
    assert len(parse_feed(XML))==1
    assert parse_feed(XML.replace(b'https://www.federalreserve.gov',b'https://evil.example'))==[]
    assert parse_feed(XML.replace(b'https://www.federalreserve.gov',b'javascript:'))==[]
    for raw in (b'<!DOCTYPE rss>'+XML,b'<!ENTITY x "x">'+XML,b'x'*(MAX_BYTES+1),b'<html/>',b'\x00'+XML):
        with pytest.raises(ValueError):parse_feed(raw)


def test_cache_concurrency_failure_and_recovery():
    async def run():
        now=[1000];calls=[];failed=[False]
        async def upstream(request):
            calls.append(str(request.url))
            return httpx.Response(503 if failed[0] else 200,content=XML)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            feed=NewsFeed(client,lambda:now[0])
            results=await asyncio.gather(*(feed.get() for _ in range(10)))
            assert len(calls)==1 and calls[0]==SOURCE_URL
            assert all(x['status']=='live' for x in results)
            failed[0]=True;now[0]+=301
            stale=await feed.get()
            assert stale['status']=='stale' and stale['updated_at']==1000 and stale['items']
            await feed.get();assert len(calls)==2
            failed[0]=False;now[0]+=61
            assert (await feed.get())['status']=='live'
    asyncio.run(run())


def test_redirect_and_first_failure():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1'}))) as client:
            result=await NewsFeed(client).get()
            assert result['status']=='unavailable' and result['items']==[]
    asyncio.run(run())
