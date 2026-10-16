import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
from app.config import Config, ConfigurationError
from app.deriv import DerivProvider
from app.market import Candle, CandleBatch, DataError
from app.main import create_app
from app.source_routing import FOREX_ASSETS, CRYPTO_BINARY_ASSETS, assets_for_source, plans
from app.service import Service
from app.store import Store
from tests.test_system import fixture

EXPECTED_ASSETS = ('EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CAD', 'USD/CHF', 'NZD/USD')


def test_forex_allowlist_remains_legacy_but_production_defaults_to_binary_crypto(tmp_path):
    with patch.object(config_module, 'ROOT', tmp_path), patch.dict(os.environ, {}, clear=True):
        config = Config.load()

    assert FOREX_ASSETS == EXPECTED_ASSETS
    assert config.assets == list(CRYPTO_BINARY_ASSETS)
    assert config.asset_scope == 'crypto_binary'
    assert len(config.assets) == 18


def test_production_scope_rejects_crypto_pairs():
    with pytest.raises(ConfigurationError):
        Config(asset_scope='forex', assets=['BTC/USD']).validate()
    with pytest.raises(ConfigurationError):
        Config(asset_scope='forex', assets=['BTC/USDT']).validate()


def test_deriv_uses_forex_symbols_and_rejects_crypto_assets():
    provider = DerivProvider(Config(asset_scope='forex', assets=['EUR/USD']))

    assert provider.symbol('EUR/USD') == 'frxEURUSD'
    with pytest.raises(DataError):provider.symbol('BTC/USD')
    with pytest.raises(DataError):
        provider.symbol('BTC/USDT')


def test_forex_scope_routes_only_to_deriv():
    class Provider:
        def __init__(self, assets, binary=False):
            self.assets = assets
            self.timeframes = ['M1', 'M5']
            if binary:
                self.supports = lambda *args: None

    config = Config(asset_scope='forex', assets=list(EXPECTED_ASSETS))
    service = type('ServiceStub', (), {
        'config': config,
        'provider_key': 'deriv',
        'automatic_sources': True,
        'providers': {
            'deriv': Provider(list(EXPECTED_ASSETS), binary=True),
            'coinbase': Provider(['BTC/USD', 'ETH/USD']),
            'binance': Provider(['BTC/USDT']),
        },
    })()

    assert assets_for_source(service, 'deriv') == list(EXPECTED_ASSETS)
    assert assets_for_source(service, 'coinbase') == []
    assert assets_for_source(service, 'binance') == []
    assert plans(service, ['EUR/USD', 'BTC/USD'], 'M5') == [
        {'source_key': 'deriv', 'assets': ['EUR/USD']}
    ]


def test_production_api_exposes_only_forex_provider_and_assets():
    config=Config(database=':memory:',asset_scope='forex',assets=['EUR/USD'])

    with TestClient(create_app(config)) as client:
        snapshot=client.get('/api/state').json()
        assert [provider['key'] for provider in snapshot['providers']]==['deriv']
        assert snapshot['available_assets']==list(EXPECTED_ASSETS)
        assert client.get('/api/instruments?market=crypto').json()['total']==0
        assert client.get('/api/chart?asset=BTC/USD&source=coinbase&timeframe=M1').status_code==422


def test_direct_service_scan_cannot_use_a_crypto_spot_provider():
    class SpotProvider:
        name = 'Coinbase'
        ready = True
        assets = ['BTC/USD']
        timeframes = ['M1', 'M5']

    config = Config(database=':memory:', asset_scope='forex', assets=['EUR/USD'])
    store = Store(':memory:')
    deriv = DerivProvider(config)
    service = Service(config, store, deriv, SimpleNamespace(configured=False), providers={
        'deriv': deriv, 'coinbase': SpotProvider(),
    }, automatic_only=True, automatic_sources=True)

    with pytest.raises(ValueError, match='Somente a fonte Forex Deriv'):
        service.start(['EUR/USD'], 300, 'M5', source_key='coinbase')

    store.close()


def test_forex_binary_gate_rejects_multiplier_contracts_and_unconfirmed_payout():
    config = Config(database=':memory:', asset_scope='forex', assets=['EUR/USD'])
    provider = DerivProvider(config)

    async def request(payload, expected):
        assert payload == {'contracts_for': 'frxEURUSD'}
        assert expected == 'contracts_for'
        return {'contracts_for': {'available': [
            {'underlying_symbol': 'frxEURUSD', 'contract_type': 'MULTUP', 'barriers': 0,
             'min_contract_duration': '0', 'max_contract_duration': '0'},
            {'underlying_symbol': 'frxEURUSD', 'contract_type': 'MULTDOWN', 'barriers': 0,
             'min_contract_duration': '0', 'max_contract_duration': '0'},
        ]}}

    provider.request = request
    store = Store(':memory:')
    service = Service(config, store, provider, SimpleNamespace(configured=False),
                      providers={'deriv': provider}, automatic_sources=True)

    with pytest.raises(DataError) as error:
        asyncio.run(service.ensure_binary_contract(provider, 'EUR/USD', 'CALL', 300))

    assert error.value.code == 'contract_unsupported'

    async def standard_contract(payload, expected):
        return {'contracts_for': {'available': [
            {'underlying_symbol': 'frxEURUSD', 'contract_type': 'CALL', 'barriers': 0,
             'min_contract_duration': '1m', 'max_contract_duration': '1h'},
        ]}}

    provider.contract_cache.clear()
    provider.request = standard_contract
    with pytest.raises(DataError) as payout_error:
        asyncio.run(service.ensure_binary_contract(provider, 'EUR/USD', 'CALL', 300))

    assert payout_error.value.code == 'payout_unavailable'
    store.close()


def test_operational_analysis_blocks_before_ai_when_forex_contract_is_missing():
    rows = fixture()

    class Broker:
        name = 'Deriv'
        identity = 'deriv:forex:public'
        assets = list(EXPECTED_ASSETS)
        timeframes = ['M1', 'M5']

        def __init__(self):
            self.contract_checks = 0

        async def candles(self, asset, interval=300, count=100):
            assert interval == 300
            return CandleBatch(rows, self.identity)

        async def supports(self, asset, direction, duration):
            self.contract_checks += 1
            raise DataError('Deriv não confirmou contrato CALL/PUT.', 'contract_unsupported')

    async def run():
        now = [rows[-1].t + 305]
        provider = Broker()
        config = Config(database=':memory:', asset_scope='forex', assets=['EUR/USD'], strategy='candlestick_sr')
        store = Store(':memory:')
        service = Service(config, store, provider, SimpleNamespace(configured=False),
                          providers={'deriv': provider}, automatic_sources=True,
                          clock=lambda: now[0])

        service.start(['EUR/USD'], 300, 'M5')
        await service.current_analysis_task

        assert provider.contract_checks == 1
        assert store.signals() == []
        assert any(check.get('code') == 'contract_unsupported'
                   for analysis in store.analyses() for check in analysis['checks'])
        assert service.ai_status['last_decision'] is None
        store.close()

    asyncio.run(run())
