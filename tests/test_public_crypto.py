import asyncio
import json
import time
from unittest.mock import patch
import httpx
import pytest
from app.public_crypto import BinanceProvider,CoinbaseProvider
from app.market import DataError,Candle,CandleBatch
from app.quotes import Quotes
from tests.test_system import setup,drain,Feed,TG
from app.service import Service
from app.config import Config
from app.store import Store
from app.main import create_app
from fastapi.testclient import TestClient


def rows(interval=60,base=100,count=100):
    last=int(time.time()//interval)*interval
    return [Candle(last-(count-1-i)*interval,base,base+1,base-1,base+.5,3) for i in range(count)]

def handler(kind,seen):
    def respond(request):
        seen.append(request)
        assert request.method=='GET' and 'X-MBX-APIKEY' not in request.headers
        if request.url.path.endswith('exchangeInfo'):
            return httpx.Response(200,json={'symbols':[{'symbol':'BTCUSDT','baseAsset':'BTC','quoteAsset':'USDT','status':'TRADING','isSpotTradingAllowed':True}]})
        if request.url.path.endswith('BTC-USD'):
            return httpx.Response(200,json={'id':'BTC-USD','base_currency':'BTC','quote_currency':'USD','status':'online','trading_disabled':False})
        data=rows(300 if request.url.params.get('interval')=='5m' or request.url.params.get('granularity')=='300' else 60)
        if kind=='binance':return httpx.Response(200,json=[[c.t*1000,str(c.o),str(c.h),str(c.l),str(c.c),str(c.v)] for c in data])
        return httpx.Response(200,json=[[c.t,c.l,c.h,c.o,c.c,c.v] for c in reversed(data)])
    return respond


@pytest.mark.parametrize('cls,asset',[(BinanceProvider,'BTC/USDT'),(CoinbaseProvider,'BTC/USD')])
def test_public_candles_catalog_cache_and_identity(cls,asset):
    async def run():
        seen=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler(cls.key,seen))) as client:
            p=cls(client);await p.check_asset(asset);await p.check_asset(asset)
            first,second=await asyncio.gather(p.candles(asset),p.candles(asset))
            assert len(seen)==2 and len(first)==100 and first==second
            assert first.source_identity==p.identity and first[0].v==3
            assert first[-1].o==100 and first[-1].h==101 and first[-1].l==99 and first[-1].c==100.5
            with pytest.raises(DataError):await p.candles('EUR/USD')
            with pytest.raises(DataError):await p.candles(asset,120)
    asyncio.run(run())


@pytest.mark.parametrize('status',[418,429])
def test_public_limits_respect_retry_after_without_retries(status):
    async def run():
        requests=[]
        def reject(request):requests.append(request);return httpx.Response(status,headers={'Retry-After':'180'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
            p=BinanceProvider(client)
            for _ in range(2):
                with pytest.raises(DataError) as e:await p.candles('BTC/USDT')
                assert e.value.code=='rate_limit'
            assert len(requests)==1 and p.cooldown_until-time.monotonic()>175
    asyncio.run(run())


@pytest.mark.parametrize('cls,asset',[(BinanceProvider,'BTC/USDT'),(CoinbaseProvider,'BTC/USD')])
def test_historical_settlement_uses_exact_end(cls,asset):
    async def run():
        seen=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:seen.append(r) or httpx.Response(200,json=[]))) as client:
            p=cls(client);entry=int(time.time()//60)*60-600;expiry=entry+300
            await p.settlement_candles(asset,entry,expiry)
            q=seen[0].url.params
            if cls.key=='binance':assert int(q['endTime'])==(expiry+59)*1000+999
            else:
                from datetime import datetime
                assert datetime.fromisoformat(q['end']).timestamp()==expiry+59
            await p.settlement_candles(asset,entry,expiry)
            assert len(seen)==2
    asyncio.run(run())


@pytest.mark.parametrize('invalid',[[[1,1,2,0,1,-1]],[[True,1,2,0,1,1]],{'error':'bad'}])
def test_invalid_public_data_blocks_emission(invalid):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=invalid))) as client:
            with pytest.raises(DataError):await BinanceProvider(client).candles('BTC/USDT')
    asyncio.run(run())


def test_switch_resets_assets_and_keeps_original_settlement(tmp_path):
    async def run():
        s,clock=setup(tmp_path)
        s.start(['EUR/USD'],60);await drain(s);original=s.store.signals()[0]
        old=s.provider
        class Crypto(Feed):
            assets=['BTC/USDT'];timeframes=['M1','M5'];name='Binance';identity='binance:spot:USDT:public'
        crypto=Crypto();s.providers['binance']=crypto;s.select_provider('binance')
        assert s.settings()['assets']==['BTC/USDT'] and s.available_assets==['BTC/USDT']
        with pytest.raises(ValueError):s.start(['EUR/USD'],60)
        with pytest.raises(ValueError):s.start_scan(['BTC/USDT'],60,'M2')
        old.settlement=CandleBatch([Candle(original['entry_at'],100,101,99,100),Candle(original['expiry_at'],101,102,100,101)],original['source_identity'])
        clock[0]=original['expiry_at']+65
        await s.settle(original)
        assert s.store.signal(original['id'])['result']=='WIN'
        assert s.store.signal(original['id'])['source_key']=='deriv'
        s.store.db.close()
    asyncio.run(run())


def test_crypto_signal_engine_and_original_settlement(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        class Crypto(Feed):
            assets=['BTC/USDT'];timeframes=['M1','M5'];name='Binance';identity='binance:spot:USDT:public'
            async def candles(self,asset,interval=300,count=100):
                return CandleBatch(self.rows if interval==300 else self.settlement,self.identity)
        crypto=Crypto();service.providers['binance']=crypto;service.select_provider('binance')
        service.start(['BTC/USDT'],60,'M5');await drain(service)
        s=service.store.signals()[0]
        assert s['asset']=='BTC/USDT' and s['source_key']=='binance' and s['source_identity']==crypto.identity
        assert s['telegram']['status']=='enviado'
        crypto.settlement=[Candle(s['entry_at'],100,101,99,100),Candle(s['expiry_at'],101,102,100,101)]
        service.select_provider('deriv');clock[0]=s['expiry_at']+65
        await service.settle(s)
        assert service.store.signal(s['id'])['result']=='WIN'
        assert service.store.signal(s['id'])['settlement_source_identity']==crypto.identity
        await drain(service);service.store.db.close()
    asyncio.run(run())


def test_quote_cache_separated_by_source():
    async def run():
        class Source:
            name='public'
            def __init__(self,identity,base):self.identity=identity;self.base=base
            async def check_asset(self,a):pass
            async def candles(self,*args):return rows(base=self.base,count=2)
            async def reference_candle(self,a,midnight):return Candle(midnight-60,self.base,self.base+1,self.base-1,self.base)
        q=Quotes();a=await q.get(Source('a',100),'BTC/USD');b=await q.get(Source('b',200),'BTC/USD')
        assert a['price']==100.5 and b['price']==200.5
        assert a['source_identity']!=b['source_identity']
    asyncio.run(run())


def test_api_automatic_chart_quotes_assets_and_restart(tmp_path):
    class Crypto(Feed):
        timeframes=['M1','M5'];identity='binance:spot:USDT:public';name='Binance';assets=['BTC/USDT']
        async def check_asset(self,a):pass
        async def candles(self,a,interval=300,count=100,**kw):return CandleBatch(rows(interval=interval,count=count),self.identity)
        async def reference_candle(self,a,midnight):return Candle(midnight-60,100,101,99,100)
    cfg=Config(database=str(tmp_path/'db'))
    with patch('app.main.BinanceProvider',side_effect=lambda client:Crypto()):
        with TestClient(create_app(cfg)) as c:
            assert c.put('/api/connection',json={'provider':'binance'}).status_code==422
            state=c.get('/api/state').json()
            assert state['source_mode']=='automatic'
            assert 'BTC/USDT' in state['available_assets'] and 'EUR/USD' in state['available_assets']
            assert state['timeframes']==['M1','M5']
            chart=c.get('/api/chart',params={'asset':'BTC/USDT','source':'binance','timeframe':'M1'})
            assert chart.status_code==200 and chart.json()['source_key']=='binance'
            quote=c.get('/api/quote',params={'asset':'BTC/USDT','source':'binance'}).json()
            assert quote['price']==100.5 and quote['change_pct']==.5
            assert c.get('/api/chart',params={'asset':'BTC/USDT','source':'deriv'}).status_code==422
            assert c.get('/api/chart',params={'asset':'BTC/USDT','timeframe':'M2'}).status_code==422
            assert c.get('/api/chart',params={'asset':'FAKE'}).status_code==422
            service=c.app.state.service;service.analysis_active=True
            assert c.put('/api/connection',json={'provider':'deriv'}).status_code==422
            service.analysis_active=False
            assert c.put('/api/settings',json={**state['settings'],'assets':['BTC/USDT']}).status_code==200
        with TestClient(create_app(cfg)) as c:
            restored=c.get('/api/state').json()
            assert restored['source_mode']=='automatic' and restored['settings']['assets']==['BTC/USDT']
            assert c.put('/api/connection',json={'provider':'deriv'}).status_code==422
            assert 'EUR/USD' in restored['available_assets']
