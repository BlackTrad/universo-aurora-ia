"""Fail-closed binary crypto trading-signal mode tests; no live API calls."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
from app.config import Config, ConfigurationError
from app.deriv import DerivProvider
from app.market import DataError
from app.main import create_app
from app.source_routing import CRYPTO_BINARY_ASSETS, assets_for_source, plans
from app.service import Service
from app.store import Store


def cfg(**kw):
    return Config(asset_scope='crypto_binary', assets=list(CRYPTO_BINARY_ASSETS), **kw)


def test_binary_mode_loads_exact_18_assets(tmp_path):
    with patch.object(config_module,'ROOT',tmp_path),patch.dict(os.environ,{},clear=True):
        c=Config.load()
    assert c.asset_scope=='crypto_binary'
    assert c.provider=='deriv'
    assert c.assets==list(CRYPTO_BINARY_ASSETS)
    assert len(c.assets)==18


@pytest.mark.parametrize('invalid',['EUR/USD','BTC/USDT','SOL/USD','XRP/USD','DOGE/USDT'])
def test_binary_scope_rejects_unapproved_assets(invalid):
    with pytest.raises(ConfigurationError):
        Config(asset_scope='crypto_binary',assets=[invalid]).validate()


def test_binary_requires_deriv_provider():
    with pytest.raises(ConfigurationError):
        Config(asset_scope='crypto_binary', provider='coinbase',assets=['BTC/USD']).validate()


def test_binary_source_routing_excludes_spot_sources():
    c=cfg()
    fake=SimpleNamespace(config=c,provider_key='deriv',automatic_sources=True,
       providers={'deriv':SimpleNamespace(timeframes=['M1','M5']),
                  'coinbase':SimpleNamespace(timeframes=['M1','M5'],assets=['BTC/USD']),
                  'binance':SimpleNamespace(timeframes=['M1','M5'],assets=['BTC/USDT'])})
    assert assets_for_source(fake,'deriv')==list(CRYPTO_BINARY_ASSETS)
    assert assets_for_source(fake,'coinbase')==[]
    assert assets_for_source(fake,'binance')==[]
    assert plans(fake,['BTC/USD'],'M5')==[{'source_key':'deriv','assets':['BTC/USD']}]


def test_deriv_symbol_not_accepted_without_catalog_verification():
    provider=DerivProvider(cfg())
    assert provider.symbol('BTC/USD')=='cryBTCUSD'
    with pytest.raises(DataError):provider.symbol('BTC/USDT')

    async def check():
        async def forged():
            return {'active_symbols':[{'underlying_symbol':'cryBTCUSD','market':'forex',
                     'exchange_is_open':1,'is_trading_suspended':0}]}
        provider.catalog=forged
        with pytest.raises(DataError) as exc:
            await provider.check_asset('BTC/USD')
        assert exc.value.code=='unsupported_asset'
        async def valid():
            return {'active_symbols':[{'underlying_symbol':'cryBTCUSD','market':'cryptocurrency',
                     'exchange_is_open':1,'is_trading_suspended':0}]}
        provider.catalog=valid
        await provider.check_asset('BTC/USD')
    asyncio.run(check())


def test_proposal_requires_net_payout_and_matches_contract():
    provider=DerivProvider(cfg())
    async def scenario():
        async def valid(payload,expected):
            assert expected=='proposal' and payload['underlying_symbol']=='cryBTCUSD'
            assert payload['duration']==5 and payload['duration_unit']=='m'
            return {'proposal':{'id':'exact','ask_price':'1.00','payout':'1.90'},'echo_req':payload}
        provider.request=valid
        assert await provider.confirmed_payout('BTC/USD','CALL',300)==0.9
        async def bad(payload,expected):
            return {'proposal':{'id':'exact','ask_price':'1.00','payout':'1.85'}}
        provider.request=bad
        with pytest.raises(DataError) as e:
            await provider.confirmed_payout('BTC/USD','CALL',300)
        assert e.value.code=='payout_unavailable'
        async def stale(payload,expected):
            return {'proposal':{'id':'exact','ask_price':'1.00','payout':'1.90'},
                    'echo_req':{'underlying_symbol':'cryETHUSD'}}
        provider.request=stale
        with pytest.raises(DataError):
            await provider.confirmed_payout('BTC/USD','CALL',300)
    asyncio.run(scenario())


def test_operational_binary_contract_rejects_missing_quote():
    provider=DerivProvider(cfg())
    store=Store(':memory:')
    service=Service(cfg(),store,provider,SimpleNamespace(configured=False),
                    providers={'deriv':provider},automatic_sources=True)
    async def scenario():
        async def supports(*a):return None
        provider.supports=supports
        async def payout(*a):
            raise DataError('Proposta indisponível.','payout_unavailable')
        provider.confirmed_payout=payout
        with pytest.raises(DataError) as e:
            await service.ensure_binary_contract(provider,'BTC/USD','CALL',300)
        assert e.value.code=='payout_unavailable'
    asyncio.run(scenario())
    store.close()


def test_api_exposes_only_binary_deriv_and_18_assets():
    c=cfg(database=':memory:')
    with TestClient(create_app(c)) as client:
        state=client.get('/api/state')
        assert state.status_code==200
        data=state.json()
        assert data['available_assets']==list(CRYPTO_BINARY_ASSETS)
        assert len(data['settings']['assets'])==18
        assert [x['key'] for x in data['providers']]==['deriv']
        assert 'Opções binárias cripto' in data['limitation']
        assert client.get('/api/chart?asset=EUR/USD&source=deriv').status_code==422
        assert client.get('/api/chart?asset=BTC/USD&source=coinbase').status_code==422
        assert client.get('/api/instruments?market=forex').json()['total']==0
