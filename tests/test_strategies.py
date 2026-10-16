"""Isolated deterministic fixtures for non-SMC strategies."""
import random
import asyncio
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from tests.test_system import Feed, TG
from app.market import Candle
from app.strategies import STRATEGIES

BASE=1800000000

def fixture(seed, strategy):
    random.seed(seed); price=1.1; rows=[]
    for i in range(80):
        if strategy=='ema_pullback': change=.00025+random.uniform(-.00025,.00025)
        else: change=random.uniform(-.0003,.0003)
        opening=price; close=price+change
        high=max(opening,close)+random.uniform(.0001,.0003)
        low=min(opening,close)-random.uniform(.0001,.0003)
        rows.append(Candle(BASE+i*300,opening,high,low,close));price=close
    return rows

def test_strategy_registry_is_explicit_and_manual_default():
    assert {'pa_harami','pa_head_shoulders','pa_breakout_50','fibonacci','scan_all','vwap'} <= set(STRATEGIES)
    assert Config(admin_token='a'*32).validate().strategy=='scan_all'
    try:
        Config(admin_token='a'*32,strategy='unknown').validate()
    except ValueError:
        pass
    else:
        raise AssertionError('Estratégia desconhecida foi aceita')

def test_ema_pullback_valid_almost_valid_and_absent():
    rows=fixture(21885,'ema_pullback');candidate,reason=STRATEGIES['ema_pullback'](rows)
    assert candidate and candidate['direction']=='CALL' and candidate['score']>=85
    broken=rows[:-1]+[Candle(rows[-1].t,rows[-1].o,rows[-1].h,rows[-1].l,rows[-1].o)]
    assert STRATEGIES['ema_pullback'](broken)[0] is None
    absent=[Candle(c.t,1,1.0001,.9999,1) for c in rows]
    assert STRATEGIES['ema_pullback'](absent)[0] is None

def test_bb_reversion_valid_almost_valid_and_absent():
    rows=fixture(1085,'bb_reversion');candidate,reason=STRATEGIES['bb_reversion'](rows)
    assert candidate and candidate['direction']=='PUT' and candidate['score']>=85
    broken=rows[:-1]+[Candle(rows[-1].t,rows[-1].o,rows[-1].h,rows[-1].l,rows[-1].c)]
    # Remove the mandatory reversal direction while preserving the candle evidence.
    last=rows[-1]; broken[-1]=Candle(last.t,last.c,last.h,last.l,last.c+abs(last.c-last.o))
    assert STRATEGIES['bb_reversion'](broken)[0] is None
    absent=[Candle(c.t,1,1.0001,.9999,1) for c in rows]
    assert STRATEGIES['bb_reversion'](absent)[0] is None

def test_range_pivot_valid_almost_valid_and_absent():
    rows=fixture(1085,'range_pivot');candidate,reason=STRATEGIES['range_pivot'](rows)
    assert candidate and candidate['direction']=='PUT' and candidate['score']>=85
    last=rows[-1]; broken=rows[:-1]+[Candle(last.t,last.o,last.h,last.l,last.o)]
    assert STRATEGIES['range_pivot'](broken)[0] is None
    absent=[Candle(c.t,1,1.0001,.9999,1) for c in rows]
    assert STRATEGIES['range_pivot'](absent)[0] is None

def test_legacy_strategy_migrates_to_automatic_without_losing_preferences(tmp_path):
    from app.store import Store
    cfg=Config(database=str(tmp_path/'db'))
    store=Store(cfg.database)
    store.save_settings({'strategy':'ema_pullback','provider':'deriv','assets':['GBP/USD'],
                         'timeframe':'M3','duration_seconds':180})
    store.db.close()
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        state=client.get('/api/state').json()
        assert state['strategy']=='scan_all'
        assert state['settings']['assets']==['GBP/USD']
        assert state['settings']['timeframe']=='M3'
        assert state['settings']['duration_seconds']==180
        assert client.put('/api/strategy',json={'strategy':'ema_pullback'}).status_code==422
        response=client.post('/api/analyze',json={'provider':'deriv','assets':['EUR/USD']})
        assert response.status_code==202 and response.json()['strategy']=='scan_all'
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.get('/api/state').json()['strategy']=='scan_all'

def test_strategy_is_written_to_signal_and_caption(tmp_path):
    async def run():
        from tests.test_system import setup, drain
        service,_=setup(tmp_path);service.select_strategy('ema_pullback')
        service.start(['EUR/USD'],300);await drain(service)
        # This fixture may not produce EMA setup, but the analysis always records the selected family.
        assert service.store.analyses()[0]['strategy']=='ema_pullback'
    asyncio.run(run())
