"""Controlled reproductions; no live market query or Telegram delivery."""
import asyncio
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.chart import ChartCache
from app.main import create_app
from app.market import Candle, CandleBatch, DataError
from app.quotes import Quotes
from app.service import Service
from app.store import Store
from app.telegram import Telegram
from tests.test_system import TG


class ChartProvider:
    name = 'Controlled feed'
    identity = 'controlled:original'
    assets = ['EUR/USD', 'BTC/USDT']
    timeframes = ['M1', 'M5']

    async def check_asset(self, asset):
        pass

    async def candles(self, asset, interval, count=100):
        opening = int(time.time() // interval) * interval
        return CandleBatch([Candle(opening - interval, 100, 102, 99, 101),
                            Candle(opening, 101, 103, 100, 102)], self.identity)


def sample_signal(now=None):
    now = time.time() if now is None else now
    entry = int(now // 60) * 60 + 180
    return {'id': 'controlled-signal', 'asset': 'EUR/USD', 'source': 'Controlled',
            'source_key': 'deriv', 'source_identity': 'controlled:original',
            'direction': 'CALL', 'score': 90, 'strategy': 'order_block',
            'created_at': now, 'entry_at': entry, 'expiry_at': entry + 60,
            'duration_seconds': 60, 'timeframe': 'M1', 'state': 'aguardando entrada',
            'result': 'Resultado indisponível', 'settlement_pending': True,
            'telegram': {'status': 'pendente'}, 'evidence': {}}


def test_slow_chart_does_not_block_another_source():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Slow(ChartProvider):
            async def candles(self, *args):
                entered.set()
                await release.wait()
                return await super().candles(*args)

        app = create_app(Config(database=':memory:'), Slow(), TG())
        async with app.router.lifespan_context(app):
            app.state.service.providers['binance'] = ChartProvider()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                slow = asyncio.create_task(client.get('/api/chart?asset=EUR/USD&source=deriv&timeframe=M1'))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    fast = await asyncio.wait_for(client.get('/api/chart?asset=BTC/USDT&source=binance&timeframe=M1'), .3)
                    assert fast.status_code == 200
                    assert fast.json()['source_key'] == 'binance'
                finally:
                    release.set()
                    await slow
    asyncio.run(run())


def test_chart_enforces_one_total_fetch_budget():
    class Slow(ChartProvider):
        async def check_asset(self, asset):
            await asyncio.sleep(.04)

        async def candles(self, *args):
            await asyncio.sleep(.04)
            return await super().candles(*args)

    with patch('app.main.FETCH_TIMEOUT', .06):
        with TestClient(create_app(Config(database=':memory:'), Slow(), TG())) as client:
            assert client.get('/api/chart?asset=EUR/USD&timeframe=M1').status_code == 503


def test_chart_preserves_batch_provenance_after_slicing():
    class Routed(ChartProvider):
        async def candles(self, *args):
            rows = await super().candles(*args)
            return CandleBatch(rows, 'controlled:actual-route')

    with TestClient(create_app(Config(database=':memory:'), Routed(), TG())) as client:
        response = client.get('/api/chart?asset=EUR/USD&timeframe=M1')
        assert response.status_code == 200
        assert response.json()['source_identity'] == 'controlled:actual-route'


def test_chart_refetches_on_new_minute_even_inside_cache_ttl():
    with TestClient(create_app(Config(database=':memory:'), ChartProvider(), TG())) as client:
        with patch('app.main.time.time', return_value=1800000059):
            first = client.get('/api/chart?asset=EUR/USD&timeframe=M1').json()
        with patch('app.main.time.time', return_value=1800000060):
            newer = client.get('/api/chart?asset=EUR/USD&timeframe=M1').json()
        assert newer['data_at'] == first['data_at'] + 60


@pytest.mark.parametrize('field', ['entry_at', 'expiry_at', 'entry_price', 'expiry_price', 'payout_pct'])
def test_manual_numbers_do_not_accept_booleans(field):
    with TestClient(create_app(Config(database=':memory:'), ChartProvider(), TG())) as client:
        service = client.app.state.service
        s = sample_signal(time.time() - 600)
        s.update(state='encerrado', settlement_pending=False)
        service.store.add_signal(s, 'manual-fixture')
        body = {'executed': True, 'broker': 'Declared broker', 'result': 'WIN',
                'entry_at': .5, 'expiry_at': 1.5, 'entry_price': 1.0, 'expiry_price': 1.1,
                'payout_pct': 85}
        body[field] = True
        assert client.post('/api/signals/' + s['id'] + '/manual-confirmation', json=body).status_code == 422
        assert service.store.manual_confirmations() == []


@pytest.mark.parametrize('field', ['stake_brl', 'payout_pct'])
def test_money_settings_do_not_accept_booleans(field):
    with TestClient(create_app(Config(database=':memory:'), ChartProvider(), TG())) as client:
        original = client.app.state.service.settings()
        body = {**original, 'stake_brl': 10, 'payout_pct': 85, field: True}
        assert client.put('/api/settings', json=body).status_code == 422
        assert client.app.state.service.settings() == original


def test_config_accepts_implemented_binance_instrument():
    assert Config(provider='binance', assets=['BTC/USDT']).validate().assets == ['BTC/USDT']


def test_outer_telegram_timeout_keeps_original_destination():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': 123}})

        cfg = Config(database=':memory:', telegram_token='123:controlled', telegram_chat='-100123')
        store = Store(':memory:')
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                tg = Telegram(cfg, client)
                service = Service(cfg, store, ChartProvider(), tg)
                s = sample_signal()
                store.add_signal(s, 'timeout-fixture')
                original_destination = tg.destination_key()

                async def unconfirmed(signal):
                    await asyncio.sleep(1)

                with patch.object(tg, 'send', side_effect=unconfirmed), patch('app.service.SEND_TIMEOUT_SECONDS', .01):
                    await service.deliver(s['id'])
                saved = store.signal(s['id'])
                assert saved['telegram']['status'] == 'incerto'
                assert saved['telegram'].get('destination_key') == original_destination
                cfg.telegram_chat = '-100456'
                result = await tg.send_result({**saved, 'state': 'encerrado', 'settlement_pending': False,
                                               'result': 'WIN'}, {'wins': 1, 'losses': 0, 'ties': 0, 'win_rate_pct': 100})
                assert result.get('code') == 'destination_changed'
                assert calls == []
        finally:
            store.db.close()
    asyncio.run(run())


def test_quote_cache_and_idle_locks_are_bounded():
    class Unavailable(ChartProvider):
        async def check_asset(self, asset):
            raise DataError('Unlisted instrument', 'unsupported_asset')

    async def run():
        quotes = Quotes()
        for index in range(300):
            await quotes.get(Unavailable(), 'instrument-' + str(index))
        assert len(quotes.cache) <= 256
        assert len(quotes.locks) == 0
    asyncio.run(run())


@pytest.mark.parametrize('factory', [ChartCache, Quotes])
def test_same_instrument_requests_reuse_fetch_and_release_lock(factory):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Counted(ChartProvider):
            calls = 0

            async def candles(self, *args):
                self.calls += 1
                entered.set()
                await release.wait()
                return await super().candles(*args)

            async def reference_candle(self, asset, midnight):
                return Candle(midnight - 60, 100, 101, 99, 100)

        cache, provider = factory(), Counted()
        def get():
            return cache.get(provider, 'deriv', 'EUR/USD', 'M1') if isinstance(cache, ChartCache) else cache.get(provider, 'EUR/USD')
        first = asyncio.create_task(get())
        await entered.wait()
        second = asyncio.create_task(get())
        await asyncio.sleep(0)
        release.set()
        a, b = await asyncio.gather(first, second)
        assert a == b
        assert provider.calls == 1
        assert len(cache.locks) == 0
    asyncio.run(run())


@pytest.mark.parametrize('factory', [ChartCache, Quotes])
def test_cancelled_fetch_does_not_keep_lock_or_prevent_recovery(factory):
    async def run():
        entered = asyncio.Event()

        class Interrupted(ChartProvider):
            interrupt = True

            async def check_asset(self, asset):
                if self.interrupt:
                    entered.set()
                    await asyncio.Future()

        cache, provider = factory(), Interrupted()
        def get():
            return cache.get(provider, 'deriv', 'EUR/USD', 'M1') if isinstance(cache, ChartCache) else cache.get(provider, 'EUR/USD')
        task = asyncio.create_task(get())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        provider.interrupt = False
        result = await get()
        assert result['price'] == 102
        assert len(cache.locks) == 0
    asyncio.run(run())


def test_chart_cache_expires_at_timeframe_boundary_and_is_bounded():
    async def run():
        cache = ChartCache(capacity=2)
        provider = ChartProvider()
        with patch('app.chart.time.time', return_value=1800000059):
            first = await cache.get(provider, 'deriv', 'EUR/USD', 'M1')
        with patch('app.chart.time.time', return_value=1800000060):
            newer = await cache.get(provider, 'deriv', 'EUR/USD', 'M1')
            await cache.get(provider, 'deriv', 'GBP/USD', 'M1')
            await cache.get(provider, 'deriv', 'USD/JPY', 'M1')
        assert newer['data_at'] == first['data_at'] + 60
        assert len(cache.cache) == 2
        assert ('controlled:original', 'EUR/USD', 'M1') not in cache.cache
        assert len(cache.locks) == 0
    asyncio.run(run())


def test_quote_reference_caches_stay_bounded_and_evicted_key_is_refetched():
    class Referenced(ChartProvider):
        references = 0

        async def reference_candle(self, asset, midnight):
            self.references += 1
            return Candle(midnight - 60, 100, 101, 99, 100)

    async def run():
        cache, provider = Quotes(), Referenced()
        cache.capacity = 2
        for asset in ('EUR/USD', 'GBP/USD', 'USD/JPY', 'EUR/USD'):
            result = await cache.get(provider, asset)
            assert result['change_pct'] == 2
        assert provider.references == 4
        assert len(cache.cache) == len(cache.references) == 2
        assert len(cache.reference_retries) == 0
    asyncio.run(run())


@pytest.mark.parametrize('source,asset', [('binance', 'BTC/USDT'), ('binance', 'DOGE/USDT'), ('coinbase', 'BTC/USD')])
def test_valid_config_does_not_override_provider_support(source, asset):
    from app.public_crypto import BinanceProvider, CoinbaseProvider
    Config(provider=source, assets=[asset]).validate()
    provider = (BinanceProvider if source == 'binance' else CoinbaseProvider)(None)
    assert provider.symbol(asset)
    with pytest.raises(DataError):
        provider.symbol('ZZZ/USDT')


@pytest.mark.parametrize('source,asset', [('deriv', 'BTC/USDT'), ('deriv', 'EUR/OTC'), ('binance', 'btc/usdt'), ('coinbase', 'BTC/USD<script>')])
def test_config_still_rejects_invalid_source_symbols(source, asset):
    from app.config import ConfigurationError
    with pytest.raises(ConfigurationError):
        Config(provider=source, assets=[asset]).validate()


def test_normal_numeric_money_and_manual_confirmation_remain_supported():
    with TestClient(create_app(Config(database=':memory:'), ChartProvider(), TG())) as client:
        service = client.app.state.service
        original = service.settings()
        response = client.put('/api/settings', json={**original, 'stake_brl': 10, 'payout_pct': 85.5})
        assert response.status_code == 200
        s = sample_signal(time.time() - 600)
        s.update(state='encerrado', settlement_pending=False)
        service.store.add_signal(s, 'numeric-fixture')
        response = client.post('/api/signals/' + s['id'] + '/manual-confirmation', json={
            'executed': True, 'broker': 'Declared broker', 'result': 'WIN',
            'entry_at': s['entry_at'], 'expiry_at': s['expiry_at'],
            'entry_price': 100, 'expiry_price': 101.5, 'payout_pct': 85.5})
        assert response.status_code == 200
        assert len(service.store.manual_confirmations()) == 1
        assert service.store.signal(s['id'])['result'] == 'Resultado indisponível'


def test_inflight_telegram_destination_survives_restart(tmp_path):
    async def run():
        cfg = Config(database=str(tmp_path/'restart.db'), telegram_token='123:controlled', telegram_chat='-100123')
        store = Store(cfg.database)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            tg = Telegram(cfg, client)
            destination = tg.destination_key()
            service = Service(cfg, store, ChartProvider(), tg)
            s = sample_signal()
            store.add_signal(s, 'interrupted-delivery')
            entered = asyncio.Event()

            async def interrupted(signal):
                entered.set()
                await asyncio.Future()

            with patch.object(tg, 'send', side_effect=interrupted):
                task = asyncio.create_task(service.deliver(s['id']))
                await entered.wait()
                assert store.signal(s['id'])['telegram']['destination_key'] == destination
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        store.db.close()
        restarted = Store(cfg.database)
        try:
            restarted.recover(time.time())
            delivery = restarted.signal(s['id'])['telegram']
            assert delivery['status'] == 'incerto'
            assert delivery['destination_key'] == destination
        finally:
            restarted.db.close()
    asyncio.run(run())


def test_cancelled_waiter_cannot_remove_owners_instrument_lock():
    from app.cache_locks import KeyedLocks
    async def run():
        locks = KeyedLocks()
        entered, release, third_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def owner():
            async with locks.hold('instrument'):
                entered.set()
                await release.wait()
        async def follower():
            async with locks.hold('instrument'):
                third_entered.set()
        first = asyncio.create_task(owner())
        await entered.wait()
        second = asyncio.create_task(follower())
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):await second
        third = asyncio.create_task(follower())
        await asyncio.sleep(0)
        assert len(locks) == 1 and not third_entered.is_set()
        release.set()
        await asyncio.gather(first, third)
        assert third_entered.is_set() and len(locks) == 0
    asyncio.run(run())


def test_chart_timeout_includes_waiting_for_same_instrument():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Slow(ChartProvider):
            async def candles(self, *args):
                entered.set()
                await release.wait()
                return await super().candles(*args)
        app = create_app(Config(database=':memory:'), Slow(), TG())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                owner = asyncio.create_task(client.get('/api/chart?asset=EUR/USD&timeframe=M1'))
                await entered.wait()
                try:
                    with patch('app.main.FETCH_TIMEOUT', .03):
                        follower = await client.get('/api/chart?asset=EUR/USD&timeframe=M1')
                    assert follower.status_code == 503
                finally:
                    release.set()
                    assert (await owner).status_code == 200
    asyncio.run(run())
