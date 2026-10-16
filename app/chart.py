"""Bounded public chart cache; independent instruments never share a fetch lock."""
from collections import OrderedDict
import time

from .cache_locks import KeyedLocks
from .market import DataError, valid_candle
from .timeframes import TIMEFRAMES


class ChartCache:
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.cache = OrderedDict()
        self.locks = KeyedLocks()

    async def get(self, provider, source_key, asset, timeframe):
        interval = TIMEFRAMES[timeframe]
        key = (getattr(provider, 'identity', source_key), asset, timeframe)
        async with self.locks.hold(key):
            cached = self.cache.get(key)
            now = time.time()
            bucket = int(now // interval)
            if cached and time.monotonic() - cached[0] < 15 and cached[1] == bucket:
                self.cache.move_to_end(key)
                return cached[2]
            market_status = 'open'
            if hasattr(provider, 'check_asset'):
                try:
                    await provider.check_asset(asset)
                except DataError as error:
                    if error.code != 'market_closed':
                        raise
                    market_status = 'closed'
            rows = await provider.candles(asset, interval, 100)
            now = time.time()
            if not rows or any(not valid_candle(c) or type(c.t) is not int or c.t <= 0
                               or c.t % interval or c.t > now for c in rows):
                raise DataError('Candles inválidos para o gráfico.')
            if any(b.t <= a.t for a, b in zip(rows, rows[1:])):
                raise DataError('Candles fora de ordem.')
            # Preserve request provenance before converting the batch to a slice.
            identity = getattr(rows, 'source_identity', getattr(provider, 'identity', provider.name))
            rows = rows[-70:]
            age = max(0, now - (rows[-1].t + interval))
            status = 'closed' if market_status == 'closed' else 'stale' if age > interval + 30 else 'live'
            payload = {'asset': asset, 'timeframe': timeframe, 'source_key': source_key,
                       'source': provider.name, 'source_identity': identity,
                       'candles': [c.json() for c in rows], 'status': status,
                       'received_at': now, 'data_at': rows[-1].t, 'price': rows[-1].c,
                       'change_pct': round(100 * (rows[-1].c / rows[0].o - 1), 4),
                       'change_basis': 'Variação sobre a abertura da primeira vela exibida; não é variação diária.'}
            # A fetch spanning a boundary cannot cache the previous bucket as current.
            self.cache[key] = (time.monotonic(), bucket, payload)
            self.cache.move_to_end(key)
            while len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
            return payload
