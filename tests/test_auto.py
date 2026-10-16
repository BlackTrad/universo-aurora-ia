import asyncio
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.market import Candle, DataError
from app.config import Config
from app.strategies import auto
from app.strategies.regime import classify, metrics
from app.strategies.htf import confirm
from tests.test_strategies import fixture

def test_auto_api_persistence_stale_tabs_and_busy(tmp_path):
    from app.main import create_app
    from tests.test_system import Feed, TG
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'api.db'))
    headers={'Authorization':'Bearer '+cfg.admin_token}
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.put('/api/strategy',headers=headers,json={'strategy':'scan_all'}).status_code==200
        assert client.post('/api/analyze',headers=headers,json={'provider':'deriv','assets':['EUR/USD']}).status_code==202
        client.post('/api/analyze/stop',headers=headers,json={})
        service=client.app.state.service
        service.analysis_active=True
        assert client.put('/api/strategy',headers=headers,json={'strategy':'range_pivot'}).status_code==409
        service.analysis_active=False
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.get('/api/state',headers=headers).json()['strategy']=='scan_all'

@pytest.mark.parametrize('htf_status',['disabled'])
def test_service_evidence_filter_delivery_and_cross_mode_duplicates(tmp_path,htf_status):
    from tests.test_system import setup, drain
    from app.strategies import STRATEGIES
    from app.telegram import caption, card
    from unittest.mock import AsyncMock
    async def run():
        service,now=setup(tmp_path)
        rows=fixture(21885,'ema_pullback')
        service.provider.rows=rows; now[0]=rows[-1].t+305
        service.select_strategy('auto')
        base=STRATEGIES['ema_pullback'](rows)[0]
        candidate={**base,'strategy_resolved':'ema_pullback','regime':'tendencial'}
        service.config.confirm_htf=htf_status!='disabled'  # Exercise prepared path; production Config rejects activation.
        report={'status':htf_status,'detail':'H1 '+htf_status,'elapsed_ms':1}
        with patch.dict(STRATEGIES,{'auto':lambda c:(candidate,'')}), patch('app.service.confirm',new=AsyncMock(return_value=report)) as htf:
            service.start(['EUR/USD'],300); await drain(service)
            assert htf.call_count==(htf_status!='disabled')
        signals=service.store.signals()
        if htf_status=='rejected':
            assert signals==[] and service.telegram.calls==0
            return
        s=signals[0]
        assert s['strategy']=='auto' and s['strategy_resolved']=='ema_pullback'
        assert s['evidence']['strategy_resolved']=='ema_pullback'
        assert s['htf']['status']==htf_status and service.telegram.calls==1
        assert 'OPORTUNIDADE ENCONTRADA' not in caption(s) and '💱Ativo:' in caption(s)
        assert card(s).startswith(b'\x89PNG')
        assert 'H1 unavailable' not in caption(s)
        service.config.confirm_htf=False
        service.select_strategy('ema_pullback')
        service.start(['EUR/USD'],300); await drain(service)
        assert len(service.store.signals())==1
        assert service.store.analyses()[0]['checks'][0]['kind']=='duplicate'
    asyncio.run(run())


def test_deriv_h1_wire_interval():
    from app.deriv import DerivProvider
    async def run():
        provider=DerivProvider(Config())
        async def request(payload,expected):
            assert payload['granularity']==3600 and payload['ticks_history']=='frxEURUSD'
            return {'echo_req':payload,'candles':[{'epoch':1800000000,'open':1,'high':1.1,'low':.9,'close':1.05}]}
        provider.request=request
        with pytest.raises(DataError, match='Intervalo'):
            await provider.candles('EUR/USD',3600,80)
    asyncio.run(run())

def regime_fixture(kind, interval=300):
    rows=[]
    for i in range(80):
        close=100+i*i*.01 if kind=='trend' else 100+(i%2)*.01
        width=2 if kind=='trend' else .5
        rows.append(Candle(1800000000//interval*interval+i*interval,close-.1,close+width,close-width,close))
    return rows

@pytest.mark.parametrize('kind,resolved',[('trend','ema_pullback'),('lateral','range_pivot')])
def test_actual_auto_signal_without_mocked_evaluators(kind,resolved,tmp_path):
    from app.strategies.helpers import ema_series
    from tests.test_system import setup, drain
    if kind=='lateral':
        rows=fixture(3151,'range_pivot')
    else:
        rows=regime_fixture('trend')
        last=rows[-1];fast=ema_series([c.c for c in rows],9)[-1]
        rows=rows[:-1]+[Candle(last.t,fast,last.c+.11,fast-.01,last.c+.1)]
    candidate,reason=auto.evaluate(rows)
    assert candidate and candidate['strategy_resolved']==resolved
    assert candidate['score']>=85
    # Same series reflected around a positive baseline must reverse direction.
    reflected=[Candle(c.t,400-c.o,400-c.l,400-c.h,400-c.c) for c in rows]
    reflected_candidate,_=auto.evaluate(reflected)
    assert reflected_candidate and reflected_candidate['direction']!=candidate['direction']
    async def run():
        service,now=setup(tmp_path,rows)
        now[0]=rows[-1].t+305;service.select_strategy('auto')
        service.start(['EUR/USD'],300);await drain(service)
        s=service.store.signals()[0]
        assert s['strategy_resolved']==resolved and s['evidence']['regime_metrics']
        assert service.telegram.calls==1
    asyncio.run(run())

def test_regime_fixtures_and_short_history():
    assert classify(regime_fixture('trend'))=='tendencial'
    assert classify(regime_fixture('flat'))=='lateral'
    rows=regime_fixture('trend')
    # Increase ranges to put the final normalized separation in the dead zone.
    gap=metrics(rows)['gaps'][-1]
    rows=[Candle(c.t,c.o,c.c+gap,c.c-gap,c.c) for c in rows]
    assert .4 <= metrics(rows)['distances'][-1] < .6
    assert classify(rows) is None
    assert classify(rows[:20]) is None
    assert classify([Candle(c.t,100,100,100,100) for c in rows]) is None

@pytest.mark.parametrize('regime,resolved', [('tendencial','ema_pullback'),('lateral','range_pivot'),(None,None)])
def test_exclusive_dispatch_no_fallback(regime,resolved):
    rows=regime_fixture('trend')
    candidate={'reasons':['Fixture'], 'score':85}
    with patch.object(auto,'classify',return_value=regime), patch.object(auto,'evaluate_ema_pullback',return_value=(candidate,'')) as ema, patch.object(auto,'evaluate_range_pivot',return_value=(candidate,'')) as pivot:
        result,_=auto.evaluate(rows)
        assert ema.call_count == (resolved=='ema_pullback')
        assert pivot.call_count == (resolved=='range_pivot')
        if resolved:
            assert result['strategy_resolved']==resolved and result['score']==85
            assert candidate['reasons']==['Fixture']
        else: assert result is None
    with patch.object(auto,'classify',return_value='tendencial'), patch.object(auto,'evaluate_ema_pullback',return_value=(None,'Ausente')), patch.object(auto,'evaluate_range_pivot') as pivot:
        assert auto.evaluate(rows)[0] is None
        pivot.assert_not_called()

@pytest.mark.parametrize('seed,key',[(21885,'ema_pullback'),(1085,'range_pivot'),(1085,'bb_reversion')])
def test_existing_fixtures_route_by_regime_not_by_available_signal(seed,key):
    from app.strategies import STRATEGIES
    rows=fixture(seed,key); regime=classify(rows)
    resolved={'tendencial':'ema_pullback','lateral':'range_pivot'}.get(regime)
    result,reason=auto.evaluate(rows)
    expected=STRATEGIES[resolved](rows)[0] if resolved else None
    assert bool(result)==bool(expected)
    if result:
        assert result['strategy_resolved']==resolved
        assert result['score']==expected['score']
    assert auto.evaluate(rows)==(result,reason)

def test_thresholds_exact():
    from app.strategies import regime
    for distances,growing,expected in [([.1,.2,.6],True,'tendencial'),([.1,.2,.6],False,None),([.39]*3,False,'lateral'),([.4,.2,.2],False,None),([.5]*3,True,None)]:
        with patch.object(regime,'metrics',return_value={'distances':distances,'growing':growing}):
            assert regime.classify([])==expected

def test_htf_gate():
    assert Config(admin_token='a'*32,strategy='auto').validate().confirm_htf is False
    with pytest.raises(ValueError,match='medição real'):
        Config(admin_token='a'*32,confirm_htf=True).validate()

def test_htf_closed_direction_unavailable_and_timeout():
    async def run():
        rows=regime_fixture('trend',3600); cutoff=rows[-1].t+3600
        class Provider:
            name=identity='fixture'
            async def candles(self,asset,interval,count):
                assert asset=='EUR/USD' and interval==3600
                return rows+[Candle(cutoff,100,10000,1,1)]
        p=Provider()
        result=await confirm(p,'EUR/USD','CALL',cutoff,'fixture')
        assert result['status']=='confirmed' and len(result['candles'])==80
        assert (await confirm(p,'EUR/USD','PUT',cutoff,'fixture'))['status']=='rejected'
        assert (await confirm(p,'EUR/USD','CALL',cutoff,'other'))['code']=='source_mismatch'
        async def fail(*args): raise DataError('erro','rate_limit')
        p.candles=fail
        assert (await confirm(p,'EUR/USD','CALL',cutoff,'fixture'))['code']=='rate_limit'
        async def slow(*args): await asyncio.sleep(.1)
        p.candles=slow
        result=await confirm(p,'EUR/USD','CALL',cutoff,'fixture',timeout=.005)
        assert result['status']=='unavailable' and result['code']=='timeout' and result['elapsed_ms']<100
    asyncio.run(run())
