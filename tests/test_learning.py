"""Synthetic correctness checks: these are not trading performance evidence."""
import asyncio
from unittest.mock import patch
import pytest
from app.learning import RULES_VERSION, PIPELINE_VERSION, profiles, choose_tie, operational_records, study_records
from app.indicators import rma, rsi, atr
from app.market import Candle
from app.strategies import scan_all, STRATEGIES
from app.strategies.apex_engines import _signal
from tests.test_system import setup, drain, fixture

CONTEXT=('synthetic', 'EUR/USD', 'M5', 300)
SCOPE={'source_key':'deriv','regime':'lateral','configuration_id':'synthetic-config'}


def records(strategy='cci', wins=90, mode='operacional'):
    result=[]
    for i in range(90):
        t=10020+i*600
        won=i<wins
        result.append(dict(id=str(i),rules_version=RULES_VERSION,pipeline_version=PIPELINE_VERSION,mode=mode,source_identity=CONTEXT[0],asset=CONTEXT[1],
            timeframe='M5',duration_seconds=300,direction='CALL',strategy=strategy,entry_at=t,expiry_at=t+300,
            settled_at=t+365,result='WIN' if won else 'LOSS',entry_price=100,expiry_price=101 if won else 99,
            created_at=t-60,state='encerrado',settlement_pending=False,**SCOPE,
            settlement_source_identity=CONTEXT[0],settlement_entry_at=t,settlement_expiry_at=t+300,
            accepted=True,settlement_evidence=[Candle(t,100,101,99,100).json(),
                Candle(t+300,101 if won else 99,102,98,100).json()]))
    return result


def test_learning_requires_later_validation_and_never_uses_future_results():
    rows=records()
    p=next(iter(profiles(rows,100000).values()))
    assert p['eligible'] and p['train_sample']==60 and p['validation_sample']==30
    assert set(p['train_ids']).isdisjoint(p['validation_ids'])
    assert p['validation_start']>p['train_start']
    earlier=next(iter(profiles(rows,rows[-1]['settled_at']-1).values()))
    assert not earlier['eligible'] and earlier['sample']==89
    # Excellent training followed by bad validation must not earn preference.
    p=next(iter(profiles(records(wins=65),100000).values()))
    assert not p['eligible'] and p['validation_win_rate_pct']==16.67


def test_learning_rejects_old_rules_unavailable_and_inconsistent_outcomes():
    rows=records()
    assert not profiles([{**r,'rules_version':'1.16.1'} for r in rows],100000)
    assert not profiles([{**r,'result':'INDISPONIVEL'} for r in rows],100000)
    assert not profiles([{**r,'expiry_price':99} for r in rows],100000)
    assert not profiles([{**r,'entry_price':float('nan')} for r in rows],100000)


def test_overlapping_and_duplicate_observations_do_not_inflate_sample():
    rows=records()
    overlapped=[{**r,'id':'overlap-'+r['id'],'entry_at':r['entry_at']+60,'expiry_at':r['expiry_at']+60,'settled_at':r['settled_at']+60,'settlement_entry_at':r['entry_at']+60,'settlement_expiry_at':r['expiry_at']+60} for r in rows]
    overlapped=[{**r,'settlement_evidence':[{**q,'t':q['t']+60} for q in original['settlement_evidence']]} for r,original in zip(overlapped,rows)]
    p=next(iter(profiles(rows+overlapped+rows,100000).values()))
    assert p['sample']==90 and p['excluded_overlap']==180


def test_contexts_sources_and_modes_are_not_pooled():
    rows=records()[:45]
    for change in ({'mode':'estudo'},{'asset':'GBP/USD'},{'source_identity':'other'},{'timeframe':'M1'}):
        result=profiles(rows+[{**r,**change,'settlement_source_identity':change.get('source_identity',r['source_identity'])} for r in records()[45:]],100000)
        assert len(result)==2 and not any(p['eligible'] for p in result.values())


def test_learning_prefers_eligible_profiles_without_requiring_all_profiles():
    a={'direction':'CALL','score':90,'setup_candle':123,'reasons':[],'strategy_resolved':'cci'}
    b={**a,'strategy_resolved':'fibonacci'}
    learned=profiles(records('cci',wins=86)+records('fibonacci'),100000)
    assert choose_tie([a,b],learned,CONTEXT,scope=SCOPE)['strategy_resolved']=='fibonacci'
    assert choose_tie([a,b],learned,CONTEXT,scope=SCOPE)['score']==90
    assert choose_tie([a,b],profiles(records(),100000),CONTEXT,scope=SCOPE)['learning']['applied']


def test_learning_does_not_mix_study_and_operational_profiles_in_comparison():
    a={'direction':'CALL','score':90,'strategy_resolved':'cci'}
    b={**a,'strategy_resolved':'fibonacci'}
    learned=profiles(records('cci')+records('fibonacci',mode='estudo'),100000)
    assert choose_tie([a,b],learned,CONTEXT,scope=SCOPE)['learning']['mode']=='operacional'
    learned.update(profiles(records('cci',wins=86,mode='estudo'),100000))
    winner=choose_tie([a,b],learned,CONTEXT,scope=SCOPE)
    assert winner['strategy_resolved']=='cci' and winner['learning']['mode']=='operacional'


def test_pending_or_cancelled_operational_signals_are_not_learning_data():
    base={**records()[0],'evidence':{'rules_version':RULES_VERSION},'strategy_resolved':'cci','settlement_pending':True}
    assert not list(operational_records([base]))
    assert not list(operational_records([{**base,'state':'cancelado','settlement_pending':False}]))
    assert len(list(operational_records([{**base,'state':'encerrado','settlement_pending':False}])))==1


def test_wilder_indicators_use_full_causal_history():
    assert rma([1,2,3,10],3)==pytest.approx(14/3)
    # SMA of the final three changes gives 50; Wilder retains earlier gains.
    assert rsi([100,102,101,104,102],3)==pytest.approx(100*5/9)
    assert rsi([100,103,102,105,104],3)==pytest.approx(100*12/17)
    assert rsi([10]*20)==50 and rsi(list(range(20)))==100
    candles=[Candle(i*60+60,100,100+v,100-v,100) for i,v in enumerate([1,1,2,3,5])]
    assert atr(candles,3)==pytest.approx(6)
    # No future candle is part of this prefix calculation.
    assert atr(candles[:-1],3)==pytest.approx(4)


def test_score_varies_with_measured_confirmation_without_probability():
    weak=Candle(120,100,102,99,101)
    strong=Candle(120,99.2,102,99,101.9)
    a=_signal([weak],'CALL','test',['Mandatory (45 pts)'],3)[0]
    b=_signal([strong],'CALL','test',['Mandatory (45 pts)'],3)[0]
    assert 70<=a['score']<b['score']<=100
    assert '(45 pts)' not in a['reasons'][0] and 'probability' not in b


def test_later_asset_can_win_and_entry_does_not_wait_for_m5_boundary(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        candidate={'direction':'CALL','score':80,'setup_candle':fixture()[-1].t,'reasons':['synthetic']}
        with patch('app.service.evaluate',side_effect=[(candidate,''),({**candidate,'score':95},'')]):
            service.start(['EUR/USD','GBP/USD'],300)
            await drain(service)
        signal=service.store.signals()[0]
        assert signal['asset']=='GBP/USD'
        assert len(service.store.analyses()[0]['ranking'])==2
        assert 60<=signal['entry_at']-now[0]<120 and signal['entry_at']%60==0
        assert signal['evidence']['rules_version']==RULES_VERSION
    asyncio.run(run())


def test_stale_candidate_is_discarded_after_other_assets_take_too_long(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        original=service.provider.candles
        async def delayed(asset,interval=300,count=100):
            if asset=='GBP/USD':now[0]+=300
            return await original(asset,interval,count)
        service.provider.candles=delayed
        service.start(['EUR/USD','GBP/USD'],300)
        await drain(service)
        assert not service.store.signals()
        assert any(r['kind']=='discarded' for r in service.store.analyses()[0]['checks'])
    asyncio.run(run())


def test_study_learning_survives_restart_and_records_version(tmp_path):
    from app.night_study import Study
    path=tmp_path/'night-study.db'
    assert study_records(path)==[] and not path.exists()
    study=Study(path)
    study.add('night','EUR/USD','cci',dict(direction='CALL',score=85,setup_candle=300),[],302,source_identity='synthetic')
    row=study.pending()[0]
    study.settle(row,[Candle(420,100,101,99,100),Candle(720,101,102,100,101)],785)
    study.db.close()
    rows=study_records(path)
    assert len(rows)==1 and rows[0]['rules_version']==RULES_VERSION
    assert rows[0]['mode']=='estudo' and rows[0]['source_identity']=='synthetic'
    assert not profiles(rows,800)  # Legacy study lacks exact operational proof and stays inactive.


def test_learning_expires_without_recent_outcomes():
    from app.learning import MAX_PROFILE_AGE
    rows=records()
    p=next(iter(profiles(rows,rows[-1]['settled_at']+MAX_PROFILE_AGE+1).values()))
    assert not p['eligible'] and not p['recent']


def test_service_uses_persistent_learning_and_audits_the_actual_tiebreak(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        service.select_strategy('scan_all')
        from app.selection_observer import config_snapshot
        from app.strategies.regime import classify
        configuration=config_snapshot(service.config,{**service.settings(),'assets':['EUR/USD'],'timeframe':'M5','duration_seconds':300})
        for row in records('cci',wins=86)+records('fibonacci'):
            offset=(now[0]-100000)//60*60
            row.update({key:row[key]+offset for key in ('entry_at','expiry_at','settled_at','settlement_entry_at','settlement_expiry_at')})
            row['settlement_evidence']=[{**q,'t':q['t']+offset} for q in row['settlement_evidence']]
            signal={**row,'id':row['strategy']+'-'+row['id'],'created_at':row['entry_at']-60,
                'state':'encerrado','settlement_pending':False,'telegram':{'status':'enviado'},
                'strategy_resolved':row['strategy'],'source_identity':service.provider.name,
                'settlement_source_identity':service.provider.name,
                'evidence':{'rules_version':RULES_VERSION,'pipeline_version':PIPELINE_VERSION,
                    'regime':classify(fixture()) or 'transicao','configuration':configuration}}
            service.store.add_signal(signal,signal['id'])
        c={'direction':'CALL','score':90,'setup_candle':fixture()[-1].t,'reasons':['synthetic']}
        with patch.dict(STRATEGIES,{'cci':lambda rows:(c,''),'fibonacci':lambda rows:(c,''),'scan_all':scan_all},clear=True):
            service.start(['EUR/USD'],300)
            await drain(service)
        signal=service.store.signals()[0]
        assert signal['strategy_resolved']=='fibonacci'
        assert signal['learning']['applied'] and signal['learning']['mode']=='operacional'
        assert signal['evidence']['learning']['validation_sample']==30
        assert service.snapshot()['learning']['eligible_profiles']==2
        service.store.db.close()
        from app.store import Store
        reopened=Store(service.config.database)
        assert reopened.signal(signal['id'])['learning']==signal['learning']
        reopened.db.close()
    asyncio.run(run())


def test_even_fully_eligible_simulated_profiles_cannot_change_selection():
    a={'direction':'CALL','score':90,'strategy_resolved':'cci'}
    b={**a,'strategy_resolved':'fibonacci'}
    learned=profiles(records('cci',wins=86,mode='estudo')+records('fibonacci',mode='estudo'),100000)
    assert all(p['eligible'] for p in learned.values())
    selected=choose_tie([a,b],learned,CONTEXT,scope=SCOPE)
    assert selected['strategy_resolved']=='cci' and not selected['learning']['applied']


def test_backend_does_not_start_or_create_simulated_study(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config
    from tests.test_system import Feed,TG
    with TestClient(create_app(Config(database=str(tmp_path/'aurora.db')),Feed(),TG())) as client:
        state=client.get('/api/state').json()
        assert 'study' not in state
        assert not (tmp_path/'night-study.db').exists()
