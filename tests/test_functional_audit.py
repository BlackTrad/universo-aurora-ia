"""Auditoria funcional: contratos, limites, estados e recuperação; sem rede externa."""
import asyncio
import io
import json
from datetime import datetime, timezone
from unittest.mock import patch
import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.config import Config
from app.strategies import scan_all as evaluate
from app.main import create_app
from app.market import DataError,Candle
from app.store import Store
from app.telegram import Telegram,card,caption,local
from tests.test_system import Feed,TG,fixture,setup,drain

AUTH='f'*32

@pytest.mark.parametrize('method,path,body',[
    ('GET','/api/state',None),('POST','/api/analyze',{'assets':['EUR/USD']}),
    ('PUT','/api/settings',{'assets':['EUR/USD']}),('POST','/api/telegram/check',None),
    ('GET','/api/signals/missing',None),('GET','/api/signals/missing/card',None),
    ('POST','/api/signals/missing/cancel',None)])
def test_all_data_endpoints_block_remote_access(tmp_path,method,path,body):
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG()),client=('192.0.2.10',50000)) as client:
        assert client.request(method,path,json=body).status_code==403

@pytest.mark.parametrize('status,allowed',[
    ('pendente',True),('falha',True),('não configurado',True),('repetir',True),
    ('enviado',False),('enviando',False),('incerto',False)])
def test_cancel_contract(tmp_path,status,allowed):
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    tg=TG()
    with TestClient(create_app(cfg,Feed(),tg)) as client:
        service=client.app.state.service
        now=service.clock()
        signal={'id':'cancel-me','created_at':now,'entry_at':now+300,'expiry_at':now+600,
            'state':'aguardando entrada','settlement_pending':True,'telegram':{'status':status}}
        service.store.add_signal(signal,'cancel-me')
        response=client.post('/api/signals/cancel-me/cancel',headers={'Authorization':'Bearer '+AUTH})
        assert response.status_code==(200 if allowed else 409)
        if allowed:
            assert response.json()['state']=='cancelado'
            assert response.json()['settlement_pending'] is False
            assert not service.store.active()
            assert service.store.events('cancel-me')[-1]['kind']=='cancelado'
        assert tg.calls==0

@pytest.mark.parametrize('path',['/api/signals/missing','/api/signals/missing/card','/api/signals/missing/cancel'])
def test_unknown_signal_is_404(tmp_path,path):
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        response=client.request('POST' if path.endswith('/cancel') else 'GET',path,headers={'Authorization':'Bearer '+AUTH})
        assert response.status_code==404

@pytest.mark.parametrize('body',[{'assets':[]},{'assets':['EUR/USD','EUR/USD']},{'assets':['OTHER']},{'assets':['EUR/USD'],'duration_seconds':600}])
def test_settings_reject_invalid_values(tmp_path,body):
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.put('/api/settings',json={**body,'provider':'deriv'},headers={'Authorization':'Bearer '+AUTH}).status_code==422


def test_expired_signal_cannot_be_cancelled(tmp_path):
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        service=client.app.state.service;now=service.clock()
        service.store.add_signal({'id':'old','created_at':now-100,'entry_at':now-1,'expiry_at':now+299,
            'state':'aguardando entrada','telegram':{'status':'falha'}},'old')
        assert client.post('/api/signals/old/cancel',headers={'Authorization':'Bearer '+AUTH}).status_code==409

@pytest.mark.parametrize('seconds,duration',[(5,300),(240,300),(241,180),(299,180)])
def test_entry_grid_and_duration_are_independent(tmp_path,seconds,duration):
    async def run():
        service,now=setup(tmp_path)
        now[0]=fixture()[-1].t+300+seconds
        service.start(['EUR/USD'],duration);await drain(service)
        sig=service.store.signals()[0]
        assert 60<=sig['entry_at']-now[0]<360
        assert sig['entry_at']%60==0
        assert sig['expiry_at']-sig['entry_at']==duration
        assert sig['timeframe']=='M5'
    asyncio.run(run())

@pytest.mark.parametrize('final,expected',[(99,'WIN'),(101,'LOSS'),(100,'EMPATE')])
def test_put_settlement(tmp_path,final,expected):
    async def run():
        service,now=setup(tmp_path)
        service.provider.rows=[Candle(c.t,200-c.o,200-c.l,200-c.h,200-c.c) for c in fixture()]
        service.start(['EUR/USD'],300);await drain(service)
        sig=service.store.signals()[0];assert sig['direction']=='PUT'
        service.provider.settlement=[Candle(sig['entry_at'],100,101,99,100),Candle(sig['expiry_at'],final,final+1,final-1,final)]
        now[0]=sig['expiry_at']+65;await service.tick();await drain(service)
        assert service.store.signal(sig['id'])['result']==expected
    asyncio.run(run())


def test_restart_recovers_analysis_and_preferences(tmp_path):
    store=Store(str(tmp_path/'db'))
    store.analysis({'id':'unfinished','created_at':10,'status':'analisando'})
    store.save_settings({'assets':['GBP/USD'],'timeframe':'M5','duration_seconds':600})
    store.acquire('unfinished',10);store.db.close()
    restored=Store(str(tmp_path/'db'));restored.recover(11)
    assert restored.analyses()[0]['status']=='interrompida'
    assert restored.settings()['duration_seconds']==600
    assert not restored.busy(11)
    restored.db.close()


def test_same_database_rejects_concurrent_process_lifespan(tmp_path):
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as first:
        with pytest.raises(RuntimeError,match='único processo'):
            with TestClient(create_app(cfg,Feed(),TG())):pass
        assert first.get('/').status_code==200


@pytest.mark.parametrize('mode',['good','bad_token','bad_chat','offline','unconfigured'])
def test_telegram_connection_check_sends_explicit_test(mode):
    async def run():
        methods=[]
        def handle(request):
            method=request.url.path.split('/')[-1];methods.append(method)
            assert method=='sendMessage'
            if mode=='offline':raise httpx.ConnectError('SECRET_TEST')
            if mode=='bad_token':return httpx.Response(401,json={'ok':False,'error_code':401})
            if mode=='bad_chat':return httpx.Response(400,json={'ok':False,'error_code':400})
            return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        cfg=Config(telegram_token='' if mode=='unconfigured' else '123:test',telegram_chat='' if mode=='unconfigured' else '1')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result=await Telegram(cfg,client).check()
            assert result['status']==('conectado' if mode=='good' else 'não configurado' if mode=='unconfigured' else 'incerto' if mode=='offline' else 'falha')
            assert 'SECRET_TEST' not in json.dumps(result)
            if mode=='unconfigured':assert not methods
    asyncio.run(run())


def test_brasilia_midnight_and_card_payload(tmp_path):
    async def run():
        service,now=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service)
        s=service.store.signals()[0]
        s['entry_at']=datetime(2026,9,22,2,55,tzinfo=timezone.utc).timestamp()
        s['expiry_at']=s['entry_at']+600
        assert local(s['entry_at'])=='21/09/2026 23:55:00'
        assert local(s['expiry_at'])=='22/09/2026 00:05:00'
        for direction in ('CALL','PUT'):
            s['direction']=direction
            png=Image.open(io.BytesIO(card(s)))
            assert png.size==(1080,860) and png.format=='PNG'
            message=caption(s)
            assert ('Compra' if direction=='CALL' else 'Venda') in message and 'ID:' not in message and s['asset'] in message
            assert len(message)<=1024 and 'SEM MARTINGALE' in message
            assert '23:55' in message and f"{s['duration_seconds']//60} Minutos" in message
    asyncio.run(run())


def test_query_endpoints_do_not_expose_env_credentials(tmp_path):
    cfg=Config(admin_token=AUTH,telegram_token='123:TG_SECRET',telegram_chat='-100123',database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        state=client.get('/api/state',headers={'Authorization':'Bearer '+AUTH})
        for secret in (AUTH,cfg.telegram_token):assert secret not in state.text
        assert state.headers['cache-control']=='no-store'
        assert "frame-ancestors 'none'" in state.headers['content-security-policy']
        assert client.get('/static/../.env').status_code==404


def test_empty_and_stalled_market_blocked(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        for rows in ([],[Candle(c.t,100,100,100,100) for c in fixture()]):
            service.provider.rows=rows
            service.start(['EUR/USD'],300);await drain(service)
            assert not service.store.signals()
        assert not service.store.busy(now[0])
    asyncio.run(run())


def test_data_must_still_be_fresh_after_evaluation(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        def slow_engine(rows):
            result=evaluate(rows)
            now[0]+=400
            return result
        with patch('app.service.evaluate',side_effect=slow_engine):
            service.start(['EUR/USD'],300);await drain(service)
        assert not service.store.signals()
    asyncio.run(run())


def test_api_returns_conflict_while_analysis_runs(tmp_path):
    import threading
    release=threading.Event()
    class WaitingFeed(Feed):
        async def candles(self,*args,**kwargs):
            while not release.is_set():await asyncio.sleep(.005)
            return await super().candles(*args,**kwargs)
    cfg=Config(admin_token=AUTH,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,WaitingFeed(),TG())) as client:
        headers={'Authorization':'Bearer '+AUTH}
        try:
            assert client.post('/api/analyze',json={'provider':'deriv','assets':['EUR/USD']},headers=headers).status_code==202
            assert client.get('/api/state',headers=headers).json()['busy'] is True
            assert client.post('/api/analyze',json={'provider':'deriv','assets':['EUR/USD']},headers=headers).status_code==409
        finally:release.set()


def test_signal_survives_without_telegram_credentials(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        service.telegram=Telegram(Config(),None)
        service.start(['EUR/USD'],300);await drain(service)
        signal=service.store.signals()[0]
        assert signal['state']=='aguardando entrada'
        assert signal['telegram']['status']=='não configurado'
        assert service.snapshot()['signals'][0]['id']==signal['id']
    asyncio.run(run())
