import asyncio
from unittest.mock import patch
import pytest
from app.config import Config
from app.deriv import DerivProvider
from app.market import DataError

def test_catalog_concurrent_calls_share_one_request():
    async def run():
        p=DerivProvider(Config());calls=[]
        async def request(payload,expected):
            calls.append(payload);await asyncio.sleep(0)
            return {'active_symbols':[{'underlying_symbol':'frxEURUSD','market':'forex','exchange_is_open':1,'is_trading_suspended':0}]}
        p.request=request
        await asyncio.gather(*(p.check_asset('EUR/USD') for _ in range(8)))
        assert len(calls)==1
        p.catalog_cache=(p.catalog_cache[0]-16,p.catalog_cache[1])
        await p.check_asset('EUR/USD');assert len(calls)==2
    asyncio.run(run())

def test_candles_share_subset_but_not_boundary_or_history():
    async def run():
        p=DerivProvider(Config());calls=[]
        async def request(payload,expected):
            calls.append(payload);await asyncio.sleep(0)
            return {'candles':[dict(epoch=6000-i*60,open=1,high=2,low=.5,close=1.5) for i in range(payload['count']-1,-1,-1)]}
        p.request=request
        with patch('app.deriv.time') as clock:
            clock.time.return_value=6010;clock.monotonic.return_value=10
            batches=await asyncio.gather(*(p.candles('EUR/USD',60,50) for _ in range(4)))
            assert len(calls)==1
            batches[0].clear()
            assert len(await p.candles('EUR/USD',60,2))==2 and len(calls)==1
            clock.time.return_value=6061
            await p.candles('EUR/USD',60,50);assert len(calls)==2
            await p.candles('EUR/USD',60,2,end=6059)
            await p.candles('EUR/USD',60,2,end=6059)
            assert len(calls)==4
            clock.monotonic.return_value=21
            await p.candles('EUR/USD',60,50);assert len(calls)==5
    asyncio.run(run())

def test_global_budget_cooldown_and_recovery():
    async def run():
        p=DerivProvider(Config());calls=[]
        async def transport(payload,expected):calls.append(payload);return {}
        p._request=transport
        with patch('app.deriv.time') as clock:
            clock.monotonic.return_value=100
            for _ in range(90):await p.request({},'candles')
            with pytest.raises(DataError,match='Limite local'):await p.request({},'candles')
            assert len(calls)==90
            clock.monotonic.return_value=161
            await p.request({},'candles');assert len(calls)==91
            async def limited(*args):raise DataError('Limit','rate_limit')
            p._request=limited
            with pytest.raises(DataError):await p.request({},'candles')
            p._request=transport
            with pytest.raises(DataError):await p.request({},'candles')
            clock.monotonic.return_value=222
            await p.request({},'candles');assert len(calls)==92
    asyncio.run(run())
