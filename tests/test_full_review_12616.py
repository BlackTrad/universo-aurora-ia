"""Regression coverage for the frontend/backend review and entry scheduling."""
import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.market import DataError, validate, validate_latest
from app.store import Store
from app.entry_timing import next_entry, describe_entry
from app.delivery_policy import can_start
from app.service import Service
from tests.test_automatic_sources_12614 import service, sources
from tests.test_evolution_1250 import signal
from tests.test_system import TG, fixture
from tests.test_manual_12611 import Feed, Telegram, fixture as manual_fixture


def test_connection_requires_the_last_closed_candle_not_just_age_tolerance():
    rows=fixture()[:-1];now=fixture()[-1].t+305
    assert validate(rows,now,330,300)
    with pytest.raises(DataError) as error:
        validate_latest(rows,now,330,300)
    assert error.value.code=='missing_latest_candle'


@pytest.mark.parametrize('automatic',[False,True])
def test_connection_api_does_not_approve_a_missing_latest_candle(tmp_path,automatic):
    feeds=sources()
    for feed in feeds.values():feed.rows=fixture()[:-1]
    with patch('app.main.DerivProvider',return_value=feeds['deriv']), \
         patch('app.main.BinanceProvider',return_value=feeds['binance']), \
         patch('app.main.CoinbaseProvider',return_value=feeds['coinbase']):
        app=create_app(Config(database=str(tmp_path/'db')),provider=None if automatic else feeds['deriv'],
                       telegram=TG(),clock=lambda:fixture()[-1].t+305)
        with TestClient(app) as client:
            checked=client.post('/api/connection/check',json={})
            assert checked.status_code==200
            result=checked.json()
            assert result['status']=='indisponível'
            if automatic:
                assert len(result['sources'])==3
                assert all(row['code']=='missing_latest_candle' for row in result['sources'])
            else:assert result['code']=='missing_latest_candle'
            assert not app.state.service.store.signals() and app.state.service.telegram.calls==0


def test_partial_and_provider_health_age_without_rewriting_original_check():
    s,_=service();clock=[s.clock()];s.clock=lambda:clock[0]
    try:
        s.source_health={'deriv':{'status':'disponível','data_at':clock[0],'checked_at':clock[0],'max_age':330},
                         'binance':{'status':'indisponível','checked_at':clock[0],'code':'connection'}}
        assert s.snapshot()['feed']['status']=='parcial'
        clock[0]+=331
        old=s.snapshot()
        assert old['feed']['status']=='desatualizado'
        assert next(p for p in old['providers'] if p['key']=='deriv')['status']=='desatualizado'
        assert all(p.get('status')!='disponível' for p in old['providers'])
        assert s.source_health['deriv']['status']=='disponível'
    finally:s.store.close()


def test_upgrade_recovers_feedback_from_every_compatible_recent_patch(tmp_path):
    path=str(tmp_path/'learning.db');store=Store(path)
    versions=['1.26.13','1.26.14','1.26.15','1.26.16']
    originals={}
    for i,version in enumerate(versions+['1.26.99','1.26.4']):
        row=signal(i,settled_by_version=version)
        store.add_signal(row,'review-'+str(i));originals[row['id']]=row
    with store.db:
        store.db.execute('DELETE FROM feedback_jobs')
        store.db.execute('UPDATE feedback_queue_meta SET version=? WHERE id=1',('previous-policy-missing-recent-patches',))
    store.close()
    reopened=Store(path)
    try:
        pending=reopened.pending_learning_feedback(200000,limit=100)
        assert {r['settled_by_version'] for r in pending}==set(versions)
        assert all(reopened.signal(sid)==row for sid,row in originals.items())
        assert reopened.db.execute('SELECT COUNT(*) FROM learning_feedback').fetchone()[0]==0
    finally:reopened.close()


@pytest.mark.parametrize('notice',[60,120,180])
def test_entry_scheduling_reserves_delivery_time_before_the_manual_notice(notice):
    base=1800000000
    for offset in (0,.001,47.999,48,48.001,59.999,60,168):
        now=base+offset;entry=next_entry(now,notice)
        signal={'entry_at':entry,'evidence':{'configuration':{'min_lead_seconds':notice}}}
        assert entry%60==0 and can_start(signal,now)
        assert not can_start({**signal,'entry_at':entry-60},now)


@pytest.mark.parametrize('offset,phase',[(0,'opening'),(60,'during_candle'),
                                        (120,'during_candle'),(180,'during_candle'),(240,'last_minute')])
def test_entry_phase_is_relative_to_the_real_m5_candle(offset,phase):
    base=1800000000
    timing=describe_entry(base+offset,300,base-80,60)
    assert timing['phase']==phase and timing['candle_open_at']==base
    assert timing['candle_close_at']==base+300 and timing['offset_seconds']==offset
    assert timing['alignment']=='minute_open'
    assert describe_entry(base+offset,60,base-80,60)['phase']=='opening'


@pytest.mark.parametrize('offset,emits',[(0,True),(48,True),(48.001,True),
                                      (168,True),(168.001,False),(239,False)])
def test_closed_setup_can_use_the_last_m5_minute_but_never_rolls_past_its_window(offset,emits):
    async def run():
        rows=manual_fixture('pa_hammer');start=rows[-1].t+300;now=start+offset
        store=Store(':memory:')
        try:
            service=Service(Config(database=':memory:'),store,Feed(rows),Telegram(),clock=lambda:now,automatic_only=True)
            service.start(['EUR/USD'],300,'M5')
            while service.tasks:await asyncio.gather(*list(service.tasks))
            signals=store.signals()
            assert bool(signals)==emits
            if emits:
                for row in signals:
                    assert can_start(row,now)
                    assert row['entry_at']<row['setup_contract']['entry_window_end']
                    assert row['entry_timing']==describe_entry(row['entry_at'],300,now,60)
                    if offset==168:
                        assert row['entry_at']==start+240 and row['entry_timing']['phase']=='last_minute'
            else:
                assert any(c.get('code')=='setup_expired' for c in store.analyses()[0]['checks'])
        finally:store.close()
    asyncio.run(run())


def test_delayed_asset_comparison_discards_an_entry_that_lost_its_delivery_budget():
    async def run():
        rows=manual_fixture('pa_hammer');start=rows[-1].t+300;clock=[start]
        class DelayedFeed(Feed):
            assets=['EUR/USD','GBP/USD']
            async def candles(self,asset,interval=300,count=100):
                if asset=='GBP/USD':clock[0]=start+49
                return self.rows
        store=Store(':memory:')
        try:
            service=Service(Config(database=':memory:'),store,DelayedFeed(rows),Telegram(),clock=lambda:clock[0],automatic_only=True)
            service.start(['EUR/USD','GBP/USD'],300,'M5')
            while service.tasks:await asyncio.gather(*list(service.tasks))
            signals=store.signals()
            assert signals and {s['asset'] for s in signals}=={'GBP/USD'}
            assert all(can_start(s,clock[0]) for s in signals)
            assert any(c.get('code')=='entry_late' and c['asset']=='EUR/USD' for c in store.analyses()[0]['checks'])
        finally:store.close()
    asyncio.run(run())


@pytest.mark.parametrize('offset,emits',[(168,True),(168.001,False)])
def test_live_trigger_obeys_the_same_last_minute_deadline(offset,emits):
    from tests.test_book_12612 import LiveFeed, TG as BookTelegram, scenario
    async def run():
        rows=scenario('pa_flow_hammer')['rows'];start=rows[-1].t+300;clock=[start+offset]
        store=Store(':memory:');feed=LiveFeed(clock)
        service=Service(Config(database=':memory:'),store,feed,BookTelegram(),clock=lambda:clock[0],automatic_only=True)
        try:
            service.scan={'active':True,'id':'timing-review','assets':['EUR/USD']}
            service.start(['EUR/USD'],300,'M5',scan_id='timing-review')
            await service.current_analysis_task
            signals=await service.poll_intrabar()
            while service.tasks:await asyncio.gather(*list(service.tasks))
            assert bool(signals)==emits
            if emits:
                assert len(signals)==1 and signals[0]['entry_at']==start+240
                assert signals[0]['entry_timing']['phase']=='last_minute'
                assert can_start(signals[0],clock[0])
                assert not await service.poll_intrabar()
            else:assert not store.signals()
        finally:
            await service.stop_scan()
            store.close()
    asyncio.run(run())


@pytest.mark.parametrize('latency,expected',[(0,'pending_validation'),(48,'pending_validation'),(49,'cancelled')])
def test_reference_observation_preserves_the_operational_entry_and_delivery_deadline(latency,expected):
    from app.selection_observer import SelectionObserver
    from tests.test_validation_1240 import context, candidate
    observer=SelectionObserver(':memory:');rows=fixture();now=rows[-1].t+300
    try:
        record=observer.capture_selection({'selector':'reference_fixed','candidate':candidate(),'pool':[],'reason':''},
            session=context(observer,now),asset='EUR/USD',source_key='deriv',source_identity='synthetic',
            rows=rows,interval=300,timeframe='M5',duration=300,now=now,observed_at=now+latency,min_lead=60)
        assert record['entry_at']==next_entry(now,60)==now+120
        assert record['state']==expected and record['sent_to_telegram'] is False
        assert record['entry_timing']==describe_entry(record['entry_at'],300,now,60)
        assert record['entry_at']==observer.rows()[0]['entry_at']
    finally:observer.close()
