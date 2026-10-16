"""Controlled regressions for recovery, timestamps and responsive local reports."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
import threading
import sqlite3
import pytest
from app.store import Store
from app.analysis_preview import asset_previews
from app.quotes import Quotes
from app.market import Candle
from app.logging_setup import BRASILIA
from app.main import create_app
from app.config import Config
from app.version import VERSION
from app.research_store import ResearchStore
from app.selection_observer import SelectionObserver
from tests.test_system import setup, drain
from tests.test_evolution_1250 import signal


def test_previous_release_feedback_is_recovered_after_update(tmp_path):
    store=Store(str(tmp_path/'db.sqlite'))
    original=signal(settled_by_version='1.26.5')
    store.add_signal(original,'original');store.close()
    store=Store(str(tmp_path/'db.sqlite'))
    assert store.pending_learning_feedback(100000)==[original]
    assert store.signal(original['id'])==original
    store.close()


def test_one_retry_does_not_starve_other_completed_results(tmp_path):
    async def run():
        service,clock=setup(tmp_path);clock[0]=100000
        for i in range(2):service.store.add_signal(signal(i,settled_by_version=VERSION),str(i))
        def fail(_):raise RuntimeError('controlled failure')
        service.historical_learning=fail
        await service.tick();await drain(service)
        service.historical_learning=lambda _:{}
        await service.tick();await drain(service)
        assert service.store.latest_learning_feedback(clock[0])['signal_id']=='1'
        assert service.store.signal('0')['result']=='WIN'
        clock[0]+=61;await service.tick();await drain(service)
        assert service.store.latest_learning_feedback(clock[0])['signal_id']=='0'
        assert not service.store.pending_learning_feedback(clock[0])
    asyncio.run(run())


@pytest.mark.parametrize('missing_provider',[False,True])
def test_unavailable_result_preserves_known_timestamp(tmp_path,missing_provider):
    async def run():
        service,clock=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service)
        original=service.store.signals()[0];clock[0]=original['expiry_at']+601
        service.provider.settlement=[]
        if missing_provider:service.providers.clear()
        await service.settle(original);await drain(service)
        result=service.store.signal(original['id'])
        assert result['result']=='Resultado indisponível' and result['settled_at']==clock[0]
        assert service.store.latest_learning_feedback(clock[0])['result_known_at']==clock[0]
        assert result['engine_explanation']==original['engine_explanation']
        assert service.store.result_totals(verified=True,now=clock[0])['losses']==0
    asyncio.run(run())


def test_recovered_analysis_ends_and_projection_is_interrupted():
    store=Store(':memory:')
    store.analysis({'id':'crash','created_at':100,'status':'analisando','source_key':'deriv',
                    'assets':['EUR/USD'],'current_asset':'EUR/USD','checks':[]})
    store.recover(200);analysis=store.analyses()[0]
    assert analysis['finished_at']==200 and analysis['recovered_at']==200
    assert asset_previews(analysis)[0]['status']=='interrupted'
    store.recover(300);assert store.analyses()[0]==analysis and not store.busy(300)


def test_legacy_interrupted_analysis_is_never_displayed_as_running():
    analysis={'id':'old','created_at':100,'status':'interrompida','source_key':'deriv',
              'assets':['EUR/USD'],'current_asset':'EUR/USD','checks':[]}
    assert asset_previews(analysis)[0]['status']=='interrupted'
    assert 'finished_at' not in analysis


def test_quote_reference_changes_at_brasilia_midnight(monkeypatch):
    now=[datetime(2026,10,9,23,59,59,tzinfo=BRASILIA).timestamp()]
    monkeypatch.setattr('app.quotes.time.time',lambda:now[0])
    class Provider:
        identity='test:midnight';name='controlled'
        def __init__(self):self.references=[];self.calls=0
        async def check_asset(self,_):pass
        async def candles(self,*_):
            self.calls+=1
            return [Candle(int(now[0]//60)*60,110,110,110,110)]
        async def reference_candle(self,asset,midnight):
            self.references.append(midnight);price=100 if len(self.references)==1 else 110
            return Candle(midnight-60,price,price,price,price)
    async def run():
        quotes=Quotes();provider=Provider()
        assert (await quotes.get(provider,'EUR/USD'))['change_pct']==10
        now[0]+=2;second=await quotes.get(provider,'EUR/USD')
        assert second['change_pct']==0 and len(provider.references)==2
        assert second['reference_day_at']==provider.references[-1]
        assert await quotes.get(provider,'EUR/USD')==second and provider.calls==2
    asyncio.run(run())


def test_quote_day_changes_while_provider_request_is_in_flight(monkeypatch):
    now=[datetime(2026,10,9,23,59,59,tzinfo=BRASILIA).timestamp()]
    monkeypatch.setattr('app.quotes.time.time',lambda:now[0])
    class Provider:
        async def check_asset(self,_):pass
        async def candles(self,*_):
            now[0]+=2
            return [Candle(int(now[0]//60)*60,110,110,110,110)]
        async def reference_candle(self,asset,midnight):return Candle(midnight-60,100,100,100,100)
    result=asyncio.run(Quotes().get(Provider(),'EUR/USD'))
    expected=int(datetime.fromtimestamp(now[0],BRASILIA).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
    assert result['reference_day_at']==expected and result['reference_at']==expected


@pytest.mark.parametrize('path',['/api/validation','/api/aurora/metrics'])
def test_heavy_compatibility_report_does_not_block_event_loop(path):
    async def run():
        app=create_app(Config(database=':memory:'));entered=threading.Event();release=threading.Event()
        main_thread=threading.get_ident();called=[]
        def report(*_):
            called.append(threading.get_ident());entered.set()
            assert release.wait(3),'report not released'
            return {} if path=='/api/validation' else []
        app.state.service=SimpleNamespace(clock=lambda:100,observer=None,
            store=SimpleNamespace(validation_report=report,apex_metrics=report))
        route=next(r for r in app.routes if r.path==path)
        task=asyncio.create_task(route.endpoint())
        try:
            assert await asyncio.to_thread(entered.wait,2)
            assert called[0]!=main_thread
            # The event loop runs while the expensive report remains in progress.
            assert not task.done()
        finally:release.set()
        await task
    asyncio.run(run())


def test_legacy_validation_uses_one_shared_time_cutoff():
    async def run():
        app=create_app(Config(database=':memory:'));times=[];clock=iter([100,101,102])
        def operational(now):times.append(now);return {}
        def experimental(now):times.append(now);return {'sessions':[]}
        app.state.service=SimpleNamespace(clock=lambda:next(clock),store=SimpleNamespace(validation_report=operational),
            observer=SimpleNamespace(report=experimental))
        await next(r for r in app.routes if r.path=='/api/validation').endpoint()
        assert times==[100,100]
    asyncio.run(run())


@pytest.mark.parametrize('cls',[Store,ResearchStore,SelectionObserver])
def test_close_waits_for_sqlite_worker_and_is_idempotent(cls):
    async def run():
        store=cls(':memory:');entered=threading.Event();release=threading.Event()
        def reader():
            with store.lock:
                entered.set();assert release.wait(3)
                assert store.db.execute('SELECT 1').fetchone()[0]==1
        worker=asyncio.create_task(asyncio.to_thread(reader))
        assert await asyncio.to_thread(entered.wait,2)
        closed=asyncio.create_task(asyncio.to_thread(store.close))
        await asyncio.sleep(.03)
        assert not closed.done() and not store.closed
        release.set();await worker;await closed
        store.close()
        with pytest.raises(sqlite3.ProgrammingError):store.db.execute('SELECT 1')
    asyncio.run(run())


def test_feedback_retry_deadline_and_original_outcome_survive_restart(tmp_path):
    path=str(tmp_path/'db.sqlite');store=Store(path);original=signal(settled_by_version=VERSION)
    store.add_signal(original,'one');store.defer_learning_feedback(original['id'],100100)
    store.patch(original['id'],{'telegram_result':{'status':'enviado'}})
    store.close();store=Store(path)
    assert not store.pending_learning_feedback(100099)
    assert store.pending_learning_feedback(100100)[0]['result']==original['result']
    assert store.db.execute('SELECT payload FROM signal_origins').fetchone()[0] is not None
    store.record_learning_feedback({'signal_id':original['id'],'known_at':100100,'result':'WIN'})
    assert not store.pending_learning_feedback(100101)
    store.close()


def test_queue_migrates_only_explicit_supported_settlements_once(tmp_path):
    path=str(tmp_path/'db.sqlite');store=Store(path)
    for i,version in enumerate(['1.26.5','1.26.4',None]):
        original=signal(i,settled_by_version=version)
        store.add_signal(original,str(i))
    # Emulate a real pre-queue 1.26.5 database with an interrupted feedback task.
    store.db.execute('DROP TABLE feedback_jobs');store.db.execute('DROP TABLE feedback_queue_meta');store.db.commit()
    store.close();store=Store(path)
    assert [s['id'] for s in store.pending_learning_feedback(100000,10)]==['0']
    store.close();store=Store(path)
    assert [s['id'] for s in store.pending_learning_feedback(100000,10)]==['0']
    store.close()


@pytest.mark.parametrize('mode',['pending','future_result','future_expiry','cancelled'])
def test_feedback_queue_keeps_unknown_results_out(mode):
    store=Store(':memory:');s=signal(settled_by_version=VERSION)
    if mode=='pending':s['settlement_pending']=True
    if mode=='future_result':s['settled_at']=100001
    if mode=='future_expiry':s['expiry_at']=100001
    if mode=='cancelled':s['state']='cancelado'
    store.add_signal(s,'unknown');assert not store.pending_learning_feedback(100000)


def test_idle_feedback_uses_ready_index_without_scanning_signal_payloads():
    store=Store(':memory:')
    for i in range(100):store.add_signal(signal(i),str(i))
    plan=store.db.execute('EXPLAIN QUERY PLAN SELECT s.payload FROM feedback_jobs q JOIN signals s ON s.id=q.signal_id '
        'WHERE q.ready_at<=? ORDER BY q.ready_at,q.created_at,q.signal_id LIMIT ?',(100000,1)).fetchall()
    assert any('feedback_jobs_ready' in row[3] for row in plan)
    assert all('SCAN s' not in row[3] for row in plan)
    assert store.pending_learning_feedback(100000)==[]


def test_day_export_is_half_open_and_preserves_archive_and_confirmations():
    store=Store(':memory:')
    for i,entry in enumerate([99,100,199,200]):store.add_signal(signal(i,entry_at=entry),str(i))
    store.db.execute("INSERT INTO history_archives VALUES('1','example',500)");store.db.commit()
    store.confirm_manual('1',{'result':'WIN','executed':True},500)
    rows,confirmations=store.export_period(100,200)
    assert [s['id'] for s in rows]==['1','2'] and confirmations[0]['signal_id']=='1'


def test_manual_preview_can_be_stopped_without_new_emission(tmp_path):
    async def run():
        service,clock=setup(tmp_path);started=asyncio.Event()
        async def slow(*_):started.set();await asyncio.Future()
        service.provider.candles=slow;service.start(['EUR/USD'],300,preview=True)
        await started.wait()
        assert service.snapshot()['analysis_active'] and not service.scan['active']
        await service.stop_scan();await drain(service)
        assert not service.snapshot()['analysis_active'] and not service.store.signals() and service.telegram.calls==0
        assert service.store.analyses()[0]['status']=='interrompida'
    asyncio.run(run())


def test_observation_report_releases_database_lock_during_statistics(monkeypatch):
    import json
    observer=SelectionObserver(':memory:')
    observer.db.execute('INSERT INTO sessions VALUES(?,?,?)',('one',90,json.dumps({'id':'one','created_at':90})));observer.db.commit()
    entered=threading.Event();release=threading.Event()
    def slow_group(*_):entered.set();assert release.wait(3);return []
    monkeypatch.setattr('app.selection_observer.grouped',slow_group)
    async def run():
        task=asyncio.create_task(asyncio.to_thread(observer.report,100))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            acquired=observer.lock.acquire(blocking=False)
            assert acquired,'statistical worker still holds database lock'
            observer.lock.release()
            assert observer.overview(100)['total_sessions']==1
            observer.close()  # Computation no longer uses SQLite after its snapshot.
        finally:release.set()
        assert (await task)['sessions'][0]['session']['id']=='one'
    asyncio.run(run())


def test_observation_report_does_not_cache_snapshot_after_concurrent_write(monkeypatch):
    import json
    observer=SelectionObserver(':memory:')
    observer.db.execute('INSERT INTO sessions VALUES(?,?,?)',('one',90,json.dumps({'id':'one','created_at':90})));observer.db.commit()
    entered=threading.Event();release=threading.Event()
    def slow_group(*_):entered.set();assert release.wait(3);return []
    monkeypatch.setattr('app.selection_observer.grouped',slow_group)
    async def run():
        task=asyncio.create_task(asyncio.to_thread(observer.report,100))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            with observer.lock,observer.db:
                observer.db.execute('INSERT INTO sessions VALUES(?,?,?)',('two',95,json.dumps({'id':'two','created_at':95})))
        finally:release.set()
        snapshot=await task
        assert len(snapshot['sessions'])==1 and not observer._report_cache
        assert len(observer.report(100)['sessions'])==2
    asyncio.run(run())
