"""Synthetic routing, backoff and cancellation; no live markets or messaging."""
import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.market import CandleBatch, DataError
from app.service import Service
from app.source_routing import next_check, plans
from app.store import Store
from tests.test_system import Feed, TG, fixture
from tests.test_scanning_diagnostics import until


class NativeFeed(Feed):
    timeframes = ['M1', 'M5']

    def __init__(self, key, assets, broken=False):
        super().__init__()
        self.name = key.title()
        self.identity = key + ':synthetic'
        self.assets = assets
        self.requested = []
        self.broken = broken

    async def candles(self, asset, interval=300, count=100):
        self.requested.append((asset, interval))
        if self.broken:
            raise DataError('Synthetic source down', 'connection')
        assert asset in self.assets
        return CandleBatch(self.rows, self.identity)


def sources():
    return {'deriv': NativeFeed('deriv', ['EUR/USD', 'GBP/USD']),
            'binance': NativeFeed('binance', ['BTC/USDT', 'ETH/USDT']),
            'coinbase': NativeFeed('coinbase', ['BTC/USD', 'ETH/USD'])}


def service(store=None):
    feeds = sources()
    cfg = Config(database=':memory:', assets=['EUR/USD'])
    s = Service(cfg, store or Store(':memory:'), feeds['deriv'], TG(), providers=feeds,
                automatic_only=True, automatic_sources=True, clock=lambda: fixture()[-1].t + 305)
    return s, feeds


async def cleanup(s):
    await s.stop_scan()
    await s.price_streams.close()
    if s.tasks:
        await asyncio.gather(*list(s.tasks), return_exceptions=True)
    s.store.db.close()


def test_automatic_defaults_native_symbols_and_persisted_selection():
    s, feeds = service()
    assert s.settings()['source_mode'] == 'automatic'
    assert s.settings()['assets'][:3] == ['EUR/USD', 'BTC/USDT', 'BTC/USD']
    assert s.available_timeframes == ['M1', 'M5']
    assert {p['source_key'] for p in plans(s, s.settings()['assets'], 'M5')} == set(feeds)
    assert s.snapshot()['asset_sources']['BTC/USD'] == 'coinbase'
    s.store.save_settings({**s.settings(),'assets':['DISABLED']})
    assert 1<=len(s.settings()['assets'])<=8
    s.store.save_settings({**s.settings(), 'assets': ['ETH/USD']})
    same, _ = service(s.store)
    assert same.settings()['assets'] == ['ETH/USD']
    with pytest.raises(ValueError, match='automaticamente'):
        s.select_provider('binance')
    s.store.db.close()


def test_preview_visits_all_sources_with_original_identity_even_on_failure():
    async def run():
        s, feeds = service()
        feeds['binance'].broken = True
        selected = ['EUR/USD', 'BTC/USDT', 'BTC/USD']
        try:
            s.start_scan(selected, 300, 'M5', preview=True)
            await until(lambda: not s.scan['active'])
            assert all(feed.requested for feed in feeds.values())
            assert s.telegram.calls == 0 and not s.store.signals()
            analyses = s.store.analyses()
            assert {a['source_key'] for a in analyses} == set(feeds)
            for a in analyses:
                assert a['preview']
                assert a['assets'] == [dict(deriv='EUR/USD', binance='BTC/USDT', coinbase='BTC/USD')[a['source_key']]]
                assert a['configuration']['settings']['provider'] == a['source_key']
            assert s.source_health['binance']['status'] == 'indisponível'
            assert s.source_health['coinbase']['status'] == 'disponível'
            assert s.feed['status'] == 'parcial'
        finally:
            await cleanup(s)
    asyncio.run(run())


def test_automatic_stop_releases_lease_and_does_not_visit_next_source():
    async def run():
        s, feeds = service()
        entered = asyncio.Event()
        async def slow(*args):
            entered.set()
            await asyncio.Event().wait()
        feeds['deriv'].candles = slow
        try:
            s.start_scan(['EUR/USD', 'BTC/USD'], 300, 'M5')
            await entered.wait()
            await s.stop_scan()
            assert not s.scan['active'] and not s.analysis_active
            assert not s.store.busy(s.clock())
            assert not feeds['coinbase'].requested
        finally:
            await cleanup(s)
    asyncio.run(run())


@pytest.mark.parametrize('checks,failed,expected', [
    ([{'code':'market_closed'}], 1, 300),
    ([{'code':'rate_limit'}], 1, 60),
    ([{'code':'connection'}], 1, 30),
])
def test_backoff_is_per_source(checks, failed, expected):
    assert next_check({'status':'erro','checks':checks,'failed_assets':failed}, 1000, 300) == 1000 + expected
    assert next_check({'status':'sem sinal','checks':[]}, 1000, 300) == 1202


def test_production_api_automatic_check_settings_and_no_autostart(tmp_path,monkeypatch):
    monkeypatch.setattr('app.chart.time.time',lambda:fixture()[-1].t+305)
    feeds = sources()
    with patch('app.main.DerivProvider', return_value=feeds['deriv']), \
         patch('app.main.BinanceProvider', return_value=feeds['binance']), \
         patch('app.main.CoinbaseProvider', return_value=feeds['coinbase']):
        with TestClient(create_app(Config(database=str(tmp_path/'db')),telegram=TG(),
                                   clock=lambda: fixture()[-1].t + 305)) as c:
            state = c.get('/api/state').json()
            assert state['source_mode'] == 'automatic'
            assert not state['scan']['active'] and not state['analyses']
            assert all(not f.requested for f in feeds.values())
            assert c.put('/api/connection',json={'provider':'binance'}).status_code == 422
            check = c.post('/api/connection/check',json={})
            assert check.status_code == 200
            assert {r['source_key'] for r in check.json()['sources']} == set(feeds)
            assert c.put('/api/settings',json=state['settings']).status_code == 200
            assert c.get('/api/state').json()['settings']['source_mode'] == 'automatic'
            items = c.get('/api/instruments?q=BTC').json()['items']
            assert all(i['analysis_enabled'] for i in items)
            for asset,key in [('EUR/USD','deriv'),('BTC/USDT','binance'),('BTC/USD','coinbase')]:
                chart=c.get('/api/chart',params={'asset':asset,'timeframe':'M5'})
                assert chart.status_code==200 and chart.json()['source_key']==key
            assert not c.get('/api/state').json()['scan']['active']
