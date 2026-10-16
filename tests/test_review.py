"""Regressões encontradas na revisão 1.0.1."""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.strategies import scan_all as evaluate
from app.market import Candle, CandleBatch, DataError
from app.main import create_app
from app.telegram import Telegram
from tests.test_system import fixture,setup,drain,TG,Feed


def test_breakout_candle_is_not_its_own_retest():
    rows=[Candle(**r) for r in json.loads((Path(__file__).parent/'breakout_only_fixture.json').read_text())]
    assert evaluate(rows)[0] is None
    assert evaluate(fixture())[0]['direction']=='CALL'


def test_telegram_does_not_shorten_retry_after():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={'ok':False,'parameters':{'retry_after':180}}))) as client:
            cfg=Config(telegram_token='123:test',telegram_chat='1')
            with patch('app.telegram.card',return_value=b'PNG'),patch('app.telegram.caption',return_value='test'):
                result=await Telegram(cfg,client).send({'entry_at':time.time()+300})
            assert result['retry_after']==180
    asyncio.run(run())

def test_long_retry_does_not_send_after_deadline(tmp_path):
    async def run():
        s,now=setup(tmp_path)
        async def reject(sig):
            s.telegram.calls+=1
            return {'status':'repetir','retry_after':1000}
        s.telegram.send=reject
        s.start(['EUR/USD'],300);await drain(s)
        assert s.telegram.calls==1
        assert s.store.signals()[0]['telegram']['status']=='falha'
    asyncio.run(run())

def test_settings_reconciled_with_enabled_assets(tmp_path):
    s,_=setup(tmp_path)
    s.store.save_settings({'assets':['DISABLED'],'duration_seconds':10})
    assert s.settings()=={**s.store.risk_settings(),'provider':'deriv','strategy':'candlestick_sr','assets':s.config.assets,'timeframe':'M5','duration_seconds':300,'stake_brl':None,'payout_pct':None,'selection_observation':False,'source_mode':'manual'}


def test_feed_status_ages(tmp_path):
    s,now=setup(tmp_path)
    s.feed={'status':'disponível','data_at':now[0]-331,'detail':'old'}
    assert s.snapshot()['feed']['status']=='desatualizado'


def test_request_identity_wins_over_mutable_provider_identity(tmp_path):
    async def run():
        s,now=setup(tmp_path)
        s.provider.rows=CandleBatch(fixture(),'source-response-A')
        s.provider.identity='different-concurrent-request'
        s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0]
        assert sig['source_identity']=='source-response-A'
        s.provider.settlement=CandleBatch([Candle(sig['entry_at'],100,101,99,100),Candle(sig['expiry_at'],101,102,100,101)],'source-response-A')
        now[0]=sig['expiry_at']+601;await s.tick();await drain(s)
        assert s.store.signal(sig['id'])['result']=='WIN'
    asyncio.run(run())

def test_changed_source_identity_rejects_settlement(tmp_path):
    async def run():
        s,now=setup(tmp_path)
        s.provider.rows=CandleBatch(fixture(),'source-A')
        s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0]
        s.provider.settlement=CandleBatch([Candle(sig['entry_at'],100,101,99,100),Candle(sig['expiry_at'],101,102,100,101)],'source-B')
        now[0]=sig['expiry_at']+601;await s.tick();await drain(s)
        assert s.store.signal(sig['id'])['result']=='Resultado indisponível'
    asyncio.run(run())

def test_unexpected_settlement_error_finishes_after_deadline(tmp_path):
    async def run():
        s,now=setup(tmp_path);s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0]
        async def broken(*args): raise AttributeError('bad response with sensitive text')
        s.provider.candles=broken
        now[0]=sig['expiry_at']+601;await s.tick();await drain(s)
        result=s.store.signal(sig['id'])
        assert result['settlement_pending'] is False
        assert result['result']=='Resultado indisponível'
        assert 'sensitive' not in json.dumps(result)
    asyncio.run(run())

def test_slow_settlement_does_not_block_states(tmp_path):
    async def run():
        s,now=setup(tmp_path);s.start(['EUR/USD'],300);await drain(s)
        old=s.store.signals()[0];now[0]=old['expiry_at']+601
        current={**old,'movement_id':None,'id':'second','entry_at':now[0],'expiry_at':now[0]+300,'state':'aguardando entrada'}
        s.store.add_signal(current,'second')
        gate=asyncio.Event()
        async def slow(*args): await gate.wait();return []
        s.provider.candles=slow
        await asyncio.wait_for(s.tick(),.2)
        assert s.store.signal('second')['state']=='em andamento'
        assert s.store.signal(old['id'])['state']=='encerrado'
        assert old['id'] in s.settling
        gate.set();await drain(s)
    asyncio.run(run())

def test_legacy_header_is_ignored(tmp_path):
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'api.db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.get('/api/state',headers={'Authorization':cfg.admin_token}).status_code==200
        assert client.get('/api/state',headers={'Authorization':'Bearer '+cfg.admin_token}).status_code==200

def test_chunked_body_cannot_bypass_size_limit(tmp_path):
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'api.db'))
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        def body():
            yield b'{"assets":["'
            yield b'A'*9000
            yield b'"]}'
        response=client.post('/api/analyze',content=body(),headers={'Authorization':'Bearer '+cfg.admin_token,'Content-Type':'application/json'})
        assert response.status_code==413


def test_lifespan_error_releases_database_lock(tmp_path):
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'api.db'))
    with pytest.raises(RuntimeError,match='test failure'):
        with TestClient(create_app(cfg,Feed(),TG())):
            raise RuntimeError('test failure')
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.get('/').status_code==200

@pytest.mark.parametrize('name',['mt5','unsupported','mock',''])
def test_unknown_provider_rejected(name):
    config=Config(admin_token='a'*32,provider=name)
    with pytest.raises(ValueError,match='Fonte inválida'):config.validate()







def test_legacy_other_source_is_not_settled_with_deriv(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        signal=service.store.signals()[0]
        service.store.patch(signal['id'],{'source_key':'legacy-source'})
        count=service.provider.calls
        now[0]=signal['expiry_at']+601
        await service.tick();await drain(service)
        assert service.provider.calls==count
        assert service.store.signal(signal['id'])['result']=='Resultado indisponível'
    asyncio.run(run())
