"""Demand-driven public streams. No tasks or connections on construction."""
import asyncio
from collections import defaultdict,deque
from .live_price import point_after, validate_point
from .market import DataError
from .logging_setup import event


class PriceStreams:
    def __init__(self, providers, clock, on_point, on_gap, *, sleep=asyncio.sleep):
        self.providers, self.clock = providers, clock
        self.on_point, self.on_gap = on_point, on_gap
        self.tasks = {}; self.latest = {}; self.status = {}
        self.lock = asyncio.Lock(); self.closed = False
        self.sleep=sleep;self.connection_times=defaultdict(deque)

    async def sync(self, wanted):
        async with self.lock:
            if self.closed: return
            wanted = {key for key in wanted if callable(getattr(self.providers.get(key[0]), 'stream_prices', None))}
            retired = [self.tasks.pop(key) for key in list(self.tasks) if key not in wanted]
            for task in retired: task.cancel()
            if retired: await asyncio.gather(*retired, return_exceptions=True)
            for key in list(self.latest):
                if key not in wanted: self.latest.pop(key, None)
            for key in wanted:
                if key not in self.tasks or self.tasks[key].done():
                    self.tasks[key] = asyncio.create_task(self.run(key))

    async def run(self, key):
        provider = self.providers[key[0]]; asset = key[1]; delay = 2
        while True:
            attempts=self.connection_times[key[0]];now=self.clock()
            while attempts and now-attempts[0]>=60:attempts.popleft()
            if len(attempts)>=24:
                await self.sleep(max(.1,attempts[0]+60-now));continue
            attempts.append(now)
            self.status[key] = {'state': 'conectando', 'checked_at': self.clock()}
            iterator = None
            try:
                iterator = provider.stream_prices((asset,))
                while True:
                    point = await asyncio.wait_for(anext(iterator), 12)
                    validate_point(point, asset=asset, identity=provider.identity, now=self.clock())
                    prior = self.latest.get(key)
                    if prior is not None and not point_after(point, prior): continue
                    self.latest[key] = point
                    self.status[key] = {'state': 'recebendo', 'at': point.at, 'checked_at': self.clock()}
                    delay = 2
                    await self.on_point(key, point)
            except asyncio.CancelledError:
                self.latest.pop(key, None)
                self.status[key] = {'state': 'parado', 'checked_at': self.clock()}
                raise
            except Exception as error:
                self.latest.pop(key, None)
                code = getattr(error, 'code', 'stream_disconnected')
                timing=getattr(error,'price_timing',None)
                detail=str(error) if isinstance(error,DataError) else 'Fluxo de preços interrompido.'
                self.status[key] = {'state': 'reconectando', 'code': code, 'checked_at': self.clock(),
                                    'detail':detail, **({'price_timing':timing} if timing else {})}
                event('price_stream.interrupted',source_key=key[0],asset=asset,code=code,
                      detail=detail,price_timing=timing)
                await self.on_gap(key, code)
                if isinstance(error, DataError) and code in ('rate_limit', 'market_closed'): delay = max(delay, 60)
            finally:
                if iterator is not None:
                    await iterator.aclose()
            await self.sleep(delay)
            delay = min(30, delay*2)

    async def close(self):
        async with self.lock:
            self.closed = True
            tasks = list(self.tasks.values()); self.tasks.clear(); self.latest.clear()
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
