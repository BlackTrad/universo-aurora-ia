"""Synthetic regression tests only: no assertion of market profitability."""
import asyncio
import json
import sqlite3
from copy import deepcopy
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.market import Candle, CandleBatch
from app.store import Store
from app.strategies import STRATEGIES, scan_all, select_reference
from app.learning import PIPELINE_VERSION
from app.experiments import KEYS as PRICE_ACTION_KEYS
from app.selection_observer import SelectionObserver, alternatives, config_snapshot, freeze_profiles
from app.validation_metrics import summarize, grouped, compare_selectors
from tests.test_system import fixture, setup, drain, Feed, TG
from tests.test_learning import records, CONTEXT, SCOPE


def candidate(strategy='cci',score=90,direction='CALL'):
    return {'strategy_resolved':strategy,'score':score,'direction':direction,'setup_candle':fixture()[-1].t,'reasons':['Mandatory (90 pts)']}










def test_observer_history_profiles_use_only_known_results_and_regime():
    rows=[{**r,'strategy':'pa_hammer','selector':'candidate:pa_hammer','state':'settled','regime':'lateral','pipeline_version':PIPELINE_VERSION} for r in records()]
    assert freeze_profiles(rows,rows[-1]['settled_at']-1)[0]['sample']==89
    assert not freeze_profiles(rows,rows[-1]['settled_at']-1)[0]['eligible']
    frozen=freeze_profiles(rows,100000)
    lateral={'status':'available','regime':'lateral','direction':None}
    with patch('app.selection_context.structure_context',return_value=lateral):
        choices=alternatives(fixture(),[candidate('pa_hammer')],minimum=70,strict=False,context=CONTEXT,scope=SCOPE,frozen=frozen)
    assert choices[2]['candidate']['strategy_resolved']=='pa_hammer'
    trend={**lateral,'regime':'tendencial','direction':'CALL'}
    with patch('app.selection_context.structure_context',return_value=trend):
        assert alternatives(fixture(),[candidate('pa_hammer')],minimum=70,strict=False,context=CONTEXT,scope=SCOPE,frozen=frozen)[2]['candidate'] is None


def context(observer,now):
    cfg=Config(selection_observation=True)
    configuration=config_snapshot(cfg,{'provider':'deriv','assets':['EUR/USD'],'timeframe':'M5','duration_seconds':300,'payout_pct':None})
    return observer.session(configuration,now)


def capture(observer,session,now,decision=None):
    return observer.capture_selection(decision or {'selector':'reference_fixed','candidate':candidate(),'pool':[candidate()],'reason':''},
        session=session,asset='EUR/USD',source_key='deriv',source_identity='synthetic',rows=fixture(),interval=300,
        timeframe='M5',duration=300,now=now,observed_at=now,min_lead=60)


def test_observer_dedup_immutable_decision_and_restart(tmp_path):
    path=tmp_path/'shadow.db';now=fixture()[-1].t+305
    observer=SelectionObserver(path);session=context(observer,now);r=capture(observer,session,now)
    assert r and capture(observer,session,now+1) is None
    assert r['sent_to_telegram'] is False
    original=observer.db.execute('SELECT payload FROM prediction_origins').fetchone()[0]
    observer.patch(r,state='unavailable',result='INDISPONIVEL',settled_at=now+1000)
    assert observer.db.execute('SELECT payload FROM prediction_origins').fetchone()[0]==original
    with pytest.raises(ValueError):observer.patch(r,decision={'new':'explanation after outcome'})
    observer.db.close();observer=SelectionObserver(path)
    assert observer.session(session['configuration'],now+1000)==session
    assert capture(observer,session,now+1) is None
    observer.db.close()


def test_late_shadow_does_not_roll_entry_forward():
    observer=SelectionObserver(':memory:');now=fixture()[-1].t+305;session=context(observer,now)
    c=observer.capture_selection({'selector':'reference_fixed','candidate':candidate(),'pool':[],'reason':''},
        session=session,asset='EUR/USD',source_key='deriv',source_identity='synthetic',rows=fixture(),interval=300,
        timeframe='M5',duration=300,now=now,observed_at=now+100,min_lead=60)
    assert c['state']=='cancelled' and c['entry_at']<now+160


def test_observer_same_candle_pool_no_extra_analysis_query_and_no_telegram(tmp_path):
    async def run(enabled):
        s,clock=setup(tmp_path/str(enabled));s.config.selection_observation=enabled
        if enabled:s.observer=SelectionObserver(':memory:')
        s.select_strategy('scan_all');s.start(['EUR/USD'],300);await drain(s)
        signals=s.store.signals();assert s.provider.calls==1 and s.telegram.calls==1
        if enabled:
            selectors={r['selector'] for r in s.observer.rows()}
            assert {'reference_fixed','regime_router','history_frozen','reference_adaptive','price_action_router'} <= selectors
            assert not any('baseline_1249'==key for key in selectors)
            assert all(not r['sent_to_telegram'] for r in s.observer.rows())
            assert all(c['manual'] for c in signals[0]['evidence']['selection']['candidates'])
        assert s.store.decisions()['items'][0]['events'][0]['event']=='operational_commit'
        return [{k:v for k,v in x.items() if k in ('asset','entry_at','expiry_at','score','direction','strategy_resolved')} for x in signals]
    assert asyncio.run(run(False))==asyncio.run(run(True))


def test_stop_during_fetch_creates_no_predictions_or_signals(tmp_path):
    async def run():
        s,_=setup(tmp_path);s.observer=SelectionObserver(':memory:');s.select_strategy('scan_all');waiting=asyncio.Event()
        async def blocked(*args):waiting.set();await asyncio.Event().wait()
        s.provider.candles=blocked;s.start(['EUR/USD'],300)
        await waiting.wait();await s.stop_analysis()
        assert not s.store.signals() and not s.observer.rows()
    asyncio.run(run())


def test_preview_is_audited_but_never_an_observed_trade(tmp_path):
    async def run():
        s,_=setup(tmp_path);s.observer=SelectionObserver(':memory:');s.select_strategy('scan_all')
        with patch.dict(STRATEGIES,{'cci':lambda r:(candidate(),''),'scan_all':scan_all},clear=True):
            s.start(['EUR/USD'],300,preview=True);await drain(s)
        assert not s.observer.rows() and not s.store.signals()
        assert s.store.decisions()['items'][0]['decision']['preview']
    asyncio.run(run())


def test_invalid_future_data_is_audited_and_never_selected(tmp_path):
    async def run():
        s,clock=setup(tmp_path);s.provider.rows=fixture()+[Candle(clock[0]+600,100,101,99,100)]
        s.start(['EUR/USD'],300);await drain(s)
        assert not s.store.signals()
        record=s.store.decisions()['items'][0]['decision']
        assert record['data_quality']=='invalid' and record['error_code']=='invalid_data'
    asyncio.run(run())


def test_pending_observations_revalidate_and_settle_grouped_after_restart(tmp_path):
    async def run():
        path=tmp_path/'observations.db';o=SelectionObserver(path);now=[fixture()[-1].t+305];ses=context(o,now[0])
        a=capture(o,ses,now[0]);capture(o,ses,now[0],{'selector':'candidate:cci','candidate':candidate(),'pool':[],'reason':''})
        o.db.close();o=SelectionObserver(path)
        class Provider:
            name='synthetic';calls=0
            async def candles(self,asset,interval,count):
                self.calls+=1;t=int(now[0]//60)*60;price=fixture()[-1].c
                return CandleBatch([Candle(x,price,price+.1,price-.1,price) for x in range(t-180,t,60)],'synthetic')
            async def settlement_candles(self,*args):
                self.calls+=1
                return CandleBatch([Candle(a['entry_at'],100,101,99,100),Candle(a['expiry_at'],101,102,100,101)],'synthetic')
        p=Provider();now[0]=a['entry_at']-45
        await o.step({'deriv':p},lambda:now[0]);assert p.calls==1
        assert all(r['state']=='pending_result' for r in o.rows())
        now[0]=a['expiry_at']+65
        await o.step({'deriv':p},lambda:now[0]);assert p.calls==2
        assert all(r['result']=='WIN' and r['settlement_source_identity']=='synthetic' for r in o.rows())
    asyncio.run(run())


def outcome(result,t=1000,**extra):
    return {'id':str(t)+result,'created_at':t-120,'entry_at':t,'expiry_at':t+60,'state':'encerrado','result':result,'settlement_pending':False,**extra}


def test_metrics_denominators_missing_and_payout_subset():
    rows=[outcome('WIN',1000,payout_pct=80,payout_kind='user_declared'),outcome('LOSS',2000),outcome('EMPATE',3000,payout_pct=80),
          outcome('Resultado indisponível',4000),outcome('WIN',5000,settlement_pending=True),outcome('LOSS',6000,state='cancelado')]
    m=summarize(rows)
    assert (m['wins'],m['losses'],m['ties'],m['pending'],m['unavailable'],m['cancelled'])==(1,1,1,1,1,1)
    assert m['evaluated']==2 and m['observed_rate_pct']==50 and m['win_rate_pct'] is None
    assert m['unavailable_pct']==25 and m['expected_roi_per_operation']==.4 and m['expectation_sample']==2
    assert summarize([outcome('WIN')])['expected_roi_per_operation'] is None
    assert m['wilson_95_pct'][0]<50<m['wilson_95_pct'][1]


def test_metrics_out_of_order_results_and_overlap_do_not_inflate_uncertainty():
    rows=[outcome('LOSS',1000,settled_at=9000),outcome('LOSS',1000,settled_at=8000,id='second'),outcome('WIN',1100,settled_at=7000)]
    m=summarize(list(reversed(rows)))
    assert m['max_loss_streak']==2 and m['overlap_clusters']==2 and m['nonoverlapping_sample']==2
    assert m['day_block_95_pct'] is None
    rows=[outcome('WIN' if i%3 else 'LOSS',1000+86400*i) for i in range(25)]
    assert summarize(rows)['day_block_95_pct'] is not None and len(summarize(rows)['weeks'])>=4


def test_metrics_separate_regime_version_configuration_and_identity():
    row=outcome('WIN',source_key='deriv',source_identity='one',strategy='cci',asset='EUR/USD',timeframe='M1',duration_seconds=60,
        evidence={'rules_version':'current','pipeline_version':'one','regime':'lateral','configuration':{'id':'frozen'}})
    variants=[row,{**row,'source_identity':'two'},{**row,'evidence':{**row['evidence'],'regime':'tendencial'}},
        {**row,'evidence':{**row['evidence'],'configuration':{'id':'other'}}}]
    assert len(grouped(variants))==4


def test_pair_comparison_separates_common_and_exclusives():
    base=outcome('WIN',source_identity='x',asset='EUR/USD',timeframe='M1',duration_seconds=60,direction='CALL',session_id='one',accepted=True)
    rows=[{**base,'selector':'reference_fixed'},{**base,'selector':'regime_router'},
          {**base,'selector':'reference_fixed','entry_at':2000,'result':'LOSS'}]
    r=compare_selectors(rows)
    assert r['common_signals']==1 and r['paired_resolved']==1 and r['paired_difference_pp']==0
    assert r['exclusive_left']['signals']==1 and r['exclusive_right']['signals']==0


def test_holdout_metrics_are_sealed_in_aggregate_and_phase():
    o=SelectionObserver(':memory:');now=fixture()[-1].t+305;session=context(o,now);r=capture(o,session,now)
    held=int((now+43*86400)//60)*60
    with o.db:
        item={**r,'created_at':held,'entry_at':held+120,'expiry_at':held+420,'state':'settled','settled_at':held+500,'result':'WIN',
              'entry_price':100,'expiry_price':101,'settlement_source_identity':r['source_identity'],
              'settlement_evidence':[Candle(held+120,100,100,100,100).json(),Candle(held+420,101,101,101,101).json()]}
        o.db.execute('UPDATE observations SET created=?,payload=? WHERE id=?',(held,json.dumps(item),r['id']))
    report=o.report(now+75*86400)['sessions'][0]
    assert report['groups']==[] and report['phases'][2]['groups']==[] and report['phases'][2]['sealed']
    opened=o.report(now+75*86400,include_final_test=True)['sessions'][0]
    assert opened['groups'][0]['wins']==1 and not opened['phases'][2]['sealed']


def test_decisions_and_original_signal_survive_outcome_updates(tmp_path):
    async def run():
        s,_=setup(tmp_path);s.start(['EUR/USD'],300);await drain(s);signal=s.store.signals()[0]
        original=s.store.db.execute('SELECT payload FROM signal_origins WHERE id=?',(signal['id'],)).fetchone()[0]
        decision=s.store.decisions()['items'][0]['decision']
        s.store.patch(signal['id'],{'state':'encerrado','result':'LOSS','settlement_pending':False})
        assert s.store.db.execute('SELECT payload FROM signal_origins WHERE id=?',(signal['id'],)).fetchone()[0]==original
        assert s.store.decisions()['items'][0]['decision']==decision
        with pytest.raises(sqlite3.IntegrityError):
            s.store.db.execute("UPDATE validation_decisions SET payload='{}'")
    asyncio.run(run())


def test_manual_confirmation_is_append_only_separate_from_learning_and_indicative(tmp_path):
    cfg=Config(database=str(tmp_path/'db.sqlite'));tg=TG()
    with TestClient(create_app(cfg,Feed(),tg)) as client:
        store=client.app.state.service.store
        original={**outcome('WIN',1000),'source_key':'deriv','asset':'EUR/USD','direction':'CALL','telegram':{'status':'enviado'},
                  'strategy':'cci','timeframe':'M1','duration_seconds':60,'entry_price':100,'expiry_price':101}
        store.add_signal(original,'one')
        before=deepcopy(store.signal(original['id']))
        body={'executed':True,'broker':'Declared broker','result':'LOSS','entry_price':100,'expiry_price':99,'payout_pct':80}
        first=client.post('/api/signals/'+original['id']+'/manual-confirmation',json=body)
        assert first.status_code==200 and first.json()['differences_at_recording']['result']
        second=client.post('/api/signals/'+original['id']+'/manual-confirmation',json={**body,'result':'WIN'})
        assert second.json()['supersedes']==first.json()['confirmation_id']
        assert store.signal(original['id'])==before and tg.calls==0
        report=client.get('/api/validation').json()
        assert report['operational_indicative'][0]['wins']==1 and report['manual_declared'][0]['wins']==1
        assert len(client.get('/api/signals/'+original['id']).json()['manual_confirmations'])==2
        assert client.post('/api/signals/'+original['id']+'/manual-confirmation',json={**body,'executed':False}).status_code==422
        assert client.post('/api/signals/'+original['id']+'/manual-confirmation',json={**body,'entry_at':1,'expiry_at':32503680000}).status_code==422
        assert client.post('/api/signals/'+original['id']+'/manual-confirmation',content=json.dumps({**body,'payout_pct':float('inf')}),headers={'content-type':'application/json'}).status_code==422


@pytest.mark.parametrize('value',['TRUE-ish',1,None])
def test_observation_flag_requires_bool(value):
    with pytest.raises(ValueError):Config(selection_observation=value).validate()


def test_shared_quotes_reuse_exact_provenance_and_expire_cache():
    from app.selection_observer import SharedQuotesProvider
    async def run():
        o=SelectionObserver(':memory:');now=[1800000000+305]
        rows=CandleBatch([Candle(1800000000+t,100,101,99,100) for t in (0,60,120,180,240)],'identity:one')
        class Provider:
            name='test';identity='identity:one';calls=0
            async def candles(self,*a):self.calls+=1;return rows
        p=Provider();o.remember('deriv','EUR/USD',60,rows,now[0]);proxy=SharedQuotesProvider(o,'deriv',p,lambda:now[0])
        result=await proxy.candles('EUR/USD',60,4)
        assert result.source_identity=='identity:one' and p.calls==0
        now[0]+=6;await proxy.candles('EUR/USD',60,4);assert p.calls==1
    asyncio.run(run())


def test_invalid_numeric_input_returns_422_and_never_echoes_input(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as c:
        body='{"provider":"deriv","assets":["EUR/USD"],"stake_brl":Infinity,"payout_pct":80}'
        response=c.put('/api/settings',content=body,headers={'content-type':'application/json'})
        assert response.status_code==422 and 'Infinity' not in response.text
        response=c.put('/api/settings',json={'provider':'secret-user-input','assets':['EUR/USD']})
        assert response.status_code==422 and 'secret-user-input' not in response.text


def test_metrics_do_not_count_results_unknown_at_reporting_time(tmp_path):
    s=Store(tmp_path/'db')
    r=outcome('WIN',1000,settled_at=2000)
    s.add_signal(r,'known-later')
    assert s.validation_report(1500)['operational_indicative'][0]['pending']==1
    assert s.validation_report(2500)['operational_indicative'][0]['wins']==1


def test_export_is_read_only_and_preserves_database_bytes(tmp_path):
    from scripts.export_validation import report
    path=tmp_path/'db';s=Store(path);s.add_signal(outcome('WIN',1000),'one');s.db.close()
    before=path.read_bytes();r=report(path)
    assert r['operational_indicative'][0]['wins']==1 and r['observation']['sessions']==[]
    assert path.read_bytes()==before and not path.with_suffix('.selection.db').exists()


def test_frozen_history_has_cold_start_and_one_causal_development_freeze(tmp_path):
    o=SelectionObserver(tmp_path/'db');start=99960;session=context(o,start)
    for row in records():
        delta=start-10020+60
        r={**row,'id':'candidate-'+row['id'],'created_at':row['entry_at']+delta-60,
           'entry_at':row['entry_at']+delta,'expiry_at':row['expiry_at']+delta,'settled_at':row['settled_at']+delta,
           'state':'settled','selector':'candidate:cci','session_id':session['id'],'configuration_id':session['id'],
           'settlement_evidence':[{**q,'t':q['t']+delta} for q in row['settlement_evidence']],'regime':'lateral','pipeline_version':PIPELINE_VERSION}
        with o.db:o.db.execute('INSERT INTO observations VALUES(?,?,?)',(r['id'],r['created_at'],json.dumps(r)))
    assert o.frozen_profiles(session,start+86400)==[]
    freeze=o.frozen_profiles(session,start+14*86400)
    assert freeze[0]['eligible'] and freeze[0]['sample']==90
    with o.db:o.db.execute("DELETE FROM observations")
    assert o.frozen_profiles(session,start+20*86400)==freeze
    original=o.db.execute('SELECT payload FROM profile_freezes').fetchone()[0]
    o.db.close();o=SelectionObserver(tmp_path/'db')
    assert o.frozen_profiles(session,start+30*86400)==freeze
    assert o.db.execute('SELECT payload FROM profile_freezes').fetchone()[0]==original
