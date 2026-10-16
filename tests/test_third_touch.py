"""Synthetic geometry and isolation checks; never evidence of win rate."""
import asyncio
from dataclasses import replace

import pytest

from app.experiments import evaluate, KEYS
from app.market import Candle
from app.third_touch import invalidation_limit, snapshot
from app.config import Config
from app.selection_observer import SelectionObserver, alternatives, config_snapshot
from app.research_store import ResearchStore
from app.validation_metrics import grouped, summarize

BASE = 1800000000


def rows_for_third(interval=60, diagonal=False):
    rows = []
    for i in range(48):
        level = 100 + (.04 * (i - 24) if diagonal else 0)
        values = (level + .85, level + 1.30, level + .45, level + .80)
        if i in (24, 34):
            values = (level + .65, level + 1.00, level, level + .60)
        elif i == 47:
            values = (level + .55, level + .75, level + .02, level + .04)
        rows.append(Candle(BASE + i * interval, *values))
    return rows


def decision(rows, interval=60):
    return evaluate('third_touch', rows, now=rows[-1].t + interval + 2, interval=interval)


@pytest.mark.parametrize('interval', [60, 300])
@pytest.mark.parametrize('diagonal', [False, True])
@pytest.mark.parametrize('direction', ['CALL', 'PUT'])
def test_third_visit_has_frozen_anchors_in_both_directions(interval, diagonal, direction):
    rows = rows_for_third(interval, diagonal)
    if direction == 'PUT':
        rows = [Candle(c.t, 220-c.o, 220-c.l, 220-c.h, 220-c.c) for c in rows]
    result = decision(rows, interval)
    assert result['accepted'], result
    assert result['direction'] == direction
    assert result['touch_count'] == 3
    expected = ('LTA' if direction == 'CALL' else 'LTB') if diagonal else ('suporte' if direction == 'CALL' else 'resistência')
    assert result['level_kind'] == expected
    assert result['sequence']['first_touch_at'] == rows[24].t
    assert result['sequence']['second_touch_at'] == rows[34].t
    assert result['line']['defined_at'] == rows[36].t + interval
    assert result['line']['first_anchor']['confirmed_at']==rows[26].t+interval
    assert result['line']['second_anchor']['confirmed_at']==result['line']['defined_at']
    assert result['line']['defined_at'] <= rows[46].t
    assert result['sequence']['third_touch_at'] == rows[47].t
    assert result['sequence']['confirmation_at'] == rows[47].t
    assert result['sequence']['confirmation_closed_at']==rows[47].t+interval
    assert result['target_entry_at'] == rows[47].t+interval
    assert result['version'] == 'third-touch-1.0.0'
    assert not result['can_emit'] and result['experimental']
    assert result['invalidation'][0] == pytest.approx(invalidation_limit(result['invalidation_line'], rows[-1].t))


@pytest.mark.parametrize('mode', ['adjacent', 'no_departure', 'broken', 'unconfirmed', 'fourth', 'close_off_zone', 'spike', 'late'])
def test_invalid_or_consumed_visit_is_not_a_new_opportunity(mode):
    rows = rows_for_third()
    if mode == 'adjacent':
        for i in (43, 44, 45, 46):
            rows[i] = Candle(rows[i].t, 100.4, 100.65, 100.03, 100.5)
    elif mode == 'no_departure':
        for i in range(35, 47):
            rows[i] = Candle(rows[i].t, 100.2, 100.5, 100.18, 100.3)
    elif mode == 'broken':
        rows[40] = Candle(rows[40].t, 100.2, 100.3, 99.5, 99.6)
    elif mode == 'unconfirmed':
        rows[34] = replace(rows[34], l=100.45)
        rows[45] = replace(rows[45], l=100)
    elif mode == 'fourth':
        rows[40] = Candle(rows[40].t, 100.55, 100.7, 100.02, 100.6)
    elif mode == 'close_off_zone':
        rows[-1] = Candle(rows[-1].t, 100.55, 100.7, 100.02, 100.6)
    elif mode == 'spike':
        rows[-1] = Candle(rows[-1].t, 100.55, 110, 100.47, 109)
    elif mode == 'late':
        rows.append(Candle(rows[-1].t+60, 100.6, 100.7, 100.5, 100.65))
    assert not decision(rows)['accepted']


def test_confirmation_cannot_reanchor_line_or_change_its_atr():
    rows = rows_for_third(diagonal=True)
    first = decision(rows)
    rows[-1] = replace(rows[-1], h=rows[-1].h+.04, c=rows[-1].c+.02)
    second = decision(rows)
    assert first['accepted'] and second['accepted']
    assert first['line'] == second['line']
    assert first['anchor_atr'] == second['anchor_atr']
    assert first['setup_identity'] == second['setup_identity']


def test_unrelated_intermediate_pivot_does_not_hide_two_level_anchors():
    rows = rows_for_third()
    rows[29] = replace(rows[29], l=100.35)
    result = decision(rows)
    assert result['accepted'], result
    assert result['line']['first_anchor']['at'] == rows[24].t
    assert result['line']['second_anchor']['at'] == rows[34].t


def test_extra_visit_between_anchors_cannot_be_relabelled_as_second():
    rows = rows_for_third()
    # A two-candle low is not a strict pivot, but is an independent visit.
    for i in (29, 30):
        rows[i] = replace(rows[i], l=100.02)
    assert not decision(rows)['accepted']


def test_future_and_open_candles_do_not_change_frozen_decision():
    rows = rows_for_third(); now = rows[-1].t+62
    one = evaluate('third_touch', rows, now=now, interval=60)
    assert one == evaluate('third_touch', rows+[Candle(rows[-1].t+60, 100, 140, 50, 60)], now=now, interval=60)
    future = evaluate('third_touch', rows+[Candle(rows[-1].t+120, 100, 140, 50, 60)], now=now, interval=60)
    assert not future['accepted']


def test_line_and_identity_are_stable_when_old_unrelated_candles_roll_out():
    rows = rows_for_third(diagonal=True)
    one = decision(rows); two = decision(rows[3:])
    assert one['accepted'] and two['accepted']
    assert one['setup_identity'] == two['setup_identity']
    for key in ('reference_at', 'reference_price', 'slope_per_second', 'defined_at'):
        assert one['line'][key] == pytest.approx(two['line'][key])


def test_normalized_geometry_and_missing_volume():
    rows = rows_for_third(diagonal=True)
    one = decision(rows)
    scaled = [Candle(c.t, c.o*10, c.h*10, c.l*10, c.c*10) for c in rows]
    two = decision(scaled)
    assert one['accepted'] and two['accepted']
    assert one['level_kind'] == two['level_kind']
    assert decision([replace(c, v=1000) for c in rows]) == one
    assert two['anchor_atr'] == pytest.approx(one['anchor_atr']*10)


@pytest.mark.parametrize('bad', [{}, {'reference_at': True}, {'side': 'support', 'reference_at': 1, 'reference_price': 100, 'slope_per_second': float('nan'), 'margin': 1}])
def test_invalid_line_is_not_repaired(bad):
    with pytest.raises(ValueError):
        invalidation_limit(bad, BASE)


def test_invalidation_is_projected_without_fitting_new_prices():
    result = decision(rows_for_third(diagonal=True))
    line = result['invalidation_line']; time = result['setup_candle']
    assert invalidation_limit(line, time+60)-invalidation_limit(line, time) == pytest.approx(line['slope_per_second']*60)
    assert snapshot()['operational_enabled'] is False


def test_default_config_and_operational_registry_do_not_enable_third_touch():
    from app.config import Config
    from app.strategies import STRATEGIES
    assert 'third_touch' in KEYS and 'third_touch' not in STRATEGIES
    assert not Config().experimental_strategies and not Config().selection_observation


def alternative(rows, interval):
    timeframe = 'M1' if interval == 60 else 'M5'
    choices = alternatives(rows, [], minimum=99, strict=True,
                           context=('deriv', 'EUR/USD', timeframe, 60), frozen=[])
    assert {c['selector'] for c in choices if c['selector'].startswith('price_action:')} == {'price_action:'+k for k in KEYS}
    return next(c for c in choices if c['selector'] == 'price_action:third_touch')


@pytest.mark.parametrize('interval', [60, 300])
@pytest.mark.parametrize('duration', [60, 120, 180, 300])
def test_exact_next_open_is_preserved_with_separate_duration_and_late_block(interval, duration):
    rows = rows_for_third(interval); choice = alternative(rows, interval)
    assert choice['candidate'] and choice['candidate']['score'] == 80
    target = rows[-1].t+interval; now = target+2
    observer = SelectionObserver(':memory:')
    settings = {'provider':'deriv', 'assets':['EUR/USD'], 'timeframe':'M1' if interval==60 else 'M5',
                'duration_seconds':duration, 'payout_pct':None}
    session = observer.session(config_snapshot(Config(), settings), now)
    record = observer.capture_selection(choice, session=session, asset='EUR/USD', source_key='deriv',
        source_identity='synthetic-only', rows=rows, interval=interval, timeframe=settings['timeframe'],
        duration=duration, now=now, observed_at=now, min_lead=60)
    assert record['entry_at']==target and record['expiry_at']==target+duration
    assert record['state']=='cancelled' and record['result']=='CANCELADO'
    assert record['entry_timing']['latency_seconds']==2
    assert record['entry_timing']['lead_seconds']==-2 and not record['entry_timing']['actionable']
    assert record['rules_version']=='third-touch-1.0.0'
    assert not record['sent_to_telegram'] and not record['broker_confirmed']
    assert record['decision']['line']==choice['candidate']['line']
    assert all(c['t']+interval<=now for c in record['evidence']['candles'])
    metrics = summarize([record])
    assert metrics['cancelled']==1 and metrics['evaluated']==0 and metrics['wins']==metrics['losses']==0
    assert grouped([record])[0]['rules_version']=='third-touch-1.0.0'
    observer.db.close()


def test_cancelled_next_open_survives_restart_dedup_and_never_uses_settlement_quotes(tmp_path):
    rows = rows_for_third(); target=rows[-1].t+60; now=target+2; path=tmp_path/'selection.db'
    observer=SelectionObserver(path)
    session=observer.session(config_snapshot(Config(), {'provider':'deriv','assets':['EUR/USD'],
        'timeframe':'M1','duration_seconds':60,'payout_pct':None}), now)
    kwargs=dict(session=session, asset='EUR/USD', source_key='deriv', source_identity='synthetic-only',
                rows=rows, interval=60, timeframe='M1', duration=60, now=now, observed_at=now, min_lead=60)
    record=observer.capture_selection(alternative(rows,60), **kwargs)
    origin=observer.db.execute('SELECT payload FROM prediction_origins').fetchone()[0]
    observer.db.close();observer=SelectionObserver(path)
    assert observer.session(session['configuration'],now+1000)==session
    assert observer.capture_selection(alternative(rows[3:],60), **{**kwargs,'rows':rows[3:]}) is None
    class NoQueries:
        name='synthetic-only'
        async def candles(self,*args):raise AssertionError('Cancelled setup must not fetch prices')
        async def settlement_candles(self,*args):raise AssertionError('Cancelled setup must not settle')
    asyncio.run(observer.step({'deriv':NoQueries()}, lambda:target+10000))
    assert observer.rows()[0]==record
    assert observer.db.execute('SELECT payload FROM prediction_origins').fetchone()[0]==origin
    with pytest.raises(ValueError):observer.patch(record, decision={'rewritten':True})
    observer.db.close()


def test_optional_legacy_collector_also_keeps_exact_open_and_cancels_late():
    rows=rows_for_third();result=decision(rows);now=rows[-1].t+62
    store=ResearchStore(':memory:')
    record=store.capture(result,asset='EUR/USD',source_key='deriv',source_identity='synthetic-only',
        rows=rows,interval=60,duration=300,now=now,observed_at=now)
    assert record['entry_at']==rows[-1].t+60 and record['expiry_at']==rows[-1].t+360
    assert record['state']=='cancelled' and record['entry_timing']['latency_seconds']==2
    assert record['research_configuration']['rule_snapshot']==snapshot()
    store.db.close()


def test_experimental_groups_are_searchable_by_displayed_setup_without_operational_leak(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_system import Feed, TG
    rows=rows_for_third();now=rows[-1].t+62;feed=Feed();telegram=TG()
    with TestClient(create_app(Config(database=str(tmp_path/'db'),selection_observation=True),feed,telegram)) as client:
        service=client.app.state.service;service.clock=lambda:now;observer=service.observer
        session=observer.session(config_snapshot(service.config, {'provider':'deriv','assets':['EUR/USD'],
            'timeframe':'M1','duration_seconds':60,'payout_pct':None}),now)
        observer.capture_selection(alternative(rows,60),session=session,asset='EUR/USD',source_key='deriv',
            source_identity='synthetic-only',rows=rows,interval=60,timeframe='M1',duration=60,now=now,observed_at=now,min_lead=60)
        response=client.get('/api/validation/groups',params={'scope':'experimental','session_id':session['id'],'q':'Terceiro toque'})
        assert response.status_code==200
        group=response.json()['items'][0]
        assert response.json()['total']==1 and group['strategy']=='third_touch'
        assert group['cancelled']==1 and group['wins']==group['losses']==0
        assert client.get('/api/validation/groups',params={'strategy':'third_touch'}).json()['total']==0
        assert not service.store.signals() and not service.scan['active']
        assert telegram.calls==feed.calls==0


@pytest.mark.parametrize('interval',[60,300])
def test_replay_does_not_silently_shift_next_open_or_count_a_past_price_as_win(interval):
    from scripts.validate_experiments import replay
    bars=rows_for_third(interval);target=bars[-1].t+interval
    bars += [Candle(target+i*interval,100.8,101.3,100.45,100.8) for i in range(12)]
    minutes=[]
    for bar in bars:
        minutes.append(bar)
        for offset in range(60,interval,60):
            minutes.append(Candle(bar.t+offset,bar.c,bar.c+.01,bar.c-.01,bar.c))
    report,trades,diagnostics=replay({'TEST':minutes}, key='third_touch',bounds=(target,target+600),interval=interval,duration=60)
    blocked=[d for d in diagnostics if d.get('entry_timing')]
    assert blocked and report['blocks']['insufficient_lead']>=1
    assert blocked[0]['entry_at']==target and blocked[0]['entry_timing']['latency_seconds']==2
    assert blocked[0]['result']=='CANCELADO' and blocked[0]['accepted']
    assert trades==[] and report['evaluated']==report['wins']==report['losses']==0

