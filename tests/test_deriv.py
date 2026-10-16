"""Protocol fixtures are synthetic; no broker credentials or orders."""
import asyncio
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.deriv import DerivProvider
from app.market import DataError, CandleBatch, Candle
from app.main import create_app
from app.service import Service
from tests.test_system import setup, drain, Feed, TG, fixture




@pytest.mark.parametrize('mode',['ok','closed','suspended','missing','wrong_market'])
def test_active_symbols(mode):
    async def run():
        p=DerivProvider(Config())
        row={'underlying_symbol':'frxEURUSD','market':'forex','exchange_is_open':1,'is_trading_suspended':0}
        if mode=='closed':row['exchange_is_open']=0
        if mode=='suspended':row['is_trading_suspended']=1
        if mode=='wrong_market':row['market']='synthetic_index'
        async def request(payload,expected):return {'active_symbols':[] if mode=='missing' else [row]}
        p.request=request
        if mode=='ok':await p.check_asset('EUR/USD')
        else:
            with pytest.raises(DataError):await p.check_asset('EUR/USD')
    asyncio.run(run())

@pytest.mark.parametrize('mode',['ok','bad_epoch','missing','mismatch','invalid'])
def test_candles(mode):
    async def run():
        p=DerivProvider(Config())
        row={'epoch':1800000000,'open':1,'high':2,'low':.5,'close':1.5}
        if mode=='bad_epoch':row['epoch']=1800000000.1
        if mode=='missing':del row['close']
        data={'candles':[row]}
        if mode=='mismatch':data['echo_req']={'ticks_history':'wrong','granularity':300}
        if mode=='invalid':data['candles']='bad'
        async def request(payload,expected):
            assert payload['ticks_history']=='frxEURUSD' and payload['granularity']==300
            return data
        p.request=request
        if mode=='ok':
            rows=await p.candles('EUR/USD');assert rows.source_identity==p.identity and rows[0].c==1.5
        else:
            with pytest.raises(DataError):await p.candles('EUR/USD')
    asyncio.run(run())

@pytest.mark.parametrize('mode',['ok','ticks','barrier','missing','duration','symbol'])
def test_contract_support(mode):
    async def run():
        p=DerivProvider(Config())
        row={'contract_type':'CALL','underlying_symbol':'frxEURUSD','barriers':0,'min_contract_duration':'1m','max_contract_duration':'1d'}
        if mode=='ticks':row['min_contract_duration']='5t'
        if mode=='barrier':row['barriers']=1
        if mode=='missing':del row['max_contract_duration']
        if mode=='duration':row['min_contract_duration']='1h'
        if mode=='symbol':row['underlying_symbol']='wrong'
        async def request(payload,expected):return {'contracts_for':{'available':[row]}}
        p.request=request
        if mode=='ok':await p.supports('EUR/USD','CALL',300)
        else:
            with pytest.raises(DataError):await p.supports('EUR/USD','CALL',300)
    asyncio.run(run())

def test_wire_protocol():
    class Socket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,payload):
            assert '"req_id": 1' in payload
            assert 'authorize' not in payload and 'buy' not in payload
        async def recv(self):return '{"msg_type":"candles","req_id":1,"candles":[]}'
    async def run():
        with patch('app.deriv.connect',return_value=Socket()) as conn:
            p=DerivProvider(Config());await p.candles('EUR/USD')
            assert conn.call_args.args[0]==p.url
    asyncio.run(run())

def test_connection_check_no_signal(tmp_path):
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'db'))
    headers={'Authorization':'Bearer '+cfg.admin_token}
    async def fail(self,asset):raise DataError('Mercado fechado')
    with patch.object(DerivProvider,'check_asset',fail),patch('app.public_crypto.PublicCrypto.check_asset',fail),TestClient(create_app(cfg)) as c:
        c.put('/api/connection',headers=headers,json={'provider':'deriv'})
        r=c.post('/api/connection/check',headers=headers,json={'provider':'deriv'})
        assert r.json()['status']=='indisponível'
        state=c.get('/api/state',headers=headers).json()
        assert not state['signals'] and not state['busy']
