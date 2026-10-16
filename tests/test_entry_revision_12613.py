"""Adversarial regressions from the 1.26.12 review. All prices are synthetic."""
import asyncio
from dataclasses import replace
import json
import math
from unittest.mock import patch
import httpx
import pytest
from app.config import Config
from app.deriv import DerivProvider
from app.entry_watch import check_live_entry
from app.live_price import PricePoint, point_after
from app.manual_book import evaluate, evaluate_live, prepare_live, region_checks, VERSION
from app.market import Candle, CandleBatch, DataError
from app.price_stream import PriceStreams
from app.public_crypto import BinanceProvider, CoinbaseProvider
from app.service import Service
from app.store import Store
from app.strategies import collect_candidates, scan_all
from tests.book_fixtures import scenario
from tests.manual_fixtures import bar, put
from tests.test_apex_engines import rows as old_rows
from tests.test_book_12612 import LiveFeed
from tests.test_system import TG


def mirrored(rows):
    m=2*max(c.h for c in rows)
    return [Candle(c.t,m-c.o,m-c.l,m-c.h,m-c.c,c.v) for c in rows]


@pytest.mark.parametrize('direction',('CALL','PUT'))
@pytest.mark.parametrize('case',('single_wick','correction_pressure'))
def test_flow_cannot_bypass_closed_region_or_arrival_veto(direction,case):
    if case=='single_wick':
        rows=[bar(i,101.5,101.9,101.1,101.7) for i in range(80)]
        put(rows,20,(100.4,100.6,99.75,100.45));put(rows,79,(100.05,100.2,99.75,100.15))
    else:
        rows=scenario('pa_correction_flow','PUT')['rows'];rows.append(bar(78,101.05,101.12,100.9,101.1))
    if direction=='PUT':rows=mirrored(rows)
    assert evaluate('pa_hammer',rows)[0] is None
    assert not [p for p in prepare_live(rows) if p['direction']==direction and p['key'] in ('pa_flow_hammer','pa_flow_zone')]


def test_isolated_body_pivot_is_not_a_strong_flow_region():
    rows=[bar(i,100,100.4,99.8,100.2) for i in range(60)]
    zone={'basis':'body','visits':[4],'level':100,'kind':'low'}
    assert not all(c[1] for c in region_checks(rows,zone,1,strong=True))


@pytest.mark.parametrize('direction',('CALL','PUT'))
def test_fibonacci_obeys_the_manual_interference_gate(direction):
    r=old_rows({54:(99.8,100,95,96),62:(102.5,102.8,102.3,102.6),73:(105,106,104.5,105.5),84:(101,103,100.5,102.7)})
    r=[replace(c,t=1800000000+i*300) for i,c in enumerate(r)]
    if direction=='PUT':r=mirrored(r)
    candidates,_,evaluations=collect_candidates(r)
    assert not any(c['strategy_resolved']=='fibonacci' for c in candidates)
    assert 'interferência' in next(e['reason'] for e in evaluations if e['strategy']=='fibonacci')
    assert scan_all(r)[0] is None


def fixture_signal(direction='CALL'):
    s=scenario('pa_wick_retracement',direction);rows=s['rows'];start=rows[-1].t+300
    points=[PricePoint(p,start+5+i*15,start+5+i*15,'EUR/USD','test:book',str(i)) for i,p in enumerate(s['prices'])]
    candidates,_=evaluate_live(prepare_live(rows),points,now=points[-1].at)
    c=next(c for c in candidates if c['strategy_resolved']=='pa_wick_retracement' and c['direction']==direction)
    entry=math.ceil((points[-1].at+72)/60)*60
    return {**c,'id':'entry-'+direction,'asset':'EUR/USD','source':'SYNTHETIC','source_key':'deriv','source_identity':'test:book',
        'strategy':'scan_all','timeframe':'M5','duration_seconds':300,'entry_at':entry,'expiry_at':entry+300,
        'created_at':points[-1].at,'analysis_at':points[-1].at,'state':'aguardando entrada','result':'Resultado indisponível',
        'settlement_pending':True,'telegram':{'status':'não configurado'},'live_monitor_required':True,
        'entry_validation':{'status':'pendente','scheduled_at':entry-45,'atr':c['manual_atr']},
        'evidence':{'candles':[r.json() for r in rows],'rules_version':VERSION}}


class EntryFeed:
    name='SYNTHETIC';identity='test:book';ready=True;assets=['EUR/USD'];timeframes=['M1','M5']
    def __init__(self,signal,clock):self.signal=signal;self.clock=clock;self.value=signal['live_observation']['price']
    async def candles(self,*args):
        end=int(self.clock[0]//60)*60-60;p=self.signal['live_observation']['price']
        return CandleBatch([Candle(end+(i-2)*60,p,p+.1,p-.02,p) for i in range(4)],self.identity)
    async def live_price(self,asset):return PricePoint(self.value,self.clock[0],self.clock[0],asset,self.identity)


async def close_service(service):
    await service.price_streams.close()
    while service.tasks:await asyncio.gather(*list(service.tasks))
    if service.observer:service.observer.close()
    service.store.close()


@pytest.mark.parametrize('direction',('CALL','PUT'))
@pytest.mark.parametrize('stage',('revalidation','after_confirmation','entry_stale','entry_fresh'))
def test_native_entry_remains_invalid_after_boundary_loss_and_needs_fresh_entry_proof(direction,stage):
    async def run():
        s=fixture_signal(direction);clock=[s['entry_at']-40];store=Store(':memory:');store.add_signal(s,'one')
        feed=EntryFeed(s,clock);tg=TG();tg.configured=False
        service=Service(Config(database=':memory:',assets=['EUR/USD']),store,feed,tg,clock=lambda:clock[0],automatic_only=True)
        sign=1 if direction=='CALL' else -1
        invalid=s['manual_geometry']['invalidation']-sign*.01*s['manual_atr']
        if stage=='revalidation':feed.value=invalid
        await service.revalidate_entry(store.signal(s['id']))
        if stage=='revalidation':
            result=store.signal(s['id']);assert result['state']=='cancelado';assert 'invalidação' in result['entry_validation']['reason']
        else:
            assert store.signal(s['id'])['entry_validation']['status']=='confirmada'
            if stage=='after_confirmation':
                clock[0]=s['entry_at']-20
                service.watch_pending_price(('deriv','EUR/USD'),PricePoint(invalid,*([clock[0]]*2),'EUR/USD',feed.identity))
                feed.value=s['live_observation']['price'];clock[0]+=1
                service.watch_pending_price(('deriv','EUR/USD'),await feed.live_price('EUR/USD'))
                assert store.signal(s['id'])['state']=='cancelado'
            else:
                if stage=='entry_fresh':
                    clock[0]=s['entry_at']-1;service.watch_pending_price(('deriv','EUR/USD'),await feed.live_price('EUR/USD'))
                clock[0]=s['entry_at'];await service.tick()
                assert store.signal(s['id'])['state']==('em andamento' if stage=='entry_fresh' else 'cancelado')
        await close_service(service)
    asyncio.run(run())


@pytest.mark.parametrize('field,value',(('asset','GBP/USD'),('source_identity','wrong'),('at',1)))
def test_entry_watch_rejects_wrong_or_stale_proof(field,value):
    s=fixture_signal();t=s['entry_at']-40
    point=replace(PricePoint(s['live_observation']['price'],t,t,'EUR/USD','test:book'),**{field:value})
    with pytest.raises(DataError):check_live_entry(s,point,t)


def test_live_observations_have_the_original_quote_and_operational_entry_and_can_be_cancelled():
    async def run():
        rows=scenario('pa_flow_hammer')['rows'];clock=[rows[-1].t+305];store=Store(':memory:');feed=LiveFeed(clock)
        tg=TG();tg.configured=False
        service=Service(Config(database=':memory:',assets=['EUR/USD'],selection_observation=True),store,feed,tg,clock=lambda:clock[0],automatic_only=True)
        service.scan={'active':True,'id':'controlled','assets':['EUR/USD']}
        service.start(['EUR/USD'],300,'M5',scan_id='controlled');await service.current_analysis_task
        assert not [r for r in service.observer.rows() if r.get('manual_intrabar')]
        created=await service.poll_intrabar();assert len(created)==1
        observations=[r for r in service.observer.rows() if r.get('manual_intrabar')]
        candidate=next(r for r in observations if r['selector']=='candidate:pa_flow_hammer')
        assert candidate['entry_at']==created[0]['entry_at']
        assert candidate['live_observation']==created[0]['live_observation']
        assert candidate['evidence']['live_trigger_observations']
        assert candidate['duration_basis']=='intrabar_planned'
        assert not candidate['sent_to_telegram']
        assert len(store.signals())==1
        count=len(service.observer.rows());clock[0]+=1;await service.poll_intrabar()
        assert len(service.observer.rows())==count
        clock[0]=candidate['entry_at']-40;await service.observer.step({'deriv':feed},lambda:clock[0])
        assert next(r for r in service.observer.rows() if r['id']==candidate['id'])['state']=='pending_result'
        boundary=candidate['setup_contract']['geometry']['invalidation'];clock[0]+=1
        service.observer.watch_price(('deriv','EUR/USD'),PricePoint(boundary-.01,clock[0],clock[0],'EUR/USD',feed.identity),clock[0])
        assert next(r for r in service.observer.rows() if r['id']==candidate['id'])['state']=='cancelled'
        assert len(store.signals())==1
        await service.stop_scan();await close_service(service)
    asyncio.run(run())


class Socket:
    def __init__(self,messages):self.messages=iter(messages);self.sent=[];self.closed=False
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True
    async def send(self,body):self.sent.append(json.loads(body))
    def __aiter__(self):return self
    async def __anext__(self):
        try:return json.dumps(next(self.messages))
        except StopIteration:raise StopAsyncIteration


@pytest.mark.parametrize('source',('deriv','binance','coinbase'))
@pytest.mark.parametrize('bad',(None,'symbol','time','price','missing'))
def test_stream_adapters_parse_only_the_requested_original_public_trade(source,bad):
    async def run():
        if source=='deriv':
            asset='EUR/USD';row={'msg_type':'tick','tick':{'symbol':'frxEURUSD','epoch':999,'quote':'100.2'}}
            target=row['tick'];symbol_field='symbol';time_field='epoch';price_field='quote';module='app.deriv'
            provider=DerivProvider(Config())
        elif source=='binance':
            asset='BTC/USDT';row={'stream':'btcusdt@trade','data':{'e':'trade','s':'BTCUSDT','T':999000,'t':2,'p':'100.2'}}
            target=row['data'];symbol_field='s';time_field='T';price_field='p';module='app.public_crypto'
            provider=BinanceProvider(httpx.AsyncClient())
        else:
            asset='BTC/USD';row={'type':'ticker','product_id':'BTC-USD','time':'1970-01-01T00:16:39Z','trade_id':2,'price':'100.2'}
            target=row;symbol_field='product_id';time_field='time';price_field='price';module='app.public_crypto'
            provider=CoinbaseProvider(httpx.AsyncClient())
        if bad=='symbol':target[symbol_field]='WRONG'
        if bad=='time':target[time_field]=True
        if bad=='price':target[price_field]='NaN'
        if bad=='missing':target.pop(time_field)
        socket=Socket([row]);iterator=provider.stream_prices((asset,))
        try:
            with patch(module+'.connect',return_value=socket),patch(module+'.time.time',return_value=1000):
                if bad:
                    with pytest.raises(DataError):await anext(iterator)
                else:
                    point=await anext(iterator)
                    assert point.price==100.2 and point.at==999 and point.received_at==1000
                    assert point.source_identity==provider.identity and point.asset==asset
                    if source=='deriv':assert socket.sent==[{'ticks':'frxEURUSD','subscribe':1,'req_id':1}]
                    if source=='coinbase':assert socket.sent[0]['channels']==['ticker','heartbeat']
        finally:
            await iterator.aclose()
            if source!='deriv':await provider.client.aclose()
        assert socket.closed
    asyncio.run(run())


def test_equal_source_seconds_keep_real_receipt_order_without_fabricated_times():
    a=PricePoint(100,999,999.1,'EUR/USD','test:book')
    b=replace(a,price=100.1,received_at=999.2)
    assert point_after(b,a) and not point_after(a,b)
    assert not point_after(replace(b,trade_id='2'),replace(a,trade_id='3'))


def test_streams_are_lazy_ordered_demand_driven_and_cancel_their_socket():
    async def run():
        clock=[1000];queue=asyncio.Queue();events=[];gaps=[]
        class Feed:
            identity='test:book';opened=0;closed=0
            async def stream_prices(self,assets):
                self.opened+=1
                try:
                    while True:yield await queue.get()
                finally:self.closed+=1
        feed=Feed()
        async def on_point(key,point):events.append(point)
        async def on_gap(key,code):gaps.append(code)
        hub=PriceStreams({'test':feed},lambda:clock[0],on_point,on_gap)
        assert not hub.tasks and feed.opened==0
        await hub.sync({('test','EUR/USD')})
        await queue.put(PricePoint(100,999,1000,'EUR/USD','test:book','2'))
        for _ in range(30):
            if events:break
            await asyncio.sleep(0)
        assert len(events)==1
        await queue.put(PricePoint(100,998,1000,'EUR/USD','test:book','1'))
        await queue.put(PricePoint(100,999,1000,'EUR/USD','test:book','2'))
        for _ in range(10):await asyncio.sleep(0)
        assert len(events)==1
        await hub.sync(set());assert feed.closed==1 and not hub.tasks and not hub.latest
        assert not gaps
        await hub.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind',('drop','invalid_price'))
def test_native_stream_cancels_confirmed_signal_and_stop_releases_connection(kind):
    async def run():
        s=fixture_signal();clock=[s['entry_at']-40];store=Store(':memory:')
        s['entry_validation']['status']='confirmada';store.add_signal(s,'one')
        queue=asyncio.Queue()
        class Feed(EntryFeed):
            opened=0;closed=0
            async def stream_prices(self,assets):
                self.opened+=1
                try:
                    while True:
                        value=await queue.get()
                        if isinstance(value,Exception):raise value
                        yield value
                finally:self.closed+=1
        feed=Feed(s,clock);tg=TG();tg.configured=False
        service=Service(Config(database=':memory:',assets=['EUR/USD']),store,feed,tg,clock=lambda:clock[0],automatic_only=True)
        await service.sync_price_streams()
        if kind=='drop':await queue.put(DataError('fixture disconnect','fixture_disconnect'))
        else:await queue.put(PricePoint(s['manual_geometry']['invalidation']-.01,clock[0],clock[0],'EUR/USD',feed.identity))
        for _ in range(60):
            if store.signal(s['id'])['state']=='cancelado':break
            await asyncio.sleep(0)
        assert store.signal(s['id'])['state']=='cancelado'
        await service.stop_scan();assert not service.price_streams.tasks and feed.closed==feed.opened==1
        await close_service(service)
    asyncio.run(run())


def test_restart_preserves_legacy_pending_and_running_results_and_cancels_uncovered_new_entry():
    async def run():
        s=fixture_signal();clock=[s['entry_at']-40];store=Store(':memory:');store.add_signal(s,'new')
        legacy={**s,'id':'legacy','live_monitor_required':False,'manual_intrabar':False}
        legacy['setup_contract']={**s['setup_contract'],'version':'1.26.12-book-1'}
        store.add_signal(legacy,'legacy');store.add_signal({**s,'id':'running','state':'em andamento'},'running')
        feed=EntryFeed(s,clock);tg=TG();tg.configured=False
        service=Service(Config(database=':memory:'),store,feed,tg,clock=lambda:clock[0],automatic_only=True)
        service.recover_entry_watches()
        assert store.signal(s['id'])['state']=='cancelado'
        assert store.signal('legacy')['state']=='aguardando entrada'
        assert store.signal('running')['state']=='em andamento'
        from app.setup_contract import check_setup
        assert check_setup(legacy,await feed.candles(),clock[0])[1]==''
        await close_service(service)
    asyncio.run(run())


def test_touch_proof_survives_tick_buffer_rotation_and_same_source_second():
    s=scenario('pa_wick_retracement');plans=[p for p in prepare_live(s['rows']) if p['key']=='pa_wick_retracement' and p['direction']=='CALL']
    plan=plans[0];t=plan['starts_at']+5;touch=PricePoint(plan['geometry']['level'],t,t+.1,'EUR/USD','test:book')
    touches={};dead=set()
    assert not evaluate_live(plans,[touch],now=t+.1,touches=touches,dead=dead)[0]
    reaction=replace(touch,price=touch.price+.06*plan['average'],received_at=t+.2)
    got,_=evaluate_live(plans,[reaction],now=t+.2,touches=touches,dead=dead)
    assert got and got[0]['manual_geometry']['touch_observation']==touch.json()
    assert got[0]['live_observation']['at']==touch.at
    assert touch.json() in got[0]['live_trigger_observations']


def test_reconnect_notifies_gap_and_accepts_only_a_fresh_new_stream():
    async def run():
        events=[];gaps=[];delays=[];gate=asyncio.Event();clock=[1000]
        class Feed:
            identity='test:book';connections=0
            async def stream_prices(self,assets):
                self.connections+=1
                if self.connections==1:raise DataError('fixture disconnected','disconnected')
                yield PricePoint(100,1000,1000,'EUR/USD',self.identity,'1')
                await gate.wait()
        async def point(key,p):events.append(p)
        async def gap(key,code):gaps.append(code)
        async def sleep(delay):delays.append(delay);await asyncio.sleep(0)
        feed=Feed();hub=PriceStreams({'test':feed},lambda:clock[0],point,gap,sleep=sleep)
        await hub.sync({('test','EUR/USD')})
        for _ in range(60):
            if events:break
            await asyncio.sleep(0)
        assert gaps==['disconnected'] and delays==[2] and feed.connections==2
        assert len(events)==1 and hub.latest[('test','EUR/USD')]==events[0]
        await hub.close();assert not hub.tasks
    asyncio.run(run())


def test_closed_and_intrabar_results_do_not_share_comparison_or_training_context():
    from app.learning import profile_key,selection_profile_key
    from app.validation_metrics import compare_selectors
    common={'session_id':'s','source_key':'deriv','source_identity':'test:book','asset':'EUR/USD',
            'timeframe':'M5','duration_seconds':300,'entry_at':1800000000,'expiry_at':1800000300,
            'accepted':True,'direction':'CALL','state':'pending_result','strategy':'pa_breakout_50'}
    closed={**common,'id':'closed','selector':'reference_fixed'}
    live={**common,'id':'live','selector':'regime_router','duration_basis':'intrabar_planned'}
    report=compare_selectors([closed,live])
    assert report['common_signals']==0 and report['both_selected_decisions']==0
    assert profile_key(closed)!=profile_key(live)
    scope={'source_key':'deriv','regime':'lateral','configuration_id':'one'}
    context=('test:book','EUR/USD','M5',300)
    assert selection_profile_key(context,{'direction':'CALL','strategy_resolved':'pa_breakout_50'},scope)!=selection_profile_key(
        context,{'direction':'CALL','strategy_resolved':'pa_breakout_50','manual_intrabar':True},scope)
