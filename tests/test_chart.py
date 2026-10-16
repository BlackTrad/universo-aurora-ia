import time
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from app.market import Candle,DataError
from tests.test_system import TG

class Source:
    name='Fonte simulada';ready=True
    def __init__(self,closed=False):self.calls=0;self.closed=closed
    async def check_asset(self,asset):
        if self.closed:raise DataError('Mercado fechado','market_closed')
    async def candles(self,asset,interval,count):
        self.calls+=1
        now=int(time.time()//interval)*interval
        return [Candle(now-i*interval,100,102,99,101) for i in range(49,-1,-1)]


def test_chart_data_cache_validation_and_closed_status(tmp_path):
    source=Source(closed=True)
    with TestClient(create_app(Config(database=str(tmp_path/'db')),source,TG())) as client:
        assert client.get('/api/chart?asset=FAKE&timeframe=M1').status_code==422
        assert client.get('/api/chart?asset=EUR/USD&timeframe=H1').status_code==422
        data=client.get('/api/chart?asset=EUR/USD&timeframe=M1').json()
        assert len(data['candles'])==50 and data['status']=='closed'
        assert data['price']==101
        client.get('/api/chart?asset=EUR/USD&timeframe=M1')
        assert source.calls==1
        assert not client.get('/api/state').json()['signals']


def test_chart_never_substitutes_fake_data_on_failure(tmp_path):
    class Broken(Source):
        async def candles(self,*args):raise DataError('Fonte indisponível','timeout')
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Broken(),TG())) as client:
        response=client.get('/api/chart?asset=EUR/USD')
        assert response.status_code==503 and 'candles' not in response.json()
