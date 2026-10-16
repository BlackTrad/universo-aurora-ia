"""Causal software tests with synthetic prices; no claim of trading efficacy."""
import json
import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.experiments import _touches, evaluate, KEYS, pivots
from app.market import Candle, CandleBatch
from app.main import create_app
from app.price_action_selection import select, SELECTOR, policy_snapshot
from app.research_store import ResearchStore
from app.selection_observer import SelectionObserver, config_snapshot, alternatives
from app.selection_context import structure_context
from app.outcome_evidence import research_metric_view
from app.validation_metrics import compare_selectors
from tests.test_accuracy_1260 import observed
from tests.test_experiments import rows_for
from tests.test_system import setup, drain, Feed, TG
from scripts.export_research import metric_rows


def test_continuous_contact_with_range_edge_is_one_visit():
    rows = [Candle(1800000000 + i * 60, 100.2, 100.5, 100, 100.3) for i in range(10)]
    assert _touches(rows, 100, .15) == [0]
    separated = rows[:2] + [Candle(rows[i].t, 101, 101.5, 100.5, 101.2) for i in range(2, 5)] + rows[5:]
    assert _touches(separated, 100, .15) == [0, 5]


def test_experimental_report_requires_price_proof_and_preserves_original():
    observer = SelectionObserver(':memory:')
    start = 1700000040
    session = observer.session(config_snapshot(Config(), {'provider': 'deriv'}), start - 100)
    good = {**observed(0, start=start), 'session_id': session['id'], 'configuration_id': session['id']}
    bad = {**observed(1, start=start), 'session_id': session['id'], 'configuration_id': session['id'],
           'settlement_source_identity': 'other'}
    with observer.db:
        for row in (good, bad):
            observer.db.execute('INSERT INTO observations VALUES(?,?,?)', (row['id'], row['created_at'], json.dumps(row)))
    original = observer.db.execute('SELECT payload FROM observations ORDER BY id').fetchall()
    report = observer.report(start + 3600)['sessions'][0]
    group = report['groups'][0]
    assert group['wins'] == 1 and group['unavailable'] == 1
    assert report['outcomes_without_quote_proof'] == 1
    assert observer.db.execute('SELECT payload FROM observations ORDER BY id').fetchall() == original
    observer.close()


def structural(regime='tendencial', direction='CALL', status='available'):
    return {'status':status,'regime':regime,'direction':direction}


def hypothesis(key='structure_pullback',direction='CALL',now=1800003000,**changes):
    result={'strategy':key,'accepted':True,'direction':direction,'version':'synthetic-only',
            'valid_until':now+180,'score':80,'experimental':True,'can_emit':False,
            'mandatory_criteria':[{'code':'synthetic_mandatory','passed':True}],
            'setup_candle':now-60,'atr':1,'invalidation':[90],'setup_identity':key}
    result.update(changes)
    return result


@pytest.mark.parametrize('direction',['CALL','PUT'])
@pytest.mark.parametrize('key,regime',[('structure_pullback','tendencial'),('breakout_retest','tendencial'),
                                      ('range_reversal','lateral'),
                                      ('breakout_retest','transicao')])
def test_declared_routes_do_not_use_scores_or_operational_priority(key,regime,direction):
    one=select([hypothesis(key,direction)],structural(regime,direction),now=1800003000,interval=60,min_lead=60)
    two=select([hypothesis(key,direction,score=9999)],structural(regime,direction),now=1800003000,interval=60,min_lead=60)
    assert one['candidate']['strategy_resolved']==two['candidate']['strategy_resolved']==key
    assert one['context_evidence']['selection_code']=='selected'
    assert not one['context_evidence']['policy']['score_used_for_selection']
    assert not one['candidate']['can_emit'] and not policy_snapshot()['operational_enabled']


@pytest.mark.parametrize('regime,key',[('lateral','structure_pullback'),('lateral','breakout_retest'),
                                      ('tendencial','range_reversal'),('transicao','range_reversal')])
def test_incompatible_regime_cannot_be_compensated_by_high_score(regime,key):
    decision=select([hypothesis(key,score=9999)],structural(regime),now=1800003000,interval=60,min_lead=60)
    assert decision['candidate'] is None
    assert decision['context_evidence']['candidate_checks'][0]['code']=='incompatible_regime'


@pytest.mark.parametrize('opposing',[False,True])
def test_ambiguity_and_directional_conflicts_are_explicit_and_order_independent(opposing):
    candidates=[hypothesis('breakout_retest'),hypothesis('breakout_retest','PUT' if opposing else 'CALL',score=100)]
    one=select(candidates,structural('transicao'),now=1800003000,interval=60,min_lead=60)
    two=select(candidates[::-1],structural('transicao'),now=1800003000,interval=60,min_lead=60)
    assert one['candidate'] is two['candidate'] is None
    assert one['reason']==two['reason']
    assert one['context_evidence']['selection_code']==('direction_conflict' if opposing else 'multiple_setups')


@pytest.mark.parametrize('changes,status,code',[
    ({'mandatory_criteria':[]},'available','mandatory_failed'),
    ({'mandatory_criteria':[{'passed':False}]},'available','mandatory_failed'),
    ({'valid_until':1800003001},'available','setup_expired'),
    ({},'insufficient','regime_unavailable'),
    ({'direction':'PUT'},'available','against_structure')])
def test_mandatory_gates_precede_selection(changes,status,code):
    decision=select([hypothesis(**changes)],structural(status=status),now=1800003000,interval=60,min_lead=60)
    assert decision['candidate'] is None
    assert decision['context_evidence']['candidate_checks'][0]['code']==code


@pytest.mark.parametrize('interval',[60,300])
def test_third_touch_next_open_is_not_shifted_to_make_it_actionable(interval):
    rows=rows_for('third_touch',interval);now=rows[-1].t+interval+2
    result=evaluate('third_touch',rows,now=now,interval=interval)
    decision=select([result],structural('lateral',None),now=now,interval=interval,min_lead=60)
    check=decision['context_evidence']['candidate_checks'][0]
    assert result['accepted'] and decision['candidate'] is None
    assert check['entry_at']==rows[-1].t+interval and check['code']=='insufficient_lead'


def test_same_evaluations_are_reused_and_open_candles_do_not_change_routing():
    rows=rows_for('structure_pullback');now=rows[-1].t+62
    with patch('app.experiments.evaluate',wraps=evaluate) as detector:
        choices=alternatives(rows,[],minimum=99,strict=True,context=('synthetic','TEST','M1',300),frozen=[],now=now)
        assert detector.call_count==len(KEYS)
    chosen=next(c for c in choices if c['selector']==SELECTOR)
    opened=Candle(rows[-1].t+60,100,999,1,100)
    assert evaluate('structure_pullback',rows,now=now,interval=60)==evaluate('structure_pullback',rows+[opened],now=now,interval=60)
    assert structure_context(rows,60,now)==structure_context(rows+[opened],60,now)
    assert chosen['context_evidence']['additional_api_calls']==0


def test_pivot_availability_is_close_time_not_confirmation_candle_open():
    rows=[Candle(1800000000+i*300,100,h,99,100) for i,h in enumerate([101,102,105,102,101])]
    assert pivots(rows)['high'][0]['confirmed_at']==rows[-1].t+300
    assert not pivots(rows[:-1])['high']


@pytest.mark.parametrize('change',[{'settlement_evidence':None},{'expiry_price':99},
                                   {'settled_at':True},{'settled_at':'invalid'},
                                   {'settlement_source_identity':'another'}])
def test_invalid_experimental_proofs_do_not_become_wins_or_raise(change):
    row={**observed(0),**change};original=deepcopy(row)
    view=research_metric_view(row,1800000000)
    assert view['state']=='unavailable' and view['result'] is None
    assert view['registered_result']=='WIN' and row==original


def test_future_result_and_prices_are_hidden_at_the_decision_cutoff():
    row=observed(0);view=research_metric_view(row,row['settled_at']-1)
    assert view['state']=='pending_result' and view['result'] is None
    assert view['registered_result'] is view['entry_price'] is view['expiry_price'] is view['settlement_evidence'] is None
    assert research_metric_view(row,row['settled_at'])['outcome_proof_status']=='price_verified_indicative'


def comparison_rows():
    one={**observed(0),'session_id':'one','source_key':'deriv','timeframe':'M5','duration_seconds':300}
    return one,{**one,'id':'other','selector':SELECTOR}


def test_paired_decisions_include_opposing_directions_but_exclusives_stay_separate():
    one,two=comparison_rows();two.update(direction='PUT',result='LOSS')
    comparison=compare_selectors([one,two],right=SELECTOR)
    assert comparison['common_signals']==0 and comparison['both_selected_decisions']==1
    assert comparison['opposing_direction_decisions']==comparison['decision_paired_resolved']==1
    assert comparison['decision_paired_difference_pp']==-100
    assert comparison['decision_paired_day_block_95_pp'] is None
    assert comparison['exclusive_left']['signals']==comparison['exclusive_right']['signals']==1


def test_providers_and_cancelled_decisions_are_not_merged_in_comparisons():
    one,two=comparison_rows();two['source_key']='coinbase'
    comparison=compare_selectors([one,two],right=SELECTOR)
    assert comparison['common_signals']==comparison['both_selected_decisions']==0
    two.update(source_key='deriv',state='cancelled',result='CANCELADO')
    comparison=compare_selectors([one,two],right=SELECTOR)
    assert comparison['common_signals']==0 and comparison['cancelled_right']==1
    assert comparison['exclusive_right']['signals']==0


def test_duplicate_or_inconsistent_common_outcome_invalidates_paired_estimate():
    one,two=comparison_rows()
    duplicates=compare_selectors([one,two,{**two,'id':'duplicate'}],right=SELECTOR)
    assert duplicates['duplicate_decision_identities']==1 and not duplicates['comparison_valid']
    assert duplicates['decision_paired_difference_pp'] is None
    mismatch=compare_selectors([one,{**two,'result':'LOSS'}],right=SELECTOR)
    assert mismatch['common_outcome_divergences']==1 and not mismatch['comparison_valid']
    assert mismatch['paired_difference_pp'] is mismatch['paired_day_block_95_pp'] is None


def test_region_break_and_recovery_before_entry_still_invalidates_setup():
    rows=rows_for('breakout_retest');now=rows[-1].t+62
    research=evaluate('breakout_retest',rows,now=now,interval=60)
    store=ResearchStore(':memory:')
    record=store.capture(research,asset='TEST',source_key='deriv',source_identity='synthetic',rows=rows,
                         interval=60,duration=300,now=now,observed_at=now,min_lead=120)
    at=record['entry_at']-45;last=int(at//60)*60-60;reference=rows[-1].c
    level=record['invalidation'][0]
    quotes=[Candle(last-120+i*60,reference,reference+.1,reference-.1,reference) for i in range(3)]
    quotes[1]=replace(quotes[1],l=level-.2,c=level-.1)
    class Provider:
        name='synthetic';identity='synthetic'
        async def candles(self,*args):return CandleBatch(quotes,'synthetic')
    asyncio.run(store.step({'deriv':Provider()},lambda:at))
    saved=store.rows()[0]
    assert saved['state']=='cancelled' and saved['result']=='CANCELADO'
    assert saved['entry_validation']['experimental_invalidation_violations'][0]['candle_at']==quotes[1].t
    assert quotes[-1].c>level  # Recovered price cannot erase the earlier breach.
    store.close()


def test_paired_uncertainty_uses_days_and_not_independent_simultaneous_signals():
    rows=[]
    for i in range(100):
        left={**observed(i,won=True),'session_id':'fixed'}
        right={**left,'id':'right-'+str(i),'selector':SELECTOR,'direction':'PUT','result':'LOSS'}
        rows.extend([left,right])
    report=compare_selectors(rows,right=SELECTOR)
    assert report['decision_paired_resolved']==100
    assert report['decision_paired_difference_pp']==-100
    assert report['decision_paired_day_block_95_pp']==[-100,-100]


def test_legacy_research_export_checks_prices_without_replacing_original_labels():
    good=observed(0);bad={**observed(1),'settlement_evidence':None}
    pending={**observed(2),'settled_at':1900000000}
    raw=deepcopy([good,bad,pending])
    projected=metric_rows([good,bad,pending],1800000000)
    assert [r['result'] for r in projected]==['WIN','INDISPONIVEL']
    assert [good,bad,pending]==raw


def test_router_metadata_dedup_originals_and_final_test_survive_restart(tmp_path):
    path=tmp_path/'selection.db';rows=rows_for('structure_pullback');now=rows[-1].t+62
    observer=SelectionObserver(path);settings={'provider':'deriv','assets':['TEST'],'timeframe':'M1','duration_seconds':300,'payout_pct':None}
    session=observer.session(config_snapshot(Config(),settings),now)
    decisions=alternatives(rows,[],minimum=99,strict=True,context=('synthetic','TEST','M1',300),frozen=[],now=now)
    decision=next(c for c in decisions if c['selector']==SELECTOR)
    args=dict(session=session,asset='TEST',source_key='deriv',source_identity='synthetic',rows=rows,
              interval=60,timeframe='M1',duration=300,now=now,observed_at=now,min_lead=60)
    record=observer.capture_selection(decision,**args)
    original=observer.db.execute('SELECT payload FROM prediction_origins').fetchone()[0]
    assert record['regime']==decision['regime'] and record['regime_method']==policy_snapshot()['regime_method']
    assert record['duration_seconds']==300 and record['timeframe']=='M1' and not record['sent_to_telegram']
    observer.close();observer=SelectionObserver(path)
    assert observer.capture_selection(decision,**args) is None
    report=observer.report(now+1)['sessions'][0]
    assert report['price_action_study']['evaluations']==1 and report['phases'][-1]['sealed']
    assert observer.db.execute('SELECT payload FROM prediction_origins').fetchone()[0]==original
    observer.close()


def test_enabling_collection_or_router_failure_cannot_change_operational_signals(tmp_path):
    async def run():
        baseline,_=setup(tmp_path/'baseline');observing,_=setup(tmp_path/'observing')
        for service in (baseline,observing):service.select_strategy('scan_all')
        observing.set_observation(True)
        for service in (baseline,observing):
            service.start(['EUR/USD'],300);await drain(service)
        left=baseline.store.signals()[0];right=observing.store.signals()[0]
        assert (left['direction'],left['score'],left['strategy_resolved'],left['entry_at'],left['expiry_at'])==(
                right['direction'],right['score'],right['strategy_resolved'],right['entry_at'],right['expiry_at'])
        assert baseline.telegram.calls==observing.telegram.calls==1
        assert baseline.provider.calls==observing.provider.calls
        assert any(r['selector']==SELECTOR for r in observing.observer.rows())
        assert not any(r['sent_to_telegram'] for r in observing.observer.rows())
        await observing.stop_analysis();before=len(observing.observer.rows())
        await observing.tick();await drain(observing)
        assert len(observing.observer.rows())==before
        failing,_=setup(tmp_path/'failure');failing.select_strategy('scan_all');failing.set_observation(True)
        with patch('app.selection_observer.select_price_action',side_effect=RuntimeError('synthetic-only')):
            failing.start(['EUR/USD'],300);await drain(failing)
        assert len(failing.store.signals())==1 and failing.telegram.calls==1
    for name in ('baseline','observing','failure'):(tmp_path/name).mkdir()
    asyncio.run(run())


def test_experimental_api_filters_selector_without_starting_or_sending(tmp_path):
    config=Config(database=str(tmp_path/'db'),selection_observation=True)
    with TestClient(create_app(config,Feed(),TG())) as client:
        service=client.app.state.service;rows=rows_for('structure_pullback');now=rows[-1].t+62;service.clock=lambda:now
        session=service.observer.session(config_snapshot(config,service.settings()),now)
        decisions=alternatives(rows,[],minimum=99,strict=True,context=('synthetic','TEST','M1',300),frozen=[],now=now)
        for decision in decisions:
            service.observer.capture_selection(decision,session=session,asset='TEST',source_key='deriv',source_identity='synthetic',
                rows=rows,interval=60,timeframe='M1',duration=300,now=now,observed_at=now,min_lead=60)
        report=client.get('/api/validation/session',params={'session_id':session['id']}).json()
        assert report['price_action_study']['status']=='experimental'
        response=client.get('/api/validation/groups',params={'scope':'experimental','session_id':session['id'],'selector':SELECTOR})
        assert response.status_code==200 and all(r['selector']==SELECTOR for r in response.json()['items'])
        assert not service.scan['active'] and service.telegram.calls==service.provider.calls==0
