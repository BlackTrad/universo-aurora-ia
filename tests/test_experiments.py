"""Synthetic causal and integration regressions; never market performance."""
import asyncio
from dataclasses import replace
import json
from unittest.mock import patch
import pytest
from app.config import Config, ConfigurationError
from app.experiments import KEYS, evaluate, pivots
from app.market import Candle
from app.strategies import STRATEGIES
from tests.test_system import setup, drain
from scripts.validate_experiments import load_history, metrics, partition, replay, main

BASE=1800000000


def rows_for(key,interval=60):
    if key == 'third_touch':
        from tests.test_third_touch import rows_for_third
        return rows_for_third(interval)
    cycle=[100,100.6,101,100.6,100,99.4,99,99.4]
    rows=[Candle(BASE+i*interval,c+.05,c+.4,c-.4,c) for i in range(48)
          for c in [cycle[i%8]+(i*.03 if key=='structure_pullback' else 0)]]
    extras={
        'structure_pullback':[(100.95,101.25,100.7,100.9),(101.05,101.65,101,101.55)],
        'breakout_retest':[(101.1,102,101,101.9),(101.4,102,101.25,101.95)],
        'sweep_structure':[(98.8,99.2,98.2,99),(100.9,102,100.8,101.8),(101.3,102,101.25,101.95)],
        'range_reversal':[(99,99.3,98.55,98.95),(99.1,99.85,99.05,99.8)]}
    return rows+[Candle(BASE+(len(rows)+i)*interval,*v) for i,v in enumerate(extras[key])]


@pytest.mark.parametrize('key',KEYS)
@pytest.mark.parametrize('interval',[60,300])
@pytest.mark.parametrize('direction',['CALL','PUT'])
def test_all_sequences_in_both_directions_and_timeframes(key,interval,direction):
    rows=rows_for(key,interval)
    if direction=='PUT':rows=[Candle(c.t,220-c.o,220-c.l,220-c.h,220-c.c) for c in rows]
    result=evaluate(key,rows,now=rows[-1].t+interval+2,interval=interval)
    assert result['accepted'] and result['direction']==direction
    assert result['can_emit'] is False and result['experimental']
    assert result['score_kind']=='heurístico; não probabilidade'
    assert result['sequence']['confirmation_at']==rows[-1].t
    preceding=max(t for k,t in result['sequence'].items() if k not in ('confirmation_at','confirmation_closed_at'))
    assert preceding<=rows[-1].t
    if key!='third_touch':assert preceding<rows[-1].t
    assert max(result['sequence'].values())<=rows[-1].t+interval
    assert result['region'][0]<result['region'][1]


@pytest.mark.parametrize('key',KEYS)
def test_unfinished_candle_cannot_change_decision(key):
    rows=rows_for(key);now=rows[-1].t+62
    original=evaluate(key,rows,now=now,interval=60)
    assert original==evaluate(key,rows+[Candle(rows[-1].t+60,100,140,50,60)],now=now,interval=60)


@pytest.mark.parametrize('mode',['gap','duplicate','future','stale','invalid','unsorted'])
def test_bad_feed_rejects_without_synthetic_repair(mode):
    rows=rows_for('breakout_retest');now=rows[-1].t+62
    if mode=='gap':rows.pop(25)
    if mode=='duplicate':rows[25]=rows[24]
    if mode=='future':rows.append(Candle(now+60,100,101,99,100))
    if mode=='stale':now+=60
    if mode=='invalid':rows[25]=replace(rows[25],c=float('nan'))
    if mode=='unsorted':rows.reverse()
    result=evaluate('breakout_retest',rows,now=now,interval=60)
    assert not result['accepted'] and result['reason']


def test_pivot_confirmation_requires_two_completed_right_hand_bars():
    rows=[Candle(BASE+i*60,100,h,99,100) for i,h in enumerate([101,102,105,102,101])]
    assert not pivots(rows[:-1])['high']
    p=pivots(rows)['high'][0]
    assert p['at']==rows[2].t and p['confirmed_at']==rows[4].t+60


@pytest.mark.parametrize('key',['breakout_retest'])
def test_breakout_candle_is_not_its_own_retest(key):
    rows=rows_for(key)[:-1]
    assert not evaluate(key,rows,now=rows[-1].t+62,interval=60)['accepted']


def test_repeated_retest_keeps_same_setup_identity():
    rows=rows_for('breakout_retest')
    first=evaluate('breakout_retest',rows,now=rows[-1].t+62,interval=60)
    rows.append(Candle(rows[-1].t+60,101.4,102,101.25,101.95))
    second=evaluate('breakout_retest',rows,now=rows[-1].t+62,interval=60)
    assert first['accepted'] and second['accepted'] and first['setup_identity']==second['setup_identity']


def test_confirmation_cannot_inflate_its_own_atr_and_allow_a_spike():
    rows=rows_for('breakout_retest');rows[-1]=replace(rows[-1],h=110,c=109)
    result=evaluate('breakout_retest',rows,now=rows[-1].t+62,interval=60)
    assert not result['accepted'] and result['checks'][0]['code']=='confirmation_geometry'


@pytest.mark.parametrize('key',KEYS)
def test_invalidated_sequences_are_rejected_and_volume_is_unused(key):
    rows=rows_for(key);now=rows[-1].t+62
    assert evaluate(key,rows,now=now,interval=60)==evaluate(key,[replace(c,v=1_000_000) for c in rows],now=now,interval=60)
    if key=='structure_pullback':
        rows[-2]=replace(rows[-2],l=99.5,c=99.6)
    elif key=='sweep_structure':
        rows.insert(-1,Candle(rows[-1].t,98.4,99,98,98.1))
        rows[-1]=replace(rows[-1],t=rows[-1].t+60)
    elif key=='breakout_retest':
        rows.insert(-1,Candle(rows[-1].t,101.2,101.3,101,101.1))
        rows[-1]=replace(rows[-1],t=rows[-1].t+60)
    else:
        rows[-2]=replace(rows[-2],l=97.5)
    result=evaluate(key,rows,now=rows[-1].t+62,interval=60)
    assert not result['accepted'] and result['reason']


def test_production_registry_and_config_exclude_experimental_emission():
    assert not set(KEYS)&STRATEGIES.keys()
    assert not Config().validate().experimental_strategies
    assert Config(experimental_strategies=KEYS).validate()
    for value in (('unknown',),(KEYS[0],KEYS[0]),[KEYS[0]]):
        with pytest.raises(ConfigurationError):Config(experimental_strategies=value).validate()


def test_shadow_has_no_effect_on_operational_signals_or_telegram(tmp_path):
    async def run():
        baseline,_=setup(tmp_path/'base');shadow,_=setup(tmp_path/'shadow')
        shadow.config.experimental_strategies=KEYS
        with patch('app.service.event') as events:
            for service in (baseline,shadow):
                service.start(['EUR/USD'],300);await drain(service)
            research=[c.kwargs for c in events.call_args_list if c.args==('experiment.shadow_evaluated',)]
        a=baseline.store.signals();b=shadow.store.signals()
        assert len(a)==len(b)==1 and baseline.telegram.calls==shadow.telegram.calls==1
        assert (a[0]['direction'],a[0]['score'],a[0]['strategy_resolved'])==(b[0]['direction'],b[0]['score'],b[0]['strategy_resolved'])
        assert len(research)==len(KEYS) and all(not r['candidate']['can_emit'] for r in research)
        assert not set(KEYS)&{t['strategy_resolved'] for t in b}
    (tmp_path/'base').mkdir();(tmp_path/'shadow').mkdir();asyncio.run(run())


def test_shadow_failure_cannot_stop_production(tmp_path):
    async def run():
        service,_=setup(tmp_path);service.config.experimental_strategies=KEYS
        with patch('app.experiments.shadow',side_effect=RuntimeError('synthetic_failure')),patch('app.service.event') as events:
            service.start(['EUR/USD'],300);await drain(service)
            assert any(c.args==('experiment.shadow_failed',) for c in events.call_args_list)
        assert len(service.store.signals())==1 and service.telegram.calls==1
    asyncio.run(run())


def archive_rows():
    return [Candle(BASE+i*60,100,100.4,99.6,100+.1*(i%2)) for i in range(320)]


def fake_candidate(rows,now,interval):
    return {'accepted':True,'direction':'CALL','score':80,'setup_candle':rows[-1].t,
            'atr':1,'regime':'lateral','version':'synthetic_test_only',
            'invalidation':[90],'valid_until':now+180,'setup_identity':str(rows[-1].t)}


def test_chronological_replay_purges_labels_and_ignores_future_prices():
    rows=archive_rows();bounds=[BASE+6000,BASE+9000]
    with patch('scripts.validate_experiments.evaluate',side_effect=lambda key,cs,**kw:fake_candidate(cs,**kw)):
        one=replay({'TEST':rows},key=KEYS[0],bounds=bounds,interval=60,duration=60)
        mutated=[c if c.t<bounds[1] else replace(c,o=1000,h=1001,l=999,c=1000) for c in rows]
        two=replay({'TEST':mutated},key=KEYS[0],bounds=bounds,interval=60,duration=60)
    assert one==two and one[0]['blocks']['purged_boundary']>0
    assert all(t['expiry_at']+65<bounds[1] for t in one[1])


def test_risk_duplicate_and_cancellation_do_not_leak_future_results():
    rows=archive_rows();bounds=[BASE+6000,BASE+9000]
    def repeated(key,cs,**kw):return {**fake_candidate(cs,**kw),'setup_identity':'one_setup'}
    with patch('scripts.validate_experiments.evaluate',side_effect=repeated):
        summary,trades,_=replay({'TEST':rows},key=KEYS[0],bounds=bounds,interval=60,duration=60)
    assert len(trades)==1 and summary['blocks']['duplicate']>0
    # A future adverse M1 quote cannot cancel a signal before its check time.
    rows=[replace(c,o=99,h=99.4,l=98.6,c=99) if c.t>=BASE+6060 else c for c in rows]
    with patch('scripts.validate_experiments.evaluate',side_effect=lambda key,cs,**kw:fake_candidate(cs,**kw)):
        summary,trades,_=replay({'TEST':rows},key=KEYS[0],bounds=bounds,interval=60,duration=60,hourly_limit=3)
    assert summary['published']==3 and summary['cancelled']>=1 and summary['blocks']['hourly_limit']>0


def test_empty_and_duplicate_history_rejected_and_metrics_report_no_rate(tmp_path):
    path=tmp_path/'history.json';raw={'start':BASE,'end':BASE+3600,'source':'synthetic_test_only','data':{'TEST':[]}}
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='vazio'):load_history(path)
    raw['data']['TEST']=[archive_rows()[0].json()]*2;path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='Duplicidade'):load_history(path)
    assert metrics([],3600)['win_rate_pct'] is None
    assert partition(BASE,BASE+6000)=={'development':[BASE,BASE+3600],'validation':[BASE+3600,BASE+4800],'test':[BASE+4800,BASE+6000]}


def test_default_cli_seals_test_and_freeze_rejects_code_or_parameters(tmp_path,monkeypatch):
    path=tmp_path/'history.json';rows=archive_rows()
    path.write_text(json.dumps({'start':BASE+6000,'end':BASE+18000,'source':'synthetic_test_only',
                               'data':{'TEST':[c.json() for c in rows if c.t+60<=BASE+18000]}}))
    out=tmp_path/'dev';monkeypatch.setattr('sys.argv',['validate','--input',str(path),'--output',str(out)])
    main();report=json.loads((out/'results.json').read_text())
    assert report['final_test_status']=='sealed'
    assert {g['fold'] for g in report['groups']}=={'development','validation'}
    monkeypatch.setattr('sys.argv',['validate','--input',str(path),'--output',str(tmp_path/'valid-final'),
                                  '--holdout-plan',str(out/'plan.json')])
    main();final=json.loads((tmp_path/'valid-final/results.json').read_text())
    assert final['final_test_status']=='evaluated' and {g['fold'] for g in final['groups']}=={'test'}
    plan=out/'plan.json';frozen=json.loads(plan.read_text());frozen['code_sha256']='changed';plan.write_text(json.dumps(frozen))
    monkeypatch.setattr('sys.argv',['validate','--input',str(path),'--output',str(tmp_path/'final'),
                                  '--holdout-plan',str(plan)])
    with pytest.raises(SystemExit):main()
    assert not (tmp_path/'final').exists()
