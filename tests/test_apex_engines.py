"""Deterministic strategy fixtures; they do not measure market performance."""
import random
from dataclasses import replace
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.market import Candle
from app.strategies import STRATEGIES, scan_all

from app.config import Config
from app.main import create_app
from tests.test_system import Feed, TG

BASE=1800000000

def rows(overrides=None):
    result=[Candle(BASE+i*60,100+(i%3-1)*.05,100.6,99.4,100+(i%3-1)*.04) for i in range(85)]
    for index,ohlc in (overrides or {}).items():result[index]=Candle(BASE+index*60,*ohlc)
    return result

def random_rows(seed):
    rng=random.Random(seed);price=100.;change=0.;drift=rng.choice((0,0,.06,-.06));result=[]
    for i in range(85):
        change=.75*change+.25*rng.gauss(drift,.28)
        close=price+change
        low=min(price,close)-rng.uniform(.03,.38)
        high=max(price,close)+rng.uniform(.03,.38)
        result.append(Candle(BASE+i*60,price,high,low,close))
        price=close
    return result

@pytest.mark.parametrize('strategy,candles',[
    ('cci',rows({83:(100,100.1,97.9,98),84:(98.4,100.6,98.3,100.5)})),
    ('candlestick_sr',rows({83:(100.1,100.2,99.4,99.5),84:(99.3,100.4,99.3,100.25)})),
    ('fibonacci',rows({54:(99.8,100,95,96),73:(105,106,104.5,105.5),84:(101,103,100.5,102.7)})),
    ('consolidation',rows({84:(100,101.8,99.9,101.6)})),
    ('rsi_divergence',random_rows(132)),('heikin_ashi',random_rows(27)),('ichimoku',random_rows(539)),
    ('stochastic',None),
])
def test_new_engines_confirm_closed_call(strategy,candles):
    if candles is None:
        candles=rows()
        candles=[Candle(c.t,100,100.5,99.5,100) for c in candles]
        for i in range(81,84):candles[i]=Candle(BASE+i*60,98.3,98.5,98,98.2)
        candles[84]=Candle(BASE+84*60,98.15,98.9,98,98.8)
    candidate,reason=STRATEGIES[strategy](candles)
    assert candidate is not None,reason
    assert candidate['direction'] in ('CALL','PUT') and candidate['score']>=85
    assert candidate['setup_candle']==candles[-1].t
    assert STRATEGIES[strategy](candles[:20])[0] is None




def test_volume_dependent_engines_refuse_ohlc_only():
    for strategy in ('vwap','sr_volume_breakout'):
        candidate,reason=STRATEGIES[strategy](rows())
        assert candidate is None and 'Volume real indisponível' in reason
    with_volume=[replace(c,v=100) for c in rows()]
    with_volume[84]=Candle(BASE+84*60,100,101.4,99.9,101.3,200)
    candidate,reason=STRATEGIES['sr_volume_breakout'](with_volume)
    assert candidate is not None,reason
    assert candidate['direction']=='CALL'
    with_volume[84]=Candle(BASE+84*60,100.1,101,99.8,100.8,100)
    candidate,reason=STRATEGIES['vwap'](with_volume)
    assert candidate is not None,reason
    assert candidate['direction']=='CALL'




def test_all_new_strategies_can_be_selected_through_api(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as client:
        for key in ('ema_macd','rsi_divergence','stochastic','cci','consolidation','ichimoku',
                    'fibonacci','heikin_ashi','candlestick_sr',
                    'sr_volume_breakout','vwap','scan_all'):
            result=client.put('/api/strategy',json={'strategy':key})
            assert result.status_code==(200 if key=='scan_all' else 422),(key,result.text)
            assert client.get('/api/state').json()['strategy']=='scan_all'


def test_scan_all_persists_resolved_strategy_and_rejects_duplicate(tmp_path):
    import asyncio
    from app.service import Service
    from app.store import Store
    from tests.manual_fixtures import fixture as manual_example
    from tests.test_system import drain
    candles=manual_example('pa_hammer');now=[candles[-1].t+305]
    class Source(Feed):
        async def candles(self,asset,interval=300,count=100):return candles
    store=Store(str(tmp_path/'signals.db'))
    service=Service(Config(database=str(tmp_path/'signals.db')),store,Source(),TG(),clock=lambda:now[0],automatic_only=True)
    async def run():
        service.start(['EUR/USD'],300,'M5');await drain(service)
        signal=store.signals()[0]
        assert signal['strategy']=='scan_all' and signal['manual']
        assert signal['timeframe']=='M5' and signal['duration_seconds']==300
        service.start(['EUR/USD'],300,'M5');await drain(service)
        assert len(store.signals())==1
        assert store.analyses()[0]['checks'][0]['kind']=='duplicate'
    asyncio.run(run());saved=store.signals()[0];store.db.close()
    reopened=Store(str(tmp_path/'signals.db'));reopened.recover(now[0])
    assert reopened.signal(saved['id'])['evidence']==saved['evidence']
    assert reopened.signal(saved['id'])['entry_at']==saved['entry_at']
    assert len(reopened.events(saved['id']))>=1
    reopened.db.close()

@pytest.mark.parametrize('strategy,candles',[
    ('cci',rows({83:(100,100.1,97.9,98),84:(98.4,100.6,98.3,100.5)})),
    ('candlestick_sr',rows({83:(100.1,100.2,99.4,99.5),84:(99.3,100.4,99.3,100.25)})),
    ('fibonacci',rows({54:(99.8,100,95,96),73:(105,106,104.5,105.5),84:(101,103,100.5,102.7)})),
    ('consolidation',rows({84:(100,101.8,99.9,101.6)})),
])
def test_direction_reverses_when_prices_are_mirrored(strategy,candles):
    mirrored=[Candle(c.t,200-c.o,200-c.l,200-c.h,200-c.c) for c in candles]
    candidate,reason=STRATEGIES[strategy](mirrored)
    assert candidate is not None,reason
    assert candidate['direction']=='PUT'


def test_deriv_volume_is_optional_and_invalid_volume_rejected():
    import asyncio
    from app.deriv import DerivProvider
    from app.market import DataError, valid_candle
    async def run():
        provider=DerivProvider(Config())
        value=[None]
        async def request(payload,expected):
            candle={'epoch':BASE,'open':100,'high':101,'low':99,'close':100}
            if value[0] is not None:candle['volume']=value[0]
            return {'candles':[candle]}
        provider.request=request
        assert (await provider.candles('EUR/USD',60))[0].v is None
        value[0]=123
        assert (await provider.candles('EUR/USD',60))[0].v==123
        value[0]=True
        with pytest.raises(DataError):await provider.candles('EUR/USD',60)
        value[0]=-1
        with pytest.raises(DataError):await provider.candles('EUR/USD',60)
        assert not valid_candle(Candle(BASE,100,101,99,100,float('nan')))
    asyncio.run(run())


def test_frontend_has_no_manual_strategy_selector():
    from pathlib import Path
    page=(Path(__file__).resolve().parents[1]/'frontend-src/src/routes/index.tsx').read_text(encoding='utf-8')
    assert 'id="strategy"' not in page
    assert 'id="applyStrategy"' not in page
    assert "api('/analyze'" in page
    assert '/scan/start' in page
    assert 'supabase' not in page
