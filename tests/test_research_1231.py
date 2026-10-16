"""Prospective/ablation correctness on synthetic fixtures, not performance."""
import asyncio
from dataclasses import replace
import json
from unittest.mock import patch
import pytest
from app.config import Config,ConfigurationError
from app.experiments import evaluate,KEYS
from app.market import Candle,CandleBatch
from app.research_filters import apply_filter,context
from app.research_store import ResearchStore
from app.logging_setup import configure,event
from app.tracing import score_composition
from scripts.validate_experiments import metrics,main
from tests.test_experiments import BASE,rows_for,archive_rows
from tests.test_system import setup,drain


def test_region_and_anchor_atr_are_frozen_before_breakout():
    rows=rows_for('breakout_retest');first=evaluate('breakout_retest',rows,now=rows[-1].t+62,interval=60)
    rows.append(Candle(rows[-1].t+60,101.4,102,101.25,101.95))
    second=evaluate('breakout_retest',rows,now=rows[-1].t+62,interval=60)
    assert first['accepted'] and second['accepted']
    assert first['region']==second['region'] and first['anchor_atr']==second['anchor_atr']
    assert first['setup_identity']==second['setup_identity']


def test_range_movement_identity_is_stable_and_strong_expansion_blocks_it():
    rows=rows_for('range_reversal');result=evaluate('range_reversal',rows,now=rows[-1].t+62,interval=60)
    assert result['accepted']
    from app.strategies.regime import classify as original
    def changing(cs):return 'tendencial' if len(cs)==len(rows) else original(cs)
    with patch('app.experiments.classify',side_effect=changing):
        assert not evaluate('range_reversal',rows,now=rows[-1].t+62,interval=60)['accepted']


def test_research_network_work_yields_to_operational_analysis(tmp_path):
    from unittest.mock import AsyncMock
    async def run():
        service,_=setup(tmp_path);service.research=ResearchStore(':memory:')
        service.analysis_active=True
        with patch.object(service.research,'step',new_callable=AsyncMock) as step:
            await service.tick();await drain(service)
            step.assert_not_called()
            service.analysis_active=False
            await service.tick();await drain(service)
            step.assert_called_once()
    asyncio.run(run())


def test_export_research_is_read_only_and_keeps_versions_separate(tmp_path,monkeypatch):
    from scripts.export_research import main as export
    path=tmp_path/'research.db';store=ResearchStore(str(path));record=capture(store)
    record=store.patch(record,state='settled',result='WIN',settled_at=record['expiry_at']+65,
        entry_price=100,expiry_price=101,settlement_source_identity=record['source_identity'],
        settlement_evidence=[Candle(record['entry_at'],100,100,100,100).json(),
                             Candle(record['expiry_at'],101,101,101,101).json()])
    store.db.close();before=path.read_bytes();out=tmp_path/'export'
    monkeypatch.setattr('sys.argv',['export','--database',str(path),'--output',str(out)])
    monkeypatch.setattr('scripts.export_research.time.time',lambda:record['settled_at'])
    export();report=json.loads((out/'summary.json').read_text())
    assert not report['production_enabled'] and report['groups'][0]['wins']==1
    assert report['groups'][0]['code_sha256'] and path.read_bytes()==before


def test_higher_filter_uses_only_closed_temporally_aligned_candles():
    higher=[Candle(BASE+i*300,100+i*.1,101+i*.1,99+i*.1,100+i*.1) for i in range(50)]
    now=higher[-1].t+302;rows=rows_for('breakout_retest');candidate={'direction':'CALL'}
    one=apply_filter('htf_alignment',rows,candidate,now=now,interval=60,higher=higher)
    opened=Candle(higher[-1].t+300,105,500,1,2)
    two=apply_filter('htf_alignment',rows,candidate,now=now,interval=60,higher=higher+[opened])
    assert one==two and one['passed'] and one['last_closed_at']<=now
    bad=apply_filter('htf_alignment',rows,candidate,now=now,interval=60,higher=higher+[replace(opened,t=opened.t+300)])
    assert bad['status']=='unavailable' and not bad['passed']
    assert apply_filter('htf_alignment',rows,candidate,now=now,interval=60)['status']=='unavailable'


def test_filters_are_normalized_and_absent_news_is_not_a_fabricated_event():
    rows=rows_for('breakout_retest');now=rows[-1].t+62
    scaled=[Candle(c.t,c.o*10,c.h*10,c.l*10,c.c*10) for c in rows]
    for name in ('volatility_band','stretch_ema','opposing_wick'):
        one=apply_filter(name,rows,{'direction':'CALL'},now=now,interval=60)
        two=apply_filter(name,scaled,{'direction':'CALL'},now=now,interval=60)
        assert one['passed']==two['passed'] and one['value']==pytest.approx(two['value'])
        assert one['score_bonus']==0
    ctx=context(rows,now,60)
    assert ctx['news']['status']=='unavailable' and ctx['hour_filter']['status']=='not_activated'


def capture(store,rows=None,variant='base',now=None,observed_at=None):
    rows=rows or rows_for('breakout_retest');now=now or rows[-1].t+62
    audit=evaluate('breakout_retest',rows,now=now,interval=60)
    return store.capture(audit,asset='TEST',source_key='original',source_identity='synthetic',rows=rows,
        interval=60,duration=60,now=now,observed_at=observed_at or now,variant=variant)


def test_prospective_is_persistent_and_movement_duplicates_do_not_restart(tmp_path):
    path=str(tmp_path/'research.db');store=ResearchStore(path);first=capture(store)
    assert first and first['state']=='pending_validation' and not first['sent_to_telegram']
    assert capture(store) is None
    assert capture(store,variant='volatility_band') is not None
    store.db.close();store=ResearchStore(path)
    assert capture(store) is None and len(store.rows())==2
    store.db.close()


def test_late_capture_and_restart_never_backdate_confirmation():
    store=ResearchStore(':memory:');rows=rows_for('breakout_retest');now=rows[-1].t+62
    late=capture(store,observed_at=now+90)
    assert late['state']=='cancelled' and late['result']=='CANCELADO'
    other=capture(store,variant='separate')
    async def run():await store.step({},lambda:other['entry_at'])
    asyncio.run(run())
    assert all(r['state']=='cancelled' for r in store.rows())
    assert not any('entry_price' in r for r in store.rows())


@pytest.mark.parametrize('direction',['CALL','PUT'])
@pytest.mark.parametrize('mode',['win','loss','tie','source','missing'])
def test_prospective_revalidation_and_exact_expiration_use_original_source(direction,mode):
    async def run():
        store=ResearchStore(':memory:');record=capture(store);record=store.patch(record,direction=direction)
        reference=record['evidence']['candles'][-1]['c']
        # The mirrored PUT geometry uses an upper invalidation level.
        if direction=='PUT':record=store.patch(record,invalidation=[reference+10])
        now=[record['entry_at']-45]
        class Provider:
            name='synthetic';identity='synthetic'
            async def candles(self,*args):
                t=int(now[0]//60)*60-60
                return CandleBatch([Candle(t-120+i*60,reference,reference+.1,reference-.1,reference) for i in range(3)],'synthetic')
            async def settlement_candles(self,*args):
                opening=100;delta=(1 if direction=='CALL' else -1)*(1 if mode=='win' else -1 if mode=='loss' else 0)
                expiry=record['expiry_at']+(60 if mode=='missing' else 0)
                return CandleBatch([Candle(record['entry_at'],opening,101,99,100),
                    Candle(expiry,opening+delta,opening+delta+1,opening+delta-1,opening+delta)],
                    'other' if mode=='source' else 'synthetic')
        providers={'original':Provider()}
        await store.step(providers,lambda:now[0]);saved=store.rows()[0]
        assert saved['state']=='pending_result' and saved['entry_validation']['status']=='confirmada'
        now[0]=record['expiry_at']+65;await store.step(providers,lambda:now[0]);saved=store.rows()[0]
        if mode in ('source','missing'):
            assert saved['state']=='pending_result' and saved['result'] is None
            now[0]=record['expiry_at']+601;await store.step(providers,lambda:now[0])
            assert store.rows()[0]['result']=='INDISPONIVEL'
        else:
            assert saved['result']=={'win':'WIN','loss':'LOSS','tie':'EMPATE'}[mode]
            assert saved['settlement_evidence'][1]['t']==record['expiry_at']
            assert saved['result_kind']=='experimental_indicativo' and not saved['broker_confirmed']
    asyncio.run(run())


def test_collection_failure_and_data_never_touch_operational_history(tmp_path):
    async def run():
        service,clock=setup(tmp_path);service.config.experimental_strategies=KEYS
        service.research=ResearchStore(str(tmp_path/'research.db'))
        with patch.object(service.research,'capture',side_effect=RuntimeError('test_failure')):
            service.start(['EUR/USD'],300);await drain(service)
        assert len(service.store.signals())==1 and service.telegram.calls==1
        emitted=service.store.signals()[0]
        trace=emitted['evidence']['decision_trace']
        assert trace['recorded_at']<emitted['expiry_at'] and trace['duration_seconds']==300
        assert trace['score_composition']['final_score']==emitted['score']
        assert not service.research.rows()
    asyncio.run(run())


def test_collection_config_is_disabled_and_separate_by_default(tmp_path):
    assert not Config().validate().experimental_collect and not Config().experimental_filters
    with pytest.raises(ConfigurationError):Config(experimental_collect=True).validate()
    with pytest.raises(ConfigurationError):Config(experimental_filters=('fake',)).validate()
    cfg=Config(database=str(tmp_path/'aurora.db'),experimental_collect=True,experimental_strategies=KEYS).validate()
    from app.service import Service
    from app.store import Store
    from tests.test_system import Feed,TG
    service=Service(cfg,Store(cfg.database),Feed(),TG())
    assert (tmp_path/'aurora.research.db').exists() and (tmp_path/'aurora.db').exists()
    assert not service.research.rows() and not service.store.signals()


def test_overlap_uncertainty_and_known_payout_are_reported_separately():
    def trade(at,result,payout=None):return {'asset':'TEST','created_at':at-60,'entry_at':at,'expiry_at':at+60,'settled_at':at+125,'result':result,'payout_pct':payout}
    trades=[trade(BASE,'WIN',80),trade(BASE+60,'LOSS',80),trade(BASE+180,'EMPATE',80)]
    summary=metrics(trades,3600)
    assert summary['evaluated']==2 and summary['non_overlapping']['evaluated']==1
    assert summary['non_overlapping']['excluded_overlaps']==1
    assert summary['expectancy_units']==pytest.approx(-.2/3,abs=1e-6)
    assert metrics([trade(BASE,'WIN')],3600)['expectancy_units'] is None


def test_audit_fields_are_not_dropped_and_no_fake_score_is_added(tmp_path):
    logger=configure(tmp_path)
    event('test.audit',rules_version='1.22.10',pipeline_version='1.22.10',limits={'hourly_limit':10},quality={'families':['structure']},audit={'checked_at':BASE})
    for handler in logger.handlers:handler.flush()
    row=json.loads((tmp_path/'aurora.log').read_text().splitlines()[-1])
    assert row['rules_version']=='1.22.10' and row['limits']['hourly_limit']==10 and row['audit']['checked_at']==BASE
    comp=score_composition({'score':85,'reasons':['Uma condição (70 pts)','Confirmação (15 pts)']})
    assert comp['reconciled'] and comp['unattributed_points']==0


def test_individual_filter_trials_and_matrix_are_registered_without_combining(tmp_path,monkeypatch):
    rows=archive_rows();path=tmp_path/'history.json'
    path.write_text(json.dumps({'start':BASE+6000,'end':BASE+18000,'source':'synthetic_test_only',
          'data':{'TEST':[c.json() for c in rows if c.t+60<=BASE+18000]}}))
    out=tmp_path/'result';monkeypatch.setattr('sys.argv',['validate','--input',str(path),'--output',str(out),
          '--matrix','--investigate-filters','volatility_band','stretch_ema'])
    # We test trial orchestration, not market performance, with explicitly empty results.
    def fake_replay(data,**kw):
        return {'strategy':kw['key'],'variant':kw['filter_name'] or 'base','interval_seconds':kw['interval'],
          'duration_seconds':kw['duration'],'bounds':kw['bounds'],'published':0,'wins':0,'losses':0,'ties':0,
          'cancelled':0,'win_rate_pct':None,'wilson_95_pct':None},[],[]
    with patch('scripts.validate_experiments.replay',side_effect=fake_replay):main()
    plan=json.loads((out/'plan.json').read_text());report=json.loads((out/'results.json').read_text())
    scenarios = 1 + len(KEYS) * 3
    assert len(plan['combinations'])==8 and len(plan['scenarios'])==scenarios
    assert len(report['groups'])==8*scenarios*2 and report['final_test_status']=='sealed'
    assert {g['variant'] for g in report['groups']}=={'base','volatility_band','stretch_ema'}
    assert (tmp_path/'research-trials.jsonl').exists()
