"""Synthetic regressions only: no result here demonstrates market efficacy."""
import json
import pytest
from fastapi.testclient import TestClient
from app.accuracy_target import POLICY, assessment, phase_groups
from app.config import Config
from app.learning import choose_tie, profiles
from app.main import create_app
from app.outcome_evidence import research_outcome
from app.selection_observer import SelectionObserver, config_snapshot
from app.store import Store
from scripts.export_validation import report as export_report
from tests.test_evolution_1250 import signal
from tests.test_learning import records, CONTEXT, SCOPE
from tests.test_system import Feed, TG


def observed(i, won=True, start=1700000040):
    entry=start+(i//5)*86400+(i%5)*600
    price=101 if won else 99
    return {**records()[0], 'id':str(i),'created_at':entry-90,'entry_at':entry,
            'expiry_at':entry+300,'settled_at':entry+365,'entry_price':100,'expiry_price':price,
            'state':'settled','result':'WIN' if won else 'LOSS','selector':'reference_fixed',
            'settlement_entry_at':entry,'settlement_expiry_at':entry+300,
            'settlement_evidence':[{'t':entry,'o':100,'h':101,'l':99,'c':100},
                                   {'t':entry+300,'o':price,'h':102,'l':98,'c':100}]}


@pytest.mark.parametrize('change',[
    {'settlement_source_identity':'another source'}, {'settlement_entry_at':10080},
    {'settlement_expiry_at':10440}, {'created_at':200000}, {'settled_at':200000},
    {'entry_price':float('nan')}, {'settlement_pending':True}, {'state':'em andamento'}])
def test_unproven_future_or_pending_history_cannot_gain_bonus_or_veto(change):
    assert profiles([{**r,**change} for r in records()],100000)=={}


@pytest.mark.parametrize('field,value',[('source_key','coinbase'),('regime','tendencial'),
    ('configuration_id','changed'),('apex_setup','Order Block + FVG')])
def test_contexts_and_variants_never_pool_for_learning(field,value):
    rows=records()
    result=profiles(rows[:45]+[{**r,field:value} for r in rows[45:]],100000)
    assert len(result)==2
    assert not any(p['eligible'] or p['blocked'] for p in result.values())


def test_scope_is_required_and_unknown_legacy_context_cannot_change_choice():
    candidates=[{'direction':'CALL','strategy_resolved':'cci','score':90},
                {'direction':'CALL','strategy_resolved':'fibonacci','score':90}]
    learned=profiles(records('fibonacci'),100000)
    assert choose_tie(candidates,learned,CONTEXT,scope=SCOPE)['strategy_resolved']=='fibonacci'
    assert choose_tie(candidates,learned,CONTEXT)['strategy_resolved']=='cci'
    assert choose_tie(candidates,learned,CONTEXT,scope={**SCOPE,'regime':'tendencial'})['strategy_resolved']=='cci'
    assert not any(p['eligible'] for p in profiles([{**r,'configuration_id':None} for r in records()],100000).values())


def test_apex_variants_get_separate_observed_rates_after_projection_upgrade(tmp_path):
    store=Store(tmp_path/'db')
    for i in range(30):
        store.add_signal(signal(i,strategy_resolved='order_block',apex_setup='Order Block + FVG'),str(i))
    assert store.win_rate_snapshot(signal(80,strategy_resolved='order_block'),200000)['sample']==0
    assert store.win_rate_snapshot(signal(80,strategy_resolved='order_block',apex_setup='Order Block + FVG'),200000)['sample']==30
    original=store.db.execute('SELECT payload FROM signal_origins ORDER BY id').fetchall()
    store.db.execute("UPDATE metric_projection_version SET version='1.25.1-1'");store.db.commit();store.db.close()
    store=Store(tmp_path/'db')
    assert store.db.execute('SELECT payload FROM signal_origins ORDER BY id').fetchall()==original
    assert store.win_rate_snapshot(signal(80,strategy_resolved='order_block',apex_setup='Order Block + FVG'),200000)['sample']==30
    store.db.close()


def test_small_perfect_sample_is_not_evidence_of_ninety_percent():
    result=assessment([observed(i) for i in range(10)],policy=POLICY,cutoff=1800000000,complete=True)
    assert result['observed_rate_pct']==100
    assert result['status']=='insufficient' and not result['checks']['wilson_lower']
    assert result['automatic_promotion'] is False and result['probability_estimate'] is False


def test_ninety_observed_is_not_a_ninety_percent_lower_bound():
    result=assessment([observed(i,i%10!=9) for i in range(150)],policy=POLICY,cutoff=1800000000,complete=True)
    assert result['observed_rate_pct']==90 and result['checks']['observed_rate']
    assert not result['checks']['wilson_lower'] and result['status']=='criteria_not_met'


def test_large_synthetic_perfect_sample_passes_only_its_descriptive_phase():
    result=assessment([observed(i) for i in range(150)],policy=POLICY,cutoff=1800000000,complete=True)
    assert result['status']=='phase_criteria_met' and all(result['checks'].values())
    assert result['resolved_days']==30 and result['evaluated']==150
    assert assessment([observed(i) for i in range(150)],policy=POLICY,cutoff=1800000000,complete=False)['status']=='collecting'


def test_future_or_missing_quote_evidence_reduces_availability_without_faking_losses():
    rows=[observed(i) for i in range(150)]
    for row in rows[:15]:row['settlement_source_identity']='wrong'
    result=assessment(rows,policy=POLICY,cutoff=1800000000,complete=True)
    assert result['wins']==135 and result['losses']==0 and result['unavailable']==15
    assert result['availability_pct']==90 and not result['checks']['availability']
    assert result['unproven_outcomes']==15
    rows=[observed(i) for i in range(150)]
    for row in rows[:15]:row['settled_at']=1900000000
    result=assessment(rows,policy=POLICY,cutoff=1800000000,complete=True)
    assert result['pending']==15 and result['wins']==135 and result['availability_pct']==90


@pytest.mark.parametrize('kind',['missing','wrong_time','wrong_open','invalid_ohlc','future_close','other_source','bool_price','duplicate_quote'])
def test_research_price_proof_rejects_invalid_and_unavailable_quotes(kind):
    row=observed(0);quotes=row['settlement_evidence']
    if kind=='missing':row.pop('settlement_evidence')
    elif kind=='wrong_time':quotes[1]['t']+=60
    elif kind=='wrong_open':quotes[1]['o']=100.5
    elif kind=='invalid_ohlc':quotes[1]['h']=90
    elif kind=='future_close':row['settled_at']=row['expiry_at']+59
    elif kind=='other_source':row['settlement_source_identity']='other'
    elif kind=='bool_price':quotes[1]['o']=True
    elif kind=='duplicate_quote':quotes[1]=dict(quotes[0])
    assert not research_outcome(row,1800000000)


def test_overlapping_and_candidate_universe_records_do_not_qualify_the_target():
    rows=[observed(i) for i in range(150)]
    for row in rows:
        row['entry_at']=1700000040;row['expiry_at']=1700000340
        row['created_at']=1699999950;row['settled_at']=1700000405
        row['settlement_evidence']=[{**q,'t':1700000040+j*300} for j,q in enumerate(row['settlement_evidence'])]
    result=assessment(rows,policy=POLICY,cutoff=1800000000,complete=True)
    assert result['nonoverlapping_sample']==1 and not result['checks']['nonoverlap_sample']
    assert phase_groups([{**observed(0),'selector':'candidate:cci'}],policy=POLICY,cutoff=1800000000,complete=True)==[]


def test_expectation_remains_absent_without_payout_and_negative_subset_blocks():
    rows=[observed(i) for i in range(150)]
    assert assessment(rows,policy=POLICY,cutoff=1800000000,complete=True)['expected_roi_per_operation'] is None
    rows[-1]=observed(149,False);rows[-1]['payout_pct']=80;rows[-1]['payout_kind']='user_declared_estimate'
    result=assessment(rows,policy=POLICY,cutoff=1800000000,complete=True)
    assert result['expected_roi_per_operation']==-1 and not result['checks']['known_payout_expectation']


def test_phase_keeps_late_results_pending_and_final_test_is_sealed(tmp_path):
    observer=SelectionObserver(tmp_path/'shadow.db');start=1700000040
    session=observer.session(config_snapshot(Config(),{'provider':'deriv'}),start)
    phase_end=start+42*86400
    row={**observed(0,start=start+15*86400),'session_id':session['id'],'configuration_id':session['id'],
         'settled_at':phase_end+86400}
    with observer.db:observer.db.execute('INSERT INTO observations VALUES(?,?,?)',(row['id'],row['created_at'],json.dumps(row)))
    viewed=observer.report(phase_end+2*86400)['sessions'][0]
    validation=viewed['phases'][1]
    assert validation['groups'][0]['pending']==1 and validation['groups'][0]['wins']==0
    assert validation['target_groups'][0]['pending']==1 and validation['target_groups'][0]['availability_pct']==0
    assert viewed['phases'][2]['sealed'] and not viewed['phases'][2]['target_groups']
    assert observer.report(start+70*86400,include_final_test=True)['sessions'][0]['final_test_status']=='opened'
    observer.db.close()


def test_export_with_observations_is_read_only_and_does_not_rewrite_originals(tmp_path):
    path=tmp_path/'aurora.db';store=Store(path);store.add_signal(signal(),'one');store.db.close()
    observer=SelectionObserver(path.with_suffix('.selection.db'))
    observer.session(config_snapshot(Config(),{'provider':'deriv'}),100000);observer.db.close()
    before={p:p.read_bytes() for p in (path,path.with_suffix('.selection.db'))}
    payload=export_report(path)
    assert payload['observation']['sessions'][0]['final_test_status']=='sealed'
    assert all(p.read_bytes()==data for p,data in before.items())


def test_legacy_report_separates_unproven_labels_from_trusted_metrics(tmp_path):
    store=Store(tmp_path/'db')
    for i in range(30):
        row=signal(i)
        if i<10:row['settlement_source_identity']='wrong'
        store.add_signal(row,str(i))
    original=store.db.execute('SELECT payload FROM signal_origins ORDER BY id').fetchall()
    value=store.validation_report(200000)
    assert value['operational_indicative'][0]['wins']==30
    trusted=value['operational_price_verified'][0]
    assert trusted['wins']==20 and trusted['unavailable']==10 and trusted['evaluated']==20
    assert value['outcomes_without_quote_proof']==10
    assert store.db.execute('SELECT payload FROM signal_origins ORDER BY id').fetchall()==original
    store.db.close()


def test_exported_trusted_metrics_preserve_pending_and_known_cutoff(tmp_path):
    path=tmp_path/'db';store=Store(path)
    store.add_signal(signal(0,settled_at=200000),'future')
    store.add_signal(signal(1,settlement_entry_at=1),'bad')
    at=store.validation_report(100000)['operational_price_verified'][0]
    assert at['pending']==1 and at['unavailable']==1 and at['wins']==0
    store.db.close()
    exported=export_report(path)
    assert exported['operational_price_verified'][0]['wins']==1
    assert exported['outcomes_without_quote_proof']==1


def test_target_api_is_paged_rejects_bad_phase_and_never_starts_monitoring(tmp_path):
    cfg=Config(database=str(tmp_path/'db'),selection_observation=True)
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        service=client.app.state.service
        session=service.observer.session(config_snapshot(cfg,service.settings()),service.clock())
        response=client.get('/api/validation/target',params={'session_id':session['id']})
        assert response.status_code==200 and response.json()['policy']['target_pct']==90
        assert response.json()['items']==[] and response.json()['page_size']==10
        assert client.get('/api/validation/target',params={'session_id':session['id'],'phase':'future'}).status_code==422
        assert client.get('/api/validation/target',params={'session_id':session['id'],'phase':'test'}).json()['status']=='sealed'
        assert not service.scan['active'] and service.telegram.calls==0 and service.provider.calls==0
