import asyncio
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.market import Candle
from app.http_limits import BodyLimitMiddleware
from tests.test_chart import Source
from tests.test_system import Feed,TG

def test_chart_percentage_matches_visible_window(tmp_path):
    class Trend(Source):
        async def candles(self,asset,interval,count):
            import time
            now=int(time.time()//interval)*interval
            return [Candle(now-(99-i)*interval,100+i,102+i,99+i,101+i) for i in range(100)]
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Trend(),TG())) as client:
        data=client.get('/api/chart?asset=EUR/USD&timeframe=M1').json()
        assert len(data['candles'])==70
        assert data['candles'][0]['o']==130
        assert data['change_pct']==round(100*(200/130-1),4)

def test_api_cross_origin_reads_and_rate_limit(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as client:
        for headers in ({'Origin':'https://foreign.invalid'},{'Sec-Fetch-Site':'cross-site'},{'Sec-Fetch-Site':'same-site'}):
            assert client.get('/api/credentials',headers=headers).status_code==403
        for _ in range(240):assert client.get('/api/credentials').status_code==200
        response=client.get('/api/credentials')
        assert response.status_code==429 and int(response.headers['retry-after'])>0
        assert client.get('/').status_code==200
        assert client.post('/api/scan/stop').status_code==200

def test_slow_request_body_times_out():
    async def run():
        sent=[]
        async def app(*args):raise AssertionError('Endpoint must not run')
        async def receive():await asyncio.sleep(10)
        async def send(message):sent.append(message)
        with patch('app.http_limits.time') as clock:
            clock.monotonic.side_effect=[0,11]
            await BodyLimitMiddleware(app)({'type':'http','method':'POST'},receive,send)
        assert sent[0]['status']==408
    asyncio.run(run())
