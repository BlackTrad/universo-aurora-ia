"""Book-rule, real-price provenance and pipeline tests; no financial backtest."""
import asyncio
from dataclasses import replace
import math
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.deriv import DerivProvider
from app.public_crypto import BinanceProvider,CoinbaseProvider
from app.live_price import PricePoint,validate_point
from app.manual_book import (CATALOG,VERSION,LIVE_KEYS,evaluate,prepare_live,evaluate_live,
                             room_at,snapshot,shape_contexts)
from app.market import Candle,CandleBatch,DataError
from app.main import create_app
from app.service import Service
from app.store import Store
from tests.book_fixtures import scenario
from tests.manual_fixtures import put,bar,fixture as legacy
from tests.test_system import TG


def observed(s):
    start=s['rows'][-1].t+300
    return [PricePoint(p,start+5+i*3,start+5+i*3,'EUR/USD','test:book',str(i))
            for i,p in enumerate(s['prices'])]


@pytest.mark.parametrize('key',tuple(CATALOG))
@pytest.mark.parametrize('direction',('CALL','PUT'))
def test_every_book_rule_has_a_causal_closed_or_observed_example(key,direction):
    s=scenario(key,direction);rows=s['rows']
    if key in LIVE_KEYS:
        points=observed(s)
        candidates,rejected=evaluate_live(prepare_live(rows),points,now=points[-1].at,
                                          forming_open=s['forming_open'])
        matches=[c for c in candidates if c['strategy_resolved']==key and c['direction']==direction]
        assert matches, (key,rejected)
        c=matches[0]
        assert c['live_observation']==points[-1].json()
        assert c['live_trigger_observations']==[p.json() for p in points]
        for name in ('touch_observation','cross_before_observation'):
            if name in c['manual_geometry']:
                assert c['manual_geometry'][name] in [p.json() for p in points]
    else:
        c,reason=evaluate(key,rows)
        assert c is not None,reason
    assert c['direction']==direction
    assert c['manual_rules_version']==VERSION and c['setup_contract']['version']==VERSION
    assert c['manual_source_pages']==CATALOG[key][1]
    assert all(p['passed'] is True for p in c['mandatory_criteria'])
    assert c['setup_contract']['confirmed_at']==rows[-1].t+300
    assert c['setup_contract']['entry_window_end']==rows[-1].t+600
    assert all(c['manual_geometry'][k]>0 for k in ('level','invalidation'))


@pytest.mark.parametrize('key',tuple(CATALOG))
@pytest.mark.parametrize('bad',('short','gap','malformed','unknown_interval'))
def test_invalid_context_never_arms_or_emits_any_book_rule(key,bad):
    rows=scenario(key)['rows']
    if bad=='short':rows=rows[-39:]
    elif bad=='gap':rows=rows[:-3]+rows[-2:]
    elif bad=='malformed':rows=[*rows[:-1],replace(rows[-1],h=rows[-1].l-1)]
    else:rows=[replace(c,t=rows[0].t+i*240) for i,c in enumerate(rows)]
    assert evaluate(key,rows)[0] is None
    assert prepare_live(rows)==[]


def test_mini_pullback_requires_the_micro_confluence_not_only_harami():
    rows=scenario('pa_pullback_harami')['rows']
    assert evaluate('pa_pullback_harami',rows)[0]
    put(rows,70,(102.9,103.25,102.9,102.98));put(rows,75,(102.9,103.25,102.9,102.98))
    c,reason=evaluate('pa_pullback_harami',rows)
    assert c is None and 'microtendência' in reason


def test_near_miss_breakout_requires_a_real_prior_retreat():
    rows=scenario('pa_breakout_near_miss')['rows'];assert evaluate('pa_breakout_near_miss',rows)[0]
    for i in range(74,79):put(rows,i,(102.98,103.1,102.9,103.0))
    assert evaluate('pa_breakout_near_miss',rows)[0] is None


def test_false_breakout_needs_the_figure_relief_and_first_return():
    rows=scenario('pa_false_breakout')['rows'];assert evaluate('pa_false_breakout',rows)[0]
    missing=list(rows);put(missing,75,(99.45,99.46,99.35,99.4))
    assert evaluate('pa_false_breakout',missing)[0] is None
    already=list(rows);put(already,77,(99.45,100.2,99.35,100.0))
    assert evaluate('pa_false_breakout',already)[0] is None


def test_correction_leg_blocks_reversal_before_its_micro_target():
    rows=scenario('pa_correction_flow','PUT')['rows']
    rows.append(bar(78,100.95,101.25,100.9,101.20))
    c,reason=evaluate('pa_battle_body',rows)
    assert c is None and 'correção' in reason


def test_micro_flow_requires_an_earlier_example_on_the_same_line():
    rows=scenario('pa_flow_micro')['rows'];assert any(p['key']=='pa_flow_micro' for p in prepare_live(rows))
    put(rows,78,(100.2,100.3,99.94,100.2))
    assert not any(p['key']=='pa_flow_micro' for p in prepare_live(rows))


def test_fourth_touch_needs_additional_region_and_pattern_confluences():
    rows=scenario('pa_micro_fourth')['rows'];assert evaluate('pa_micro_fourth',rows)[0]
    put(rows,90,(100.5,100.7,100.3,100.55))
    assert evaluate('pa_micro_fourth',rows)[0] is None


def test_body_and_wick_of_one_region_do_not_hide_a_distinct_obstacle():
    geometry={'zone_id':'body:low:1000'}
    assert room_at([{'zone_id':'wick:low:1000','level':100.1}],geometry,1,100)[0]
    assert not room_at([{'zone_id':'body:low:2000','level':100.1}],geometry,1,100)[0]


def test_live_touch_requires_an_actual_sample_not_interpolation_or_future_low():
    s=scenario('pa_wick_retracement');plans=[p for p in prepare_live(s['rows']) if p['key']=='pa_wick_retracement']
    t=s['rows'][-1].t+305;level=plans[0]['geometry']['level'];a=plans[0]['average']
    points=[PricePoint(level+.3*a,t,t,'EUR/USD','test:book'),
            PricePoint(level+.2*a,t+3,t+3,'EUR/USD','test:book')]
    assert evaluate_live(plans,points,now=t+3)[0]==[]
    assert evaluate_live(plans,[replace(points[-1],price=level)],now=t+3)[0]==[]


def test_live_invalidation_is_not_erased_when_the_price_recovers():
    s=scenario('pa_wick_retracement');plans=[p for p in prepare_live(s['rows']) if p['key']=='pa_wick_retracement'];dead=set()
    point=observed(s)[0];invalid=replace(point,price=plans[0]['geometry']['invalidation']-.01)
    assert not evaluate_live(plans,[invalid],now=invalid.at,dead=dead)[0]
    assert dead
    good=observed(s)
    assert not evaluate_live(plans,good,now=good[-1].at,dead=dead)[0]


@pytest.mark.parametrize('kind',('missing','unknown','future','stale','wrong_source','wrong_asset','out_of_order'))
def test_live_trigger_rejects_unproven_or_wrong_price(kind):
    s=scenario('pa_flow_hammer');plans=prepare_live(s['rows']);points=observed(s);now=points[-1].at
    if kind=='missing':points=[]
    elif kind=='unknown':points=[{'price':100,'at':now}]
    elif kind=='future':points[-1]=replace(points[-1],at=now+1)
    elif kind=='stale':now+=11
    elif kind=='wrong_source':points[-1]=replace(points[-1],source_identity='another:source')
    elif kind=='wrong_asset':points[-1]=replace(points[-1],asset='GBP/USD')
    else:points.reverse()
    assert evaluate_live(plans,points,now=now,asset='EUR/USD',identity='test:book')[0]==[]


def test_live_context_expires_in_its_original_candle():
    s=scenario('pa_flow_hammer');plans=prepare_live(s['rows']);points=observed(s)
    now=s['rows'][-1].t+600
    points=[replace(p,at=now,received_at=now) for p in points[-1:]]
    assert not evaluate_live(plans,points,now=now)[0]


def test_deep_live_retest_needs_four_closed_relief_bars_in_every_variant():
    rows=legacy('pa_breakout_90')[:-1]
    plans=prepare_live(rows);assert any(p['key']=='pa_breakout_90' for p in plans)
    assert not any(p['geometry'].get('breakout_fraction',0)>=.85 for p in prepare_live(rows[:-1]))


@pytest.mark.parametrize('key',('pa_head_shoulders','pa_double_extreme'))
def test_large_shape_can_arm_first_live_retest_without_faking_a_return_candle(key):
    rows=legacy(key)[:-1]
    geometries=shape_contexts(rows, __import__('app.indicators',fromlist=['atr']).atr(rows[:-1]),key)
    assert geometries
    assert any(p['key']==key for p in prepare_live(rows))
    assert all(g['breakout_at']==rows[-1].t for g in geometries)


@pytest.mark.parametrize('field,value',[
    ('price',0),('price',True),('price',math.nan),('at',math.inf),('at',True),
    ('at',1001),('at',980),('received_at',1001),('source_identity','wrong'),('asset','BTC/USD')])
def test_price_point_validation(field,value):
    p=replace(PricePoint(100,999,1000,'EUR/USD','original'),**{field:value})
    with pytest.raises(DataError):validate_point(p,asset='EUR/USD',identity='original',now=1000)


@pytest.mark.parametrize('provider_cls',('deriv','binance','coinbase'))
def test_live_provider_reads_original_timestamped_public_trade_only(provider_cls):
    async def run():
        if provider_cls=='deriv':
            provider=DerivProvider(Config());requests=[]
            async def request(payload,expected):
                requests.append((payload,expected));return {'tick':{'symbol':'frxEURUSD','quote':100.2,'epoch':999}}
            provider.request=request
            with patch('app.deriv.time.time',return_value=1000):point=await provider.live_price('EUR/USD')
            assert requests==[({'ticks':'frxEURUSD','subscribe':1},'tick')]
            asset='EUR/USD'
        else:
            requests=[]
            def handler(request):
                requests.append(request);assert request.method=='GET' and 'authorization' not in request.headers
                data=([{'id':1,'price':'100.2','time':999000}] if provider_cls=='binance' else
                      {'trade_id':1,'price':'100.2','time':'1970-01-01T00:16:39Z'})
                return httpx.Response(200,json=data)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider=(BinanceProvider if provider_cls=='binance' else CoinbaseProvider)(client)
                asset='BTC/USDT' if provider_cls=='binance' else 'BTC/USD'
                with patch('app.public_crypto.time.time',return_value=1000):point=await provider.live_price(asset)
            assert requests[0].url.path==('/api/v3/trades' if provider_cls=='binance' else '/products/BTC-USD/ticker')
        validate_point(point,asset=asset,identity=provider.identity,now=1000)
        assert point.price==100.2 and point.at==999
    asyncio.run(run())


class LiveFeed:
    name='TESTE SINTÉTICO';identity='test:book';assets=['EUR/USD'];timeframes=['M1','M5'];ready=True
    def __init__(self,clock):
        self.clock=clock;self.rows=scenario('pa_flow_hammer')['rows'];self.price_calls=0;self.mode='good'
    async def check_asset(self,asset):pass
    async def candles(self,asset,interval=300,count=100):
        if interval==300:return CandleBatch(self.rows,self.identity)
        last=int(self.clock[0]//60)*60-60
        return CandleBatch([Candle(last-(3-i)*60,100.2,100.3,100.05,100.2) for i in range(4)],self.identity)
    async def live_price(self,asset):
        self.price_calls+=1;t=self.clock[0]
        return PricePoint(100.25,t-11 if self.mode=='stale' else t,t,asset,
                          'changed' if self.mode=='wrong' else self.identity,str(self.price_calls))


def test_native_scanner_waits_for_real_live_price_commits_once_and_stops():
    async def run():
        rows=scenario('pa_flow_hammer')['rows'];clock=[rows[-1].t+305]
        store=Store(':memory:');feed=LiveFeed(clock)
        service=Service(Config(assets=['EUR/USD'],database=':memory:'),store,feed,TG(),clock=lambda:clock[0],automatic_only=True)
        # Control the real scanner's initial analysis and the observation pass
        # without a wall-clock sleep or any external provider.
        service.scan={'active':True,'id':'controlled','assets':['EUR/USD']}
        a=service.start(['EUR/USD'],300,'M5',scan_id='controlled');await service.current_analysis_task
        assert a['signals_emitted']==0 and not store.active()
        assert service.intrabar_contexts
        feed.mode='stale';assert await service.poll_intrabar()==[];assert not store.active()
        feed.mode='good';created=await service.poll_intrabar();assert len(created)==1
        s=created[0];assert s['manual_intrabar'] and s['live_observation']['price']==100.25
        assert s['entry_at']<s['setup_contract']['entry_window_end']
        assert s['entry_at']-clock[0]>=72 and s['entry_at']%60==0
        assert s['evidence']['rules_version']==VERSION
        assert not await service.poll_intrabar();assert len(store.signals())==1
        await asyncio.gather(*list(service.tasks))
        clock[0]=s['entry_at']-40
        await service.revalidate_entry(store.signal(s['id']))
        assert store.signal(s['id'])['entry_validation']['status']=='confirmada'
        calls=feed.price_calls;await service.stop_scan();assert await service.poll_intrabar()==[]
        assert feed.price_calls==calls and service.snapshot()['intrabar']['state']=='parado'
        store.close()
    asyncio.run(run())


def test_api_has_entry_catalog_without_soros_or_automatic_start():
    rows=scenario('pa_hammer')['rows'];clock=[rows[-1].t+305];feed=LiveFeed(clock)
    with TestClient(create_app(Config(database=':memory:',assets=['EUR/USD']),feed,TG(),clock=lambda:clock[0])) as client:
        state=client.get('/api/state').json();assert state['scan']['active'] is False and feed.price_calls==0
        data=client.get('/api/manual/strategies').json();assert len(data['strategies'])==39
        assert data['version']==VERSION and data['rules']['forming_candles_used_as_closed'] is False
        assert client.get('/api/manual/management?capital=100&payout_pct=87').status_code==404
        assert not any('soros' in str(item).lower() for item in data['strategies'])
        assert client.get('/api/state').json()['settings']==state['settings']


def test_new_snapshot_declares_live_timing_and_never_promises_immediate_orders():
    value=snapshot()
    assert value['intrabar_immediate_broker_execution'] is False
    assert value['parameters']['live_price_maximum_age_seconds']==10
    assert value['parameters']['live_poll_seconds']==15
    assert value['smc_active'] is False
