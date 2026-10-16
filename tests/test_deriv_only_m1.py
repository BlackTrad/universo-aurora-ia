"""Synthetic regressions for Deriv-only migration and M1; no real messages/orders."""
import asyncio
import json
import os
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config as config_module
from app.config import Config
from app.deriv import DerivProvider
from app.main import create_app
from app.market import Candle, DataError
from app.service import Service
from app.store import Store
from app.telegram import Telegram, caption
from tests.test_system import Feed, TG, fixture, drain


def rows_for(interval, direction='CALL'):
    rows=[Candle(1800000000+i*interval,c.o,c.h,c.l,c.c) for i,c in enumerate(fixture())]
    return rows if direction=='CALL' else [Candle(c.t,200-c.o,200-c.l,200-c.h,200-c.c) for c in rows]


@pytest.mark.parametrize('timeframe,interval',[('M1',60),('M5',300)])
@pytest.mark.parametrize('duration',[60,300])
@pytest.mark.parametrize('direction',['CALL','PUT'])
@pytest.mark.parametrize('outcome',['WIN','LOSS','EMPATE'])
def test_deriv_wire_engine_telegram_and_settlement(tmp_path,timeframe,interval,duration,direction,outcome):
    async def run():
        rows=rows_for(interval,direction);now=[rows[-1].t+interval+5];wire=[];photos=[]
        quotes=[]
        class Socket:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def send(self,text):self.payload=json.loads(text);wire.append(self.payload)
            async def recv(self):
                p=self.payload
                if 'active_symbols' in p:
                    return json.dumps({'msg_type':'active_symbols','req_id':1,'active_symbols':[{'underlying_symbol':'frxEURUSD','market':'forex','exchange_is_open':1,'is_trading_suspended':0}]})
                if 'contracts_for' in p:
                    return json.dumps({'msg_type':'contracts_for','req_id':1,'contracts_for':{'available':[{'underlying_symbol':'frxEURUSD','contract_type':direction,'barriers':0,'min_contract_duration':'1m','max_contract_duration':'1h'}]}})
                assert p['ticks_history']=='frxEURUSD'
                assert p['granularity']==(60 if quotes else interval)
                return json.dumps({'msg_type':'candles','req_id':1,'echo_req':p,'candles':[{'epoch':c.t,'open':c.o,'high':c.h,'low':c.l,'close':c.c} for c in (quotes or rows)]})
        def telegram_request(request):
            assert request.url.host=='api.telegram.org'
            assert request.url.path.endswith('/sendPhoto')
            assert b'OPORTUNIDADE ENCONTRADA' not in request.content and b'Ativo:' in request.content
            assert b'\x89PNG\r\n\x1a\n' in request.content
            photos.append(request.content)
            return httpx.Response(200,json={'ok':True,'result':{'message_id':456}})
        cfg=Config(strategy='candlestick_sr',database=str(tmp_path/'db'),telegram_token='123:synthetic',telegram_chat='123')
        store=Store(cfg.database)
        with patch('app.deriv.connect',side_effect=lambda *a,**kw:Socket()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(telegram_request)) as client:
                service=Service(cfg,store,DerivProvider(cfg),Telegram(cfg,client),clock=lambda:now[0])
                analysis=service.start(['EUR/USD'],duration,timeframe);await drain(service)
                signal=store.signals()[0]
                assert analysis['timeframe']==timeframe
                assert signal['timeframe']==timeframe and signal['direction']==direction
                assert signal['source_key']=='deriv' and signal['source_identity']=='deriv:forex:public'
                assert signal['entry_at']%60==0 and signal['entry_at']-now[0]>=60
                assert signal['expiry_at']-signal['entry_at']==duration
                assert signal['data_updated_at']==rows[-1].t+interval
                assert signal['evidence']['interval_seconds']==interval
                assert signal['telegram']['status']=='enviado' and len(photos)==1
                assert 'OPORTUNIDADE ENCONTRADA' not in caption(signal) and '💱Ativo:' in caption(signal)
                assert all('authorize' not in p and 'buy' not in p for p in wire)
                movement=1 if direction=='CALL' else -1
                final=100 if outcome=='EMPATE' else 100+movement*(1 if outcome=='WIN' else -1)
                quotes.extend([Candle(signal['entry_at'],100,102,98,100),Candle(signal['expiry_at'],final,102,98,final)])
                now[0]=signal['expiry_at']+70
                await service.settle(signal)
                assert store.signal(signal['id'])['result']==outcome
                assert store.signal(signal['id'])['broker_confirmed'] is False
                assert len(store.signal(signal['id'])['settlement_evidence'])==2
        store.db.close()
        reopened=Store(cfg.database)
        assert reopened.signal(signal['id'])['result']==outcome
        reopened.db.close()
    asyncio.run(run())


@pytest.mark.parametrize('bad',['stale','incomplete','gap','open_final','minute_changed'])
def test_m1_rejects_invalid_or_delayed_setup(tmp_path,bad):
    async def run():
        rows=rows_for(60);now=[rows[-1].t+65]
        if bad=='stale':now[0]+=91
        if bad=='incomplete':rows=rows[-10:]
        if bad=='gap':rows.pop(-3)
        if bad=='open_final':now[0]=rows[-1].t+30
        class Source(Feed):
            async def candles(self,asset,interval=300,count=100):
                assert interval==60
                return rows
        service=Service(Config(strategy='candlestick_sr'),Store(':memory:'),Source(),TG(),clock=lambda:now[0])
        from unittest.mock import patch
        from app.service import evaluate as original
        def delayed(rows):
            result=original(rows)
            if bad=='minute_changed':now[0]+=60
            return result
        with patch('app.service.evaluate',side_effect=delayed):
            service.start(['EUR/USD'],300,'M1');await drain(service)
        assert service.store.signals()==[]
        assert not service.analysis_active
        service.store.db.close()
    asyncio.run(run())


def test_timeframe_is_part_of_deduplication(tmp_path):
    async def run():
        data={}
        for interval in (60,300):
            rows=rows_for(interval);shift=1800100200-rows[-1].t
            data[interval]=[Candle(c.t+shift,c.o,c.h,c.l,c.c) for c in rows]
        now=[1800100505]
        class Source(Feed):
            async def candles(self,asset,interval=300,count=100):return data[interval]
        service=Service(Config(strategy='candlestick_sr'),Store(':memory:'),Source(),TG(),clock=lambda:now[0])
        service.start(['EUR/USD'],300,'M5');await drain(service)
        # The same asset cannot hold overlapping signals, even across periods.
        # Cancel the first fixture before checking independent M1 identity.
        prior=service.store.signals()[0]
        service.store.patch(prior['id'],{'state':'cancelado','result':'CANCELADO','settlement_pending':False},
                            'cancelado',now[0])
        now[0]=1800100265
        service.start(['EUR/USD'],300,'M1');await drain(service)
        service.start(['EUR/USD'],300,'M1');await drain(service)
        assert {s['timeframe'] for s in service.store.signals()}=={'M1','M5'}
        assert len(service.store.signals())==2
        assert any(c['kind']=='duplicate' for a in service.store.analyses() for c in a['checks'])
        service.store.db.close()
    asyncio.run(run())


def test_upgrade_old_environment_settings_and_pending_signal(tmp_path):
    (tmp_path/'.env').write_text('DATA_PROVIDER=twelvedata\nTWELVEDATA_API_KEY=legacy_secret\n',encoding='utf-8')
    with patch.dict(os.environ,{},clear=True),patch.object(config_module,'ROOT',tmp_path):
        cfg=Config.load()
    assert cfg.provider=='deriv' and not hasattr(cfg,'api_key')
    cfg.database=str(tmp_path/'db')
    store=Store(cfg.database)
    store.save_settings({'provider':'twelvedata','assets':['GBP/USD'],'strategy':'range_pivot','timeframe':'M1','duration_seconds':600})
    old={'id':'legacy','source_key':'twelvedata','source':'Twelve Data','source_identity':'twelvedata:forex','asset':'EUR/USD','state':'encerrado',
         'entry_at':1800000000,'expiry_at':1800000300,'created_at':1800000000,'settlement_pending':True,'telegram':{'status':'enviado'}}
    store.add_signal(old,'legacy-fingerprint')
    source=Feed()
    service=Service(cfg,store,source,TG())
    assert service.settings()['provider']=='deriv'
    assert service.settings()['timeframe']=='M1' and service.settings()['duration_seconds']==300
    asyncio.run(service.settle(old))
    saved=store.signal('legacy')
    assert saved['source_key']=='twelvedata' and saved['result']=='Resultado indisponível'
    assert saved['settlement_pending'] is False and source.calls==0
    assert 'Fonte original desativada' in saved['settlement_rule']
    store.db.close()


def test_deriv_only_api_and_m1_settings_survive_restart(tmp_path):
    cfg=Config(database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        snapshot=client.get('/api/state').json()
        assert snapshot['provider']=='deriv' and [x['key'] for x in snapshot['providers']]==['deriv']
        for endpoint in ['/api/analyze','/api/settings']:
            method=client.post if endpoint.endswith('analyze') else client.put
            assert method(endpoint,json={'provider':'twelvedata','assets':['EUR/USD']}).status_code==422
        assert client.put('/api/connection',json={'provider':'twelvedata'}).status_code==422
        assert client.put('/api/credentials',json={'api_key':'legacy_secret'}).status_code==422
        assert 'legacy_secret' not in client.get('/api/state').text
        body={'provider':'deriv','assets':['EUR/USD'],'timeframe':'M1','duration_seconds':300}
        assert client.put('/api/settings',json=body).status_code==200
        assert client.post('/api/analyze',json={**body,'timeframe':'M4'}).status_code==422
        service=client.app.state.service
        service.analysis_active=True
        assert client.put('/api/connection',json={'provider':'deriv'}).status_code==409
        service.analysis_active=False
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.get('/api/state').json()['settings']['timeframe']=='M1'


def test_m1_connection_check_uses_saved_period(tmp_path):
    import time
    rows=rows_for(60);shift=int(time.time()//60)*60-rows[-1].t-60
    class Source(Feed):
        async def candles(self,asset,interval=300,count=100):
            assert interval==60
            return [Candle(c.t+shift,c.o,c.h,c.l,c.c) for c in rows]
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Source(),TG())) as client:
        assert client.put('/api/settings',json={'provider':'deriv','assets':['EUR/USD'],'timeframe':'M1'}).status_code==200
        response=client.post('/api/connection/check',json={'provider':'deriv'}).json()
        assert response['status']=='disponível' and response['max_age']==90
        assert response['timeframe']=='M1'
