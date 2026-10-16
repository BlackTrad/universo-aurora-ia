"""Synthetic regression tests; no real trades, messages or performance claims."""
import asyncio
import json
import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.deriv import DerivProvider
from app.strategies import scan_all as evaluate
from app.main import create_app
from app.market import Candle, DataError, validate
from app.service import Service
from app.store import Store
from app.timeframes import TIMEFRAMES, max_age, FETCH_TIMEOUT, CONTRACT_TIMEOUT
from app.telegram import caption
from tests.test_system import Feed,TG,fixture,drain,setup

async def until(predicate):
    # Profile computation runs in a real worker thread. A count of zero-delay
    # yields can expire before the scheduler gives that thread CPU time.
    deadline=asyncio.get_running_loop().time()+3
    while asyncio.get_running_loop().time()<deadline:
        if predicate(): return
        await asyncio.sleep(.001)
    raise AssertionError('Condition not reached')


@pytest.mark.parametrize('timeframe,interval',TIMEFRAMES.items())
def test_native_period_signal_scheduling_and_duplicate_guard(tmp_path,timeframe,interval):
    async def run():
        source=fixture()
        base=1800000000//interval*interval
        rows=[Candle(base+i*interval,c.o,c.h,c.l,c.c) for i,c in enumerate(source)]
        clock=[rows[-1].t+interval+5]
        calls=[]
        provider=DerivProvider(Config(strategy='candlestick_sr'))
        async def request(payload,expected):
            calls.append(payload)
            if expected=='active_symbols':return {'active_symbols':[{'underlying_symbol':'frxEURUSD','market':'forex','exchange_is_open':1,'is_trading_suspended':0}]}
            if expected=='contracts_for':return {'contracts_for':{'available':[{'underlying_symbol':'frxEURUSD','barriers':0,'contract_type':'CALL','min_contract_duration':'1m','max_contract_duration':'1h'}]}}
            assert payload['granularity']==interval
            return {'candles':[{'epoch':r.t,'open':r.o,'high':r.h,'low':r.l,'close':r.c} for r in rows]}
        provider.request=request
        store=Store(':memory:');service=Service(Config(strategy='candlestick_sr'),store,provider,TG(),clock=lambda:clock[0])
        service.start(['EUR/USD'],300,timeframe);await drain(service)
        signal=store.signals()[0]
        assert signal['entry_at']%60==0 and signal['entry_at']-clock[0]>=60
        assert signal['expiry_at']==signal['entry_at']+300
        assert signal['timeframe']==timeframe and signal['evidence']['interval_seconds']==interval
        assert signal['score']==__import__('app.strategies',fromlist=['STRATEGIES']).STRATEGIES['candlestick_sr'](source)[0]['score']
        assert 'OPORTUNIDADE ENCONTRADA' not in caption(signal) and '💱Ativo:' in caption(signal)
        service.start(['EUR/USD'],300,timeframe);await drain(service)
        assert len(store.signals())==1
        assert store.analyses()[0]['checks'][0]['kind']=='duplicate'
        with pytest.raises(DataError):validate(rows,clock[0]+max_age(timeframe,service.config)+1,max_age(timeframe,service.config),interval)
        store.save_settings({'timeframe':timeframe})
        assert service.settings()['timeframe']==timeframe
        store.db.close()
    asyncio.run(run())


def test_session_waits_for_new_candle_and_stop_preserves_emitted_signal(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        assert service.start_scan(['EUR/USD'],300,'M5')
        assert service.start_scan(['EUR/USD'],300,'M5') is None
        assert service.start(['EUR/USD'],300) is None
        await until(lambda:service.scan['cycles']==1)
        assert len(service.store.signals())==1
        assert service.scan['next_run_at']==(int(clock[0]//300)+1)*300+2
        assert not service.analysis_active and service.scan['active']
        for _ in range(30): await asyncio.sleep(0)
        assert len(service.store.analyses())==1
        with pytest.raises(RuntimeError):service.select_strategy('auto')
        sid=service.store.signals()[0]['id']
        await service.stop_scan();await drain(service)
        assert not service.scan['active'] and not service.store.busy(clock[0])
        assert service.store.signal(sid)['state']=='aguardando entrada'
        service.provider.settlement=[Candle(service.store.signal(sid)['entry_at'],100,101,99,100),Candle(service.store.signal(sid)['expiry_at'],101,102,100,101)]
        clock[0]=service.store.signal(sid)['expiry_at']+70
        await service.tick();await drain(service)
        assert service.store.signal(sid)['result']=='WIN'
    asyncio.run(run())


@pytest.mark.parametrize('immediate',[True,False])
def test_stop_then_restart_never_leaves_analysis_lease(tmp_path,immediate):
    async def run():
        service,clock=setup(tmp_path)
        entered=asyncio.Event()
        async def slow(*args):entered.set();await asyncio.Event().wait()
        service.provider.candles=slow
        service.start_scan(['EUR/USD'],300,'M1')
        if not immediate:await entered.wait()
        await service.stop_scan();await drain(service)
        assert not service.scan['active'] and not service.analysis_active and not service.store.busy(clock[0])
        service.provider.candles=Feed().candles
        service.start(['EUR/USD'],300);await drain(service)
        assert len(service.store.signals())==1
    asyncio.run(run())


def test_manual_stop_before_analysis_coroutine_starts(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300)
        assert await service.stop_analysis()
        await drain(service)
        assert not service.store.busy(clock[0]) and not service.analysis_active
        assert service.store.analyses()[0]['status']=='interrompida'
    asyncio.run(run())


def test_start_during_stop_cannot_spawn_new_scan_or_cancel_wrong_work(tmp_path):
    async def run():
        service,clock=setup(tmp_path);entered=asyncio.Event();cancelled=asyncio.Event();release=asyncio.Event()
        async def slow(*args):
            entered.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set();await release.wait();raise
        service.provider.candles=slow
        service.start_scan(['EUR/USD'],300,'M5');await entered.wait()
        stopping=asyncio.create_task(service.stop_scan());await cancelled.wait()
        assert service.start_scan(['EUR/USD'],300,'M1') is None
        assert service.start(['EUR/USD'],300) is None
        release.set();await stopping;await drain(service)
        assert not service.scan_stopping and not service.store.busy(clock[0])
    asyncio.run(run())


def test_data_failure_backoff_and_progress_are_explained(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.provider.error='Tempo limite da Deriv excedido.'
        service.start_scan(['EUR/USD'],300,'M5')
        await until(lambda:service.scan['cycles']==1)
        assert service.scan['next_run_at']==clock[0]+30
        a=service.store.analyses()[0]
        assert a['status']=='erro' and a['checks'][0]['kind']=='data_error'
        assert a['checks'][0]['stage']=='consultando dados'
        await service.stop_scan();await drain(service)
    asyncio.run(run())


def test_session_repeats_after_new_close_without_parallel_requests(tmp_path):
    async def run():
        service,clock=setup(tmp_path);calls=[];real_sleep=asyncio.sleep
        async def provider(asset,*args):
            calls.append(asset)
            return [Candle(r.t,100,101,99,100) for r in fixture()]
        service.provider.candles=provider
        async def virtual_sleep(delay):
            if delay:clock[0]+=delay
            if service.scan['cycles']>=2:service.scan['active']=False
            await real_sleep(0)
        with patch('app.service.asyncio.sleep',side_effect=virtual_sleep):
            service.start_scan(['EUR/USD'],300,'M5')
            await service.scan_task
        assert len(calls)==2 and service.scan['cycles']==2
        assert not service.analysis_active and not service.store.busy(clock[0])
    asyncio.run(run())


def test_signal_fetch_budget_excludes_contract_requests(tmp_path):
    async def run():
        service,_=setup(tmp_path);timeouts=[];real_wait_for=asyncio.wait_for
        async def supports(*args):raise AssertionError('Unexpected contract request')
        service.provider.supports=supports
        async def wait(coro,timeout):timeouts.append(timeout);return await real_wait_for(coro,timeout)
        with patch('app.service.asyncio.wait_for',side_effect=wait):
            service.start(['EUR/USD'],300);await drain(service)
        assert FETCH_TIMEOUT in timeouts and CONTRACT_TIMEOUT not in timeouts
        assert FETCH_TIMEOUT>16 and CONTRACT_TIMEOUT>8
        assert len(service.store.signals())==1
    asyncio.run(run())


@pytest.mark.parametrize('rows,code',[
    ([], 'contract_unsupported'),
    ([{'contract_type':'CALL','underlying_symbol':'frxEURUSD','barriers':0}], 'contract_duration_unknown'),
    ([{'contract_type':'CALL','underlying_symbol':'frxEURUSD','barriers':0,'min_contract_duration':'15m','max_contract_duration':'1h'}], 'contract_duration')])
def test_distinct_contract_diagnostics(rows,code):
    async def run():
        provider=DerivProvider(Config(strategy='candlestick_sr'))
        async def request(*args):return {'contracts_for':{'available':rows}}
        provider.request=request
        with pytest.raises(DataError) as caught:await provider.supports('EUR/USD','CALL',300)
        assert caught.value.code==code
    asyncio.run(run())




def test_api_scan_survives_requests_and_restart_is_stopped(tmp_path):
    cfg=Config(database=str(tmp_path/'db'),telegram_token='123:PRIVATE_SECRET',telegram_chat='123')
    body={'provider':'deriv','assets':['EUR/USD'],'timeframe':'M3','strategy':'scan_all'}
    class Source(Feed):
        async def candles(self,*args):await asyncio.Event().wait()
    with TestClient(create_app(cfg,Source(),TG())) as client:
        assert client.post('/api/scan/start',json=body).status_code==202
        assert client.get('/api/state').json()['scan']['active']
        assert client.post('/api/scan/start',json=body).status_code==409
        assert client.put('/api/settings',json=body).status_code==409
        assert client.put('/api/strategy',json={'strategy':'auto'}).status_code==409
        diagnostic=client.get('/api/diagnostics')
        assert diagnostic.status_code==200 and 'PRIVATE_SECRET' not in diagnostic.text
        assert diagnostic.json()['scan']['timeframe']=='M3'
        # Shutdown while a scan is active exercises task cancellation and recovery.
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        state=client.get('/api/state').json()
        assert not state['scan']['active'] and not state['busy']
        assert state['analyses'][0]['status']=='interrompida'
        assert client.post('/api/scan/stop').status_code==200
