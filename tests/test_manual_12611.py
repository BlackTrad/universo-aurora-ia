"""Illustrated OHLC regressions, temporal integrity and removal of active SMC."""
import asyncio
from copy import deepcopy
import math

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from app.manual_price_action import CATALOG,VERSION,evaluate,evaluate_all,snapshot
from app.market import Candle,DataError
from app.service import Service
from app.setup_contract import check_setup,movement_id
from app.signal_visuals import annotations
from app.store import Store
from app.strategies import ACTIVE_KEYS,RETIRED_KEYS,STRATEGIES,scan_all,select_reference
from tests.manual_fixtures import fixture,put


@pytest.mark.parametrize('key',CATALOG)
@pytest.mark.parametrize('direction',['CALL','PUT'])
def test_every_manual_strategy_has_a_closed_ohlc_example_in_both_directions(key,direction):
    rows=fixture(key)
    if direction=='PUT':rows=[Candle(c.t,220-c.o,220-c.l,220-c.h,220-c.c) for c in rows]
    before=deepcopy(rows)
    candidate,reason=evaluate(key,rows)
    assert candidate is not None,reason
    assert candidate['direction']==direction and candidate['manual']
    assert candidate['manual_source_pages']==CATALOG[key][1]
    contract=candidate['setup_contract']
    assert contract['version']==VERSION and contract['engine']==key
    assert contract['confirmed_at']==rows[-1].t+300
    assert contract['entry_window_start']==rows[-1].t+300
    assert contract['entry_window_end']==rows[-1].t+600
    assert contract['valid_until']<contract['entry_window_end']
    assert all(t<=rows[-1].t for t in contract['anchors'])
    assert contract['geometry']['known_at']<=contract['confirmed_at']
    assert rows==before
    assert all(c['passed'] is True for c in candidate['mandatory_criteria'])


@pytest.mark.parametrize('key',CATALOG)
@pytest.mark.parametrize('bad',['gap','duplicate','nan','invalid_ohlc','short'])
def test_invalid_or_incomplete_input_never_generates_a_manual_candidate(key,bad):
    rows=fixture(key)
    if bad=='gap':rows.pop(20)
    elif bad=='duplicate':rows[20]=rows[19]
    elif bad=='nan':rows[20]=Candle(rows[20].t,math.nan,101,99,100)
    elif bad=='invalid_ohlc':rows[20]=Candle(rows[20].t,100,99,101,100)
    else:rows=rows[:20]
    candidate,reason=evaluate(key,rows)
    assert candidate is None and reason


def test_breakout_half_measures_the_frozen_mark_not_fibonacci():
    candidate,_=evaluate('pa_breakout_50',fixture('pa_breakout_50'))
    assert candidate['manual_geometry']['breakout_fraction']==pytest.approx(.5)
    assert 'retracement_ratio' not in candidate['setup_contract']
    assert snapshot()['percentage_basis'].startswith('portion of the real body')


def test_weak_breakout_with_large_upper_wick_is_rejected():
    rows=fixture('pa_breakout_50');put(rows,78,(103.05,104.2,103.03,103.45))
    assert evaluate('pa_breakout_50',rows)[0] is None


def test_ninety_percent_requires_four_separate_relief_candles():
    rows=fixture('pa_breakout_90')
    candidate,_=evaluate('pa_breakout_90',rows)
    assert candidate['manual_geometry']['relief_candles']==4
    rows.pop(75)
    rows=[Candle(rows[0].t+i*300,c.o,c.h,c.l,c.c) for i,c in enumerate(rows)]
    assert evaluate('pa_breakout_90',rows)[0] is None


@pytest.mark.parametrize('direction',['CALL','PUT'])
def test_multiple_engulfing_also_covers_the_two_candle_example(direction):
    rows=fixture('pa_engulfing_three');put(rows,76,(100.1,100.45,100.0,100.2))
    if direction=='PUT':rows=[Candle(c.t,220-c.o,220-c.l,220-c.h,220-c.c) for c in rows]
    candidate,reason=evaluate('pa_engulfing_three',rows)
    assert candidate and candidate['direction']==direction,reason


def test_compression_breakout_waits_for_a_distinct_retest():
    assert evaluate('pa_flow_compression',fixture('pa_flow_compression'))[0]
    assert evaluate('pa_compression_breakout',fixture('pa_flow_compression'))[0] is None
    assert evaluate('pa_compression_breakout',fixture('pa_compression_breakout'))[0]


def test_deep_breakout_wait_applies_to_generic_retest_variants_too():
    rows=fixture('pa_pullback_harami');put(rows,78,(103.21,103.63,103.19,103.61))
    assert evaluate('pa_pullback_harami',rows)[0] is None


def test_interference_even_immediately_above_current_price_is_not_ignored():
    rows=fixture('pa_micro_exhaustion');put(rows,79,(102.7,104.03,102.65,103.98))
    candidate,reason=evaluate('pa_micro_exhaustion',rows)
    assert candidate is None and 'interferência' in reason


@pytest.mark.parametrize('at',[0,-300,True,1791000000.0,'timestamp'])
def test_invalid_timestamp_types_are_rejected_before_geometry(at):
    rows=fixture('pa_hammer');rows[-1]=Candle(at,100.05,100.2,99.75,100.15)
    assert all(candidate is None for candidate,reason in evaluate_all(rows).values())


def test_unknown_contract_version_cannot_revalidate_an_entry():
    s=signal();s['setup_contract']['version']='unknown'
    with pytest.raises(DataError):check_setup(s,[],s['entry_at']-45)


def test_pipeline_has_no_hidden_smc_strategy_in_diagnostics():
    audit={};scan_all(fixture('pa_hammer'),audit=audit)
    assert len(audit['evaluations'])==40
    assert not RETIRED_KEYS.intersection(e['strategy'] for e in audit['evaluations'])


def test_causal_learning_can_only_choose_variations_of_the_same_entry():
    from app.learning import selection_profile_key
    rows=fixture('pa_hammer');a,_=evaluate('pa_hammer',rows);b=deepcopy(a)
    b['strategy_resolved']='pa_battle_reversal'
    context=('test:manual','EUR/USD','M5',300)
    scope={'source_key':'deriv','regime':'lateral','configuration_id':'synthetic-learning'}
    learned={selection_profile_key(context,a,scope):{'eligible':True,'blocked':False,'validation_lower_bound':.9}}
    chosen,reason=select_reference(rows,[a,b],learned=learned,context=context,scope=scope)
    assert chosen['strategy_resolved']=='pa_hammer' and chosen['learning']['applied'] and not reason
    assert chosen['learning']['preference_bonus']<=3 and chosen['score']==80
    b['setup_contract']['levels']['manual_region']+=10
    assert select_reference(rows,[a,b],learned=learned,context=context,scope=scope)[0] is None


def test_interference_prevents_chasing_the_micro_break():
    rows=fixture('pa_micro_exhaustion');put(rows,79,(102.7,103.9,102.65,103.85))
    candidate,reason=evaluate('pa_micro_exhaustion',rows)
    assert candidate is None and 'interferência' in reason


def test_v_requires_a_separate_old_visit_and_distancing():
    rows=fixture('pa_v_range');put(rows,69,(100.0,100.3,99.75,100.1))
    assert evaluate('pa_v_range',rows)[0] is None


def test_wick_symmetry_requires_all_four_previous_closed_candles():
    rows=fixture('pa_wick_retracement');put(rows,76,(100.1,100.4,100.05,100.35))
    assert evaluate('pa_wick_retracement',rows)[0] is None


def test_a_head_shoulders_drawing_without_neckline_break_and_retest_is_not_a_signal():
    rows=fixture('pa_head_shoulders');put(rows,78,(100.3,100.7,100.1,100.5))
    assert evaluate('pa_head_shoulders',rows)[0] is None


def test_removed_smc_not_registered_or_in_the_automatic_pool():
    assert not RETIRED_KEYS.intersection(STRATEGIES)
    from app.manual_book import CATALOG as BOOK_CATALOG
    assert tuple(ACTIVE_KEYS)==(*BOOK_CATALOG,'fibonacci')
    for key in RETIRED_KEYS:
        assert Config(strategy=key).validate().strategy=='scan_all'
    cfg=Config(experimental_strategies=('sweep_structure',),experimental_collect=True).validate()
    assert not cfg.experimental_strategies and cfg.experimental_collect is False


def test_manual_mandatory_criteria_are_not_filtered_by_legacy_score_minimum():
    rows=fixture('pa_hammer');candidate,_=evaluate('pa_hammer',rows)
    result,reason=select_reference(rows,[candidate],minimum=100,strict=True)
    assert result is not None and not reason and result['score']==80
    candidate['mandatory_criteria'][0]['passed']=False
    assert select_reference(rows,[candidate],minimum=70)[0] is None


def test_same_entry_variations_do_not_count_as_votes_or_probability():
    rows=fixture('pa_engulfing');a,_=evaluate('pa_engulfing',rows);b=deepcopy(a)
    b.update(strategy_resolved='pa_engulfing_three',score=1)
    result,reason=select_reference(rows,[a,b])
    assert not reason and result['strategy_resolved']=='pa_engulfing_three'
    assert result['quality']['independent_confirmation'] is False
    b['setup_contract']['levels']['manual_region']+=10
    assert select_reference(rows,[a,b])[0] is None
    b['direction']='PUT'
    assert select_reference(rows,[a,b])[0] is None


def signal(key='pa_hammer'):
    rows=fixture(key);candidate,_=evaluate(key,rows)
    entry=rows[-1].t+420
    return {**candidate,'id':'manual','source_key':'deriv','source_identity':'test:manual',
            'asset':'EUR/USD','timeframe':'M5','duration_seconds':300,'entry_at':entry,'expiry_at':entry+300,
            'state':'aguardando entrada','evidence':{'candles':[c.json() for c in rows]}}


def test_frozen_geometry_supports_duplicate_guard_chart_and_original_invalidation():
    s=signal();assert movement_id(s)
    picture=annotations(s)
    assert picture['zones'][0]['label']=='Região original do manual'
    assert picture['levels'][0]['label']=='Região do manual'
    assert picture['entry_price_inferred'] is False
    before=deepcopy(s)
    contract=s['setup_contract'];at=contract['confirmed_at']
    fresh=[Candle(at,100.05,100.2,99.75,100.15)]
    audit,reason=check_setup(s,fresh,at+60)
    assert not reason and audit['setup_contract_version']==VERSION
    below=contract['invalidations'][0]['price']-.1
    bad=[Candle(at,below,below+.1,below-.1,below)]
    assert 'invalidada' in check_setup(s,bad,at+60)[1]
    assert s==before
    s['entry_at']=contract['entry_window_end']
    assert 'vencido' in check_setup(s,[],at+60)[1]


def test_prior_smc_signal_can_finish_its_original_generic_invalidation_without_a_new_engine():
    s=signal();s['setup_contract']={'version':'1.26.2-setup-1','direction':'CALL','engine':'order_block',
        'confirmed_at':1000,'levels':{'order_block_boundary':99},'invalidations':[{'level':'order_block_boundary',
        'price':99,'mode':'close_below','inclusive':False}],'anchors':[700],'movement_family':'order_block',
        'valid_until':None}
    _,reason=check_setup(s,[Candle(1020,100,101,99.5,100.5)],1100)
    assert not reason
    assert check_setup(s,[Candle(1020,98,99,97,98)],1100)[1]


def test_catalog_api_is_read_only_and_removed_management_is_unavailable():
    with TestClient(create_app(Config(database=':memory:'))) as client:
        before=client.get('/api/state').json()
        catalog=client.get('/api/manual/strategies').json()
        from app.manual_book import CATALOG as BOOK_CATALOG
        assert len(catalog['strategies'])==39 and {s['key'] for s in catalog['strategies']}==set(BOOK_CATALOG)
        assert client.get('/api/manual/management?capital=100&payout_pct=87').status_code==404
        assert client.get('/api/manual/management?capital=nan&payout_pct=87').status_code==404
        assert client.put('/api/strategy',json={'strategy':'order_block'}).status_code==422
        after=client.get('/api/state').json()
        assert before['settings']==after['settings'] and not after['scan']['active'] and not after['signals']


class Feed:
    name='TESTE SINTÉTICO';identity='test:manual';assets=['EUR/USD'];timeframes=['M1','M5']
    def __init__(self,rows):self.rows=rows
    async def candles(self,asset,interval=300,count=100):return self.rows


class Telegram:
    configured=False
    async def send(self,signal):return {'status':'não configurado'}


@pytest.mark.parametrize('interval',[60,300])
def test_default_pipeline_respects_original_next_candle_window_and_manual_lead(interval):
    async def run():
        original=fixture('pa_hammer')
        base=original[0].t
        rows=[Candle(base+i*interval,c.o,c.h,c.l,c.c) for i,c in enumerate(original)]
        now=rows[-1].t+interval+2
        cfg=Config(database=':memory:');store=Store(':memory:');feed=Feed(rows)
        s=Service(cfg,store,feed,Telegram(),clock=lambda:now,automatic_only=True)
        store.save_settings({**s.settings(),'minimum_score':100})
        s.start(['EUR/USD'],300,'M1' if interval==60 else 'M5')
        while s.tasks:await asyncio.gather(*list(s.tasks))
        signals=store.signals()
        if interval==60:
            assert not signals and any(c.get('code')=='setup_expired' for c in store.analyses()[0]['checks'])
        else:
            assert signals and all(c['manual'] for c in signals)
            assert all(c['entry_at']<c['setup_contract']['entry_window_end'] for c in signals)
            assert all(c['entry_at']-now>=60 for c in signals)
            from app.manual_book import VERSION as BOOK_VERSION
            assert all(c['evidence']['rules_version']==BOOK_VERSION for c in signals)
        store.db.close()
    asyncio.run(run())
