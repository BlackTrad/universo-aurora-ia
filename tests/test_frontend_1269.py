"""Route and M1/M5 regressions. Synthetic candles verify software, not efficacy."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.chart import ChartCache
from app.config import Config
from app.main import create_app
from app.market import Candle, CandleBatch, DataError
from tests.test_system import Feed, TG


def test_pages_do_not_launch_analysis_or_change_settings(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path / 'aurora.db')), Feed(), TG())) as client:
        before = client.get('/api/state').json()
        for route in ('/', '/configuracoes', '/aprendizado', '/analise'):
            page = client.get(route)
            assert page.status_code == 200
            assert '/static/assets/' in page.text
        after = client.get('/api/state').json()
        assert before['settings'] == after['settings']
        assert not after['scan']['active']
        assert not after['analysis_active']
        assert not after['analyses'] and not after['signals']


def test_chart_m1_m5_cache_separates_periods_instruments_and_provenance(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_030.0)
    class Source:
        name = 'Synthetic chart'

        def __init__(self, identity, offset=0):
            self.identity, self.offset, self.calls = identity, offset, []

        async def candles(self, asset, interval, count):
            self.calls.append((asset, interval, count))
            last = int(time.time() // interval) * interval - interval
            # A genuine gap stays a gap; the chart must not fill it.
            return CandleBatch([
                Candle(last - i * interval, 100 + self.offset, 102 + self.offset,
                       99 + self.offset, 101 + self.offset)
                for i in range(7, -1, -1) if i != 3
            ], self.identity)

    async def exercise():
        cache = ChartCache()
        binance, coinbase = Source('binance:test', 10), Source('coinbase:test', 20)
        minute = await cache.get(binance, 'binance', 'BTC/USDT', 'M1')
        five = await cache.get(binance, 'binance', 'BTC/USDT', 'M5')
        usd = await cache.get(coinbase, 'coinbase', 'BTC/USD', 'M1')
        other = await cache.get(binance, 'binance', 'ETH/USDT', 'M1')
        await cache.get(binance, 'binance', 'BTC/USDT', 'M1')
        assert len(binance.calls) == 3 and len(coinbase.calls) == 1
        for result, interval, identity in ((minute, 60, 'binance:test'),
                                          (five, 300, 'binance:test'),
                                          (usd, 60, 'coinbase:test')):
            times = [c['t'] for c in result['candles']]
            assert len(times) == len(set(times)) == 7
            assert all(t % interval == 0 for t in times)
            assert all(t + interval <= result['received_at'] for t in times)
            assert set(b - a for a, b in zip(times, times[1:])) == {interval, 2 * interval}
            assert result['source_identity'] == identity
        assert minute['price'] == 111 and usd['price'] == 121
        assert other['asset'] == 'ETH/USDT'
        assert minute['timeframe'] == 'M1' and five['timeframe'] == 'M5'

    asyncio.run(exercise())


@pytest.mark.parametrize('timeframe,interval', [('M1', 60), ('M5', 300)])
@pytest.mark.parametrize('failure', ['future', 'duplicate', 'wrong_period', 'invalid_ohlc'])
def test_chart_rejects_future_duplicate_misaligned_and_invalid_candles(timeframe, interval, failure):
    class Source:
        name = identity = 'Synthetic only'

        async def candles(self, *args):
            last = int(time.time() // interval) * interval - interval
            rows = [Candle(last - interval, 100, 102, 99, 101),
                    Candle(last, 100, 102, 99, 101)]
            if failure == 'future':
                rows.append(Candle(last + 2 * interval, 100, 102, 99, 101))
            elif failure == 'duplicate':
                rows.append(rows[-1])
            elif failure == 'wrong_period':
                rows[-1] = Candle(last + 1, 100, 102, 99, 101)
            else:
                rows[-1] = Candle(last, 100, 98, 99, 101)
            return rows

    with pytest.raises(DataError):
        asyncio.run(ChartCache().get(Source(), 'deriv', 'EUR/USD', timeframe))
