"""Deterministic regressions for the audit; no real messages or market efficacy."""
import asyncio
from copy import deepcopy
import json
import random
from unittest.mock import patch

import httpx
import pytest

from app.config import Config
from app.context_window import ContextWindow
from app.delivery_policy import can_start, classify_ack
from app.strategies import scan_all as evaluate
from app.learning import profiles, operational_records
from app.market import Candle, DataError
from app.outcome_evidence import comparable_outcome
from app.selection_context import higher_context
from app.setup_contract import capture_setup, check_setup, movement_id, SETUP_VERSION
from app.store import Store
from app.strategies import STRATEGIES, scan_all
from app.strategies.quality import entry_guard
from app.telegram import Telegram
from tests.test_evolution_1250 import signal, candidate, manual
from tests.test_system import setup, drain, fixture


@pytest.mark.parametrize('change',[
    {'settlement_evidence':None}, {'settlement_evidence':[]},
    {'settlement_evidence':[{'t':1,'o':-9}]}, {'settled_at':60301},
    {'entry_at':60001}, {'settlement_source_identity':'other'},
    {'settlement_entry_at':60060}, {'result':'LOSS','expiry_price':101}, {'expiry_price':float('nan')},
])
def test_missing_corrupt_premature_or_mismatched_proof_never_counts_as_verified(change):
    row=signal(**change)
    assert not comparable_outcome(row,200000)
    store=Store(':memory:');store.add_signal(row,'invalid')
    assert store.win_rate_snapshot(signal(99),200000)['sample']==0
    assert store.setup_ranking(200000)['counts']['unverified']==1
    assert store.result_totals(verified=True,now=200000)['wins']==0
    assert store.signal(row['id'])['result']==row['result']


@pytest.mark.parametrize('field,value',[('t',60060),('o',99),('l',102),('h',99),('c',float('inf'))])
def test_price_proof_checks_each_original_quote_not_a_boolean_flag(field,value):
    row=signal();row['settlement_evidence'][0][field]=value
    row['price_evidence']={'verified':True,'version':'1.26.2-m1-closed-1'}
    assert not comparable_outcome(row,200000)


@pytest.mark.parametrize('direction,result',[('CALL','WIN'),('CALL','LOSS'),('PUT','WIN'),('PUT','LOSS'),('CALL','EMPATE')])
def test_valid_proofs_require_closed_expiry_and_known_result_cutoff(direction,result):
    row=signal(result=result,direction=direction)
    if direction=='PUT':
        row['expiry_price']=99 if result=='WIN' else 101
        row['settlement_evidence'][1]=Candle(60300,row['expiry_price'],102,98,100).json()
    assert comparable_outcome(row,row['settled_at'])
    assert not comparable_outcome(row,row['settled_at']-1)
    store=Store(':memory:');store.add_signal(row,'proved')
    assert store.win_rate_snapshot(row,row['settled_at']-1)['sample']==0
    assert store.result_totals(verified=True,now=row['settled_at'])['ties' if result=='EMPATE' else 'wins' if result=='WIN' else 'losses']==1


def test_projection_upgrade_keeps_origins_results_and_manual_versions(tmp_path):
    path=tmp_path/'history.db';store=Store(path)
    row=signal(settlement_evidence=None);store.add_signal(row,'legacy')
    store.confirm_manual('0',manual(),100000)
    before=[tuple(r) for r in store.db.execute('SELECT * FROM signals')]
    origins=[tuple(r) for r in store.db.execute('SELECT * FROM signal_origins')]
    confirmations=store.manual_confirmations()
    store.db.execute("UPDATE metric_projection_version SET version='1.26.0-1'")
    store.db.commit();store.db.close();store=Store(path)
    assert [tuple(r) for r in store.db.execute('SELECT * FROM signals')]==before
    assert [tuple(r) for r in store.db.execute('SELECT * FROM signal_origins')]==origins
    assert store.manual_confirmations()==confirmations
    assert store.validation_report(200000)['operational_price_verified'][0]['unavailable']==1
    assert store.result_totals()['wins']==1  # Explicit compatibility/raw view.
    assert store.result_totals(verified=True,now=200000)['unverified']==1


def test_manual_actual_duration_and_prices_are_separate_from_plan_across_all_reports(tmp_path):
    path=tmp_path/'history.db';store=Store(path);row=signal();store.add_signal(row,'original')
    confirmation={**manual(),'entry_at':60060,'expiry_at':60120,'entry_price':102,'expiry_price':100,'payout_pct':80}
    first=store.confirm_manual('0',confirmation,100000)
    projected=json.loads(store.db.execute("SELECT payload FROM metric_projection WHERE scope='manual'").fetchone()[0])
    assert (projected['duration_seconds'],projected['entry_price'],projected['expiry_price'])==(60,102,100)
    assert projected['planned_duration_seconds']==300 and projected['duration_basis']=='manual_actual'
    assert store.validation_page(105000,scope='manual')['items'][0]['duration_seconds']==60
    assert store.validation_report(105000)['manual_declared'][0]['duration_seconds']==60
    assert store.setup_ranking(105000,scope='manual')['counts']['losses']==1
    assert store.signal('0')==row
    store.confirm_manual('0',{**confirmation,'result':'WIN'},110000)
    store.db.close();store=Store(path)
    assert store.validation_page(105000,scope='manual')['items'][0]['losses']==1
    rank=store.setup_ranking(120000,scope='manual')['items'][0]
    assert rank['duration_seconds']==60 and rank['wins']==1
    assert first['externally_verified'] is False and store.signal('0')==row


def test_manual_omitted_prices_remain_missing_and_planned_duration_is_labelled():
    store=Store(':memory:');store.add_signal(signal(),'original')
    store.confirm_manual('0',manual(),100000)
    row=json.loads(store.db.execute("SELECT payload FROM metric_projection WHERE scope='manual'").fetchone()[0])
    assert row.get('entry_price') is None and row.get('expiry_price') is None
    assert row['duration_basis']=='planned_unconfirmed' and row['duration_seconds']==300
    assert store.signal('0')['entry_price']==100


@pytest.mark.parametrize('start_lead,delay,status,actionable',[(61,11,'expirado',None),(72,11,'enviado',True),(73,14,'enviado',False)])
def test_delivery_reserves_timeout_and_keeps_late_ack_as_delivered(tmp_path,start_lead,delay,status,actionable):
    async def run():
        service,now=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service)
        row=service.store.signals()[0]
        service.store.patch(row['id'],{'telegram':{'status':'pendente'}})
        now[0]=row['entry_at']-start_lead
        calls=[]
        async def slow(s):
            calls.append(s['id']);now[0]+=delay
            return {'status':'enviado','message_id':999,'destination_key':'synthetic'}
        service.telegram.send=slow
        await service.deliver(row['id']);await service.deliver(row['id'])
        saved=service.store.signal(row['id'])['telegram']
        assert saved['status']==status
        assert len(calls)==(status=='enviado')
        if actionable is not None:
            assert saved['actionable_at_ack'] is actionable and saved['lead_at_ack_seconds']==start_lead-delay
            assert saved['message_id']==999 and saved['destination_key']=='synthetic'
            assert saved['delivery_started_at']==row['entry_at']-start_lead
    asyncio.run(run())


def test_queue_wait_rechecks_same_lead_policy_before_http_post():
    async def run():
        from tests.test_telegram_compact import signal as telegram_signal
        s=telegram_signal();requests=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:requests.append(r))) as client:
            tg=Telegram(Config(telegram_token='123:synthetic',telegram_chat='1'),client)
            clock=[s['entry_at']-100]
            tg._last_post=__import__('time').monotonic()
            async def queue_wait(_):clock[0]+=30
            with patch('app.telegram.time.time',side_effect=lambda:clock[0]), patch('app.telegram.asyncio.sleep',new=queue_wait):
                result=await tg.send(s)
        assert result['status']=='expirado' and not requests
    asyncio.run(run())


def test_delivered_ack_after_cancellation_is_never_actionable_or_retried():
    s=signal(state='cancelado')
    result=classify_ack(s,{'status':'enviado','message_id':8},s['entry_at']-100,s['entry_at']-105)
    assert result['status']=='enviado' and result['actionable_at_ack'] is False and result['lead_status']=='late'
    assert can_start(s,s['entry_at']-72) and not can_start(s,s['entry_at']-71)


def validation_signal(direction='CALL',wick=False):
    return {'direction':direction,'entry_at':1800000600,'entry_validation':{'atr':1},
        'evidence':{'candles':[{'c':100.1}]},
        'setup_contract':{'version':SETUP_VERSION,'direction':direction,'confirmed_at':1800000420,
            'valid_until':1800000660,'levels':{'boundary':100},
            'invalidations':[{'level':'boundary','price':100,'mode':('wick_' if wick else 'close_')+('below' if direction=='CALL' else 'above'),'inclusive':wick}]}}


@pytest.mark.parametrize('direction,price',[('CALL',99.9),('PUT',100.3)])
def test_original_zone_break_cancels_even_when_generic_displacement_is_small(direction,price):
    s=validation_signal(direction);entry=s['entry_at']
    rows=[Candle(entry-180,100.1,100.4,99.8,100.1),Candle(entry-120,100.1,100.4,99.8,price)]
    audit,reason=entry_guard(s,rows,entry-45)
    assert 'invalidada' in reason and audit['invalidated_level']=='boundary'
    assert abs(audit['displacement_atr'])<.35


def test_intraperiod_break_remains_invalid_after_price_recovers_and_open_bar_is_ignored():
    s=validation_signal();entry=s['entry_at']
    rows=[Candle(entry-180,100.1,100.2,99.8,99.9),Candle(entry-120,100,100.3,99.9,100.2),
          Candle(entry-60,100.2,110,90,90)]  # Still open at entry-45.
    audit,reason=entry_guard(s,rows,entry-45)
    assert reason and audit['invalidation_candle']['t']==entry-180
    s['setup_contract']['confirmed_at']=entry-120
    assert not entry_guard(s,rows,entry-45)[1]  # Pre-decision violation is not reused.


def test_fvg_midpoint_wick_and_expired_setup_are_mandatory_gates():
    s=validation_signal(wick=True);entry=s['entry_at']
    rows=[Candle(entry-120,100.1,100.3,100,100.2)]
    assert entry_guard(s,rows,entry-45)[1]
    s['setup_contract']['valid_until']=entry-1
    audit,reason=entry_guard(s,rows,entry-45)
    assert 'vencido' in reason
    s['setup_contract']['invalidations'][0]['price']=float('nan');s['setup_contract']['valid_until']=entry+60
    with pytest.raises(DataError):entry_guard(s,rows,entry-45)






def legacy_limits(rows,now,entry,limits):
    valid=[r for r in rows if abs(r['entry_at']-entry)<3600 and (r['state']!='cancelado' or r.get('emitted_at') is not None
        or r.get('telegram',{}).get('status') in ('enviado','incerto','enviando'))]
    settled=sorted([r for r in rows if r['state']=='encerrado' and not r.get('settlement_pending')
        and r.get('settled_at',r['expiry_at'])<=now and r.get('result') in ('WIN','LOSS','EMPATE')],
        key=lambda r:(r.get('settled_at',r['expiry_at']),r['created_at']))
    streak=pause=0
    for r in settled:
        if r['result']=='LOSS':
            streak+=1
            if streak>=limits['loss_pause_after']:pause=max(pause,r.get('settled_at',r['expiry_at'])+limits['pause_minutes']*60)
        else:streak=0
    return {**limits,'used':len(valid),'remaining':max(0,limits['hourly_limit']-len(valid)),
        'active':sum(r['state'] in ('aguardando entrada','em andamento') for r in valid),
        'pause_until':pause if pause>now else 0,'loss_streak':limits['loss_pause_after'] if pause>now else 0}


@pytest.mark.parametrize('threshold,pause_minutes',[(1,1),(2,15),(4,120),(10,3)])
def test_indexed_limits_match_reference_with_out_of_order_results_restart_and_archive(tmp_path,threshold,pause_minutes):
    path=tmp_path/'risk.db';store=Store(path);rng=random.Random(1262);rows=[];now=100000
    store.save_settings({'loss_pause_after':threshold,'pause_minutes':pause_minutes})
    for i in range(350):
        entry=now-rng.randrange(-300,18000)
        row=signal(i,rng.choice(['WIN','LOSS','EMPATE']),created_at=entry-60,entry_at=entry,expiry_at=entry+300,
            settled_at=entry+300+rng.randrange(65,800),state=rng.choice(['encerrado']*5+['aguardando entrada','cancelado']),
            emitted_at=entry-60 if i%3 else None,settlement_pending=i%11==0,
            telegram={'status':rng.choice(['enviado','incerto','falha','não configurado'])})
        store.add_signal(row,str(i));rows.append(row)
    # Correct a settlement received out of order; the original creation order stays stable.
    updates={'settled_at':now-10,'state':'encerrado','result':'LOSS','settlement_pending':False}
    store.patch('2',updates);rows[2].update(updates)
    for t in (now-900,now,now+1800):
        assert store.apex_limits(t,t+120)==legacy_limits(rows,t,t+120,store.risk_settings())
    expected=store.apex_limits(now)
    store.clear_history('all',None,now)
    store.db.close();store=Store(path)
    assert store.apex_limits(now)==expected
    with patch('app.store.json.loads',wraps=json.loads) as loads:
        store.apex_limits(now)
        assert loads.call_count==1  # Settings only; no decision candles decoded.


def test_learning_load_is_compact_and_keeps_raw_settlement_proof():
    store=Store(':memory:');row=signal()
    row['evidence']['candles']=[{'large':'x'*1000} for _ in range(100)]
    store.add_signal(row,'proved')
    learned=store.learning_signals()
    assert len(json.dumps(learned))<3000 and 'large' not in json.dumps(learned)
    assert comparable_outcome(learned[0],200000)
    assert next(iter(profiles(operational_records(learned),200000).values()))['sample']==1


def test_M1_context_warms_without_api_calls_and_is_causal_across_sources():
    window=ContextWindow();start=1800000000;key=('deriv','source','EUR/USD',60)
    rows=[Candle(start+i*60,100,101,99,100+i*.001) for i in range(120)]
    first_cutoff=rows[98].t+60
    window.remember(key,rows[:99],60,first_cutoff)
    first,_=window.snapshot(key,first_cutoff)
    assert higher_context(first,60,first_cutoff)['status']=='unavailable'
    cutoff=rows[-1].t+60
    window.remember(key,rows[-99:],60,cutoff)
    combined,status=window.snapshot(key,cutoff)
    context=higher_context(combined,60,cutoff)
    assert context['status']=='available' and status['additional_api_calls']==0
    assert context['closed_candles']==24 and context['last_close_at']<=cutoff
    assert window.snapshot(key,first_cutoff)[0]==first  # No backfilled future availability.
    assert window.snapshot(('coinbase','source','EUR/USD',60),cutoff)[0]==[]
    assert ContextWindow().snapshot(key,cutoff)[1]['status']=='cold_start'


def test_context_detects_revised_closed_candle_and_keeps_operational_rows_unchanged():
    window=ContextWindow(max_candles=120);key=('deriv','source','EUR/USD',60);start=1800000000
    rows=[Candle(start+i*60,100,101,99,100) for i in range(120)]
    window.remember(key,rows,60,rows[-1].t+60)
    revised=[Candle(rows[-1].t,100,101,99,100.5)]
    window.remember(key,revised,60,rows[-1].t+120)
    assert window.snapshot(key,rows[-1].t+120)[1]['status']=='closed_candle_revision'
    assert len(window.snapshot(key,rows[-1].t+60)[0])==120
    assert rows[-1].c==100


def test_unproven_outcome_is_unavailable_in_all_verified_metrics_not_dropped_from_frequency():
    store=Store(':memory:');store.add_signal(signal(),'proved')
    store.add_signal(signal(1,settlement_evidence=None),'legacy')
    page=store.validation_page(200000)['items'][0]
    stats=page['price_verified']
    assert page['wins']==2 and stats['wins']==1
    assert stats['signals']==2 and stats['unavailable']==1 and stats['unavailable_pct']==50
    assert stats['frequency_per_hour']==page['frequency_per_hour']


def test_manual_custom_duration_can_be_filtered_without_enabling_it_as_operational_expiration(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_system import Feed,TG
    with TestClient(create_app(Config(database=str(tmp_path/'api.db')),Feed(),TG())) as client:
        service=client.app.state.service;service.clock=lambda:200000
        service.store.add_signal(signal(),'original')
        service.store.confirm_manual('0',{**manual(result='WIN'),'entry_at':60000,'expiry_at':60063.5},100000)
        report=client.get('/api/validation/setups?scope=manual&duration_seconds=63.5')
        assert report.status_code==200 and report.json()['items'][0]['duration_seconds']==63.5
        assert client.get('/api/validation/setups?duration_seconds=63.5').status_code==422
        assert service.settings()['duration_seconds']==300


def test_stop_during_background_learning_releases_analysis_and_cannot_emit(tmp_path):
    import threading
    async def run():
        service,_=setup(tmp_path);started=threading.Event();finish=threading.Event()
        def slow(_):
            started.set();finish.wait(2);return {}
        service.historical_learning=slow
        service.start(['EUR/USD'],300)
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.001)
            assert started.is_set()
            await asyncio.wait_for(service.stop_analysis(),.2)
            assert not service.analysis_active and not service.store.busy(service.clock())
            assert not service.store.signals() and not service.provider.calls and not service.telegram.calls
        finally:finish.set()
    asyncio.run(run())


def test_failed_engine_leaves_auditable_incomplete_pool_not_a_hidden_opposing_vote():
    audit={}
    with patch('app.strategies.evaluate_all',side_effect=RuntimeError('synthetic failure')):
        selected,reason=scan_all(fixture(),audit=audit)
    assert selected is None and 'incompleta' in reason
    assert audit['pool_complete'] is False and audit['engine_errors']
    assert all(e['error_type']=='RuntimeError' for e in audit['engine_errors'])


def test_failed_pool_does_not_abort_next_asset_or_publish_experimental_entries(tmp_path):
    async def run():
        service,_=setup(tmp_path);service.select_strategy('scan_all');service.set_observation(True)
        from app.manual_price_action import evaluate_all as original
        calls=[0]
        def intermittent(rows,**kwargs):
            calls[0]+=1
            if calls[0]==1:raise RuntimeError('synthetic failure')
            return original(rows)
        with patch('app.strategies.evaluate_all',side_effect=intermittent):
            service.start(['EUR/USD','GBP/USD'],300);await drain(service)
        assert [r['asset'] for r in service.store.signals()]==['GBP/USD']
        decisions=service.store.decisions()['items']
        failed=[r['decision'] for r in decisions if r['decision']['asset']=='EUR/USD'][0]
        assert failed['selection']['pool_complete'] is False
        assert not any(r['asset']=='EUR/USD' for r in service.observer.rows())
        assert service.telegram.calls==1 and not service.analysis_active
    asyncio.run(run())
