"""Synthetic software regressions; percentages here prove arithmetic, not efficacy."""
import asyncio
import json
import math
from copy import deepcopy
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.store import Store
from app.learning import PIPELINE_VERSION, RULES_VERSION, profiles
from app.experiments import KEYS as PRICE_ACTION_KEYS
from app.market import Candle
from app.strategies import scan_all, STRATEGIES
from app.strategies.quality import evidence_groups
from app.selection_context import structure_context, higher_context, feature_status
from app.selection_observer import alternatives, config_snapshot, SelectionObserver
from app.explanations import build_explanation
from app.telegram import caption, card
from app.win_rate import rate_text
from tests.test_system import setup, drain, fixture
from tests.test_learning import records, CONTEXT, SCOPE


def signal(i=0,result='WIN',**changes):
    entry=60000+i*600
    s={'id':str(i),'asset':'EUR/USD','source_key':'deriv','source_identity':'synthetic',
       'strategy':'scan_all','strategy_resolved':'cci','timeframe':'M5','duration_seconds':300,
       'direction':'CALL','score':95,'setup_candle':entry-300,'created_at':entry-90,
       'entry_at':entry,'expiry_at':entry+300,'settled_at':entry+365,
       'state':'encerrado','result':result,'settlement_pending':False,
       'entry_price':100,'expiry_price':101 if result=='WIN' else 99 if result=='LOSS' else 100,
       'settlement_source_identity':'synthetic','settlement_entry_at':entry,'settlement_expiry_at':entry+300,
       'telegram':{'status':'enviado','at':entry-80},
       'reasons':['Critério original (70 pts)'],
       'evidence':{'rules_version':RULES_VERSION,'pipeline_version':PIPELINE_VERSION,'regime':'lateral',
                   'configuration':{'id':'stable','selector':'reference_1232_adaptive','selector_version':'1.25.0-minimum-first'}}}
    s.update(changes)
    if 'settlement_evidence' not in changes:
        s['settlement_evidence']=settlement_quotes(s)
    return s


def settlement_quotes(s):
    # Synthetic proof for accounting tests; never distributed as market history.
    quotes=[]
    for t, p in [(s['entry_at'],s.get('entry_price')),(s['expiry_at'],s.get('expiry_price'))]:
        if type(p) not in (int,float) or not math.isfinite(p) or p<=0:
            return []
        quotes.append({'t':int(t) if t==int(t) else t,'o':p,'h':p+1,'l':p-.5,'c':p})
    return quotes


def candidate(key,score=95,direction='CALL'):
    return {'strategy_resolved':key,'direction':direction,'score':score,
            'setup_candle':fixture()[-1].t,'reasons':['Candle fechado']}


def seed(store,n=30):
    for i in range(n):store.add_signal(signal(i,'WIN' if i<18 else 'LOSS'),str(i))










def test_historical_rate_is_true_arithmetic_and_not_score():
    store=Store(':memory:');seed(store)
    snapshot=store.win_rate_snapshot(signal(90,score=100),200000)
    assert snapshot['rate_pct']==60 and snapshot['sample']==30
    assert snapshot['wins']==18 and snapshot['losses']==12
    assert snapshot['wilson_95_pct'][0]<60<snapshot['wilson_95_pct'][1]
    assert snapshot['probability_estimate'] is False
    assert '60,0%' in rate_text({'observed_win_rate':snapshot})[0]


def test_small_sample_ties_unknown_and_future_do_not_become_a_forecast():
    store=Store(':memory:');seed(store,29)
    for i,r in [(30,'EMPATE'),(31,'Resultado indisponível')]:store.add_signal(signal(i,r),str(i))
    store.add_signal(signal(32,settled_at=300000),'future')
    snap=store.win_rate_snapshot(signal(90),200000)
    assert snap['sample']==29 and snap['rate_pct'] is None and snap['ties']==1
    assert snap['excluded_incomplete_or_unknown']==2
    assert 'Taxa de acerto histórica: indisponível' in rate_text({'score':100,'observed_win_rate':snap})[0]


@pytest.mark.parametrize('field,value',[('asset','GBP/USD'),('source_key','coinbase'),('source_identity','other'),
    ('timeframe','M1'),('duration_seconds',60),('direction','PUT')])
def test_rates_never_pool_different_instruments_or_combinations(field,value):
    store=Store(':memory:');seed(store)
    assert store.win_rate_snapshot(signal(90,**{field:value}),200000)['sample']==0


@pytest.mark.parametrize('section,field,value',[('evidence','pipeline_version','1.22.10'),('evidence','rules_version','old'),
    ('evidence','regime','tendencial'),('configuration','id','other'),('configuration','selector_version','old')])
def test_rates_separate_versions_regimes_and_configurations(section,field,value):
    store=Store(':memory:');seed(store);request=signal(90)
    (request['evidence'] if section=='evidence' else request['evidence']['configuration'])[field]=value
    assert store.win_rate_snapshot(request,200000)['sample']==0


@pytest.mark.parametrize('changes',[{'settlement_source_identity':'other'},{'settlement_entry_at':123},
    {'settled_at':None},{'expiry_price':99},{'settlement_pending':True},{'state':'cancelado'}])
def test_incomplete_or_inconsistent_prices_are_excluded_from_public_signal_rate(changes):
    store=Store(':memory:');store.add_signal(signal(0,**changes),'invalid')
    assert store.win_rate_snapshot(signal(90),200000)['sample']==0


def test_rate_and_engine_explanation_remain_original_after_result_and_restart(tmp_path):
    path=tmp_path/'signals.db';store=Store(path);seed(store)
    s=signal(40,state='aguardando entrada',settlement_pending=True,result='Resultado indisponível')
    s['observed_win_rate']=store.win_rate_snapshot(s,100000)
    s['engine_explanation']=build_explanation(s)
    store.add_signal(s,'new');original=store.db.execute('SELECT payload FROM signal_origins WHERE id=?',(s['id'],)).fetchone()[0]
    store.patch(s['id'],{'state':'encerrado','settlement_pending':False,'result':'WIN','expiry_price':101,'settlement_evidence':settlement_quotes({**s,'expiry_price':101})},'apuração',s['settled_at'])
    assert store.signal(s['id'])['observed_win_rate']['rate_pct']==60
    assert ' pts)' not in s['engine_explanation']['text']
    assert s['engine_explanation']['reasons']==['Critério original (70 pts)']
    store.db.close();store=Store(path)
    assert store.db.execute('SELECT payload FROM signal_origins WHERE id=?',(s['id'],)).fetchone()[0]==original
    assert store.signal(s['id'])['observed_win_rate']==s['observed_win_rate']
    assert store.win_rate_snapshot(signal(90),200000)['sample']==31


def test_server_paginates_before_loading_groups_and_preserves_archived_metrics():
    store=Store(':memory:')
    for i in range(103):
        row=signal(i);row['evidence']['configuration']['id']=f'cfg-{i:03}'
        row['evidence']['candles']=[{'unneeded':'x'*5000}]
        store.add_signal(row,str(i))
    with patch.object(store,'all_signals',side_effect=AssertionError('No full-history decode allowed')):
        with patch('app.validation_index.summarize',wraps=__import__('app.validation_metrics',fromlist=['summarize']).summarize) as summarize:
            first=store.validation_page(200000);assert summarize.call_count==10
        last=store.validation_page(200000,page=11)
        assert len(first['items'])==10 and first['total']==103 and len(last['items'])==3
        assert store.validation_counts(200000)['generated']==103
        assert store.validation_page(200000,query='cfg-102')['total']==1
        assert store.validation_page(200000,query="' OR 1=1 --")['total']==0
        assert store.validation_page(200000,source='coinbase')['total']==0
        assert store.validation_page(200000,page=999)['page']==11
    assert 'unneeded' not in json.dumps(first)
    store.clear_history('all',None,200000)
    assert store.validation_page(200000)['total']==103


def manual(result='LOSS',broker='Corretora A',executed=True):
    return {'executed':executed,'result':result if executed else None,'broker':broker,'note':'teste',
            'entry_at':None,'expiry_at':None,'entry_price':None,'expiry_price':None,'payout_pct':None}


def test_manual_versions_are_separate_and_causal_even_with_later_corrections(tmp_path):
    store=Store(tmp_path/'signals.db');store.add_signal(signal(),'first')
    first=store.confirm_manual('0',manual(),100000)
    store.confirm_manual('0',manual(result='WIN',broker='Corretora B'),110000)
    early=store.validation_page(105000,scope='manual')['items']
    later=store.validation_page(120000,scope='manual')['items']
    assert early[0]['broker']=='Corretora A' and early[0]['losses']==1
    assert later[0]['broker']=='Corretora B' and later[0]['wins']==1
    assert store.validation_counts(105000)['manual_executed_declared']==1
    store.confirm_manual('0',manual(executed=False),130000)
    assert store.validation_counts(140000)['manual_executed_declared']==0
    assert store.validation_page(140000,scope='manual')['items']==[]
    assert store.signal('0')['result']=='WIN' and len(store.manual_confirmations())==3
    assert first['externally_verified'] is False
    store.db.close();store=Store(tmp_path/'signals.db')
    assert store.validation_page(105000,scope='manual')['items'][0]['losses']==1


def test_projection_backfill_preserves_old_originals_without_forging_rate(tmp_path):
    path=tmp_path/'legacy.db';store=Store(path);seed(store)
    store.confirm_manual('0',manual(),100000)
    original=store.db.execute('SELECT payload FROM signal_origins ORDER BY id').fetchall()
    store.db.execute('DROP TABLE metric_projection');store.db.execute('DROP TABLE metric_projection_version');store.db.commit();store.db.close()
    store=Store(path)
    assert store.validation_counts(200000)['generated']==30
    assert store.win_rate_snapshot(signal(90),200000)['rate_pct']==60
    assert [r[0] for r in store.db.execute('SELECT payload FROM signal_origins ORDER BY id')]==[r[0] for r in original]
    assert all('observed_win_rate' not in s for s in store.signals())


def candles(n=150,interval=300):
    return [Candle(60000+i*interval,100+i*.03,100+i*.03+.4,100+i*.03-.4,100+i*.03+.05) for i in range(n)]


def test_closed_higher_context_reuses_data_and_never_uses_future_or_incomplete_groups():
    rows=candles();cutoff=rows[-1].t+300
    context=higher_context(rows,300,cutoff)
    future=rows+[Candle(cutoff,1,999,1,999)]
    assert context==higher_context(future,300,cutoff)
    assert context['status']=='available' and context['additional_api_calls']==0
    assert context['last_close_at']<=cutoff and context['timeframe']=='M15'
    assert all(c['t']+900<=cutoff and c['v'] is None for c in context['candles'])
    assert higher_context(rows[:30],300,rows[29].t+300)['status']=='unavailable'
    assert higher_context(rows[:15]+rows[16:],300,cutoff)['status']=='unavailable'
    assert higher_context(rows+rows[-1:],300,cutoff)['status']=='unavailable'


def test_structure_context_uses_confirmed_pivots_and_does_not_call_every_trend_a_structure():
    rows=[]
    for i in range(120):
        p=100+i*.03+math.sin(i*math.pi/5)*.4
        rows.append(Candle(60000+i*300,p,p+.15,p-.15,p+.02))
    cutoff=rows[-1].t+300;context=structure_context(rows,300,cutoff)
    assert context['status']=='available' and context['regime']=='tendencial' and context['direction']=='CALL'
    assert all(p['confirmed_at']<=cutoff for p in context['highs']+context['lows'])
    assert structure_context(rows+[Candle(cutoff,999,999,998,999)],300,cutoff)==context
    assert structure_context(candles(120),300,cutoff)['status']=='insufficient'


def test_old_or_missing_pipeline_cannot_earn_current_learning_bonus():
    assert any(p['eligible'] for p in profiles(records(),100000).values())
    for value in ('1.22.10',None):
        learned=profiles([{**r,'pipeline_version':value} for r in records()],100000)
        assert not any(p['eligible'] or p['blocked'] for p in learned.values())


def test_observation_toggle_is_persistent_and_does_not_change_operational_context(tmp_path):
    async def run():
        service,_=setup(tmp_path)
        before=config_snapshot(service.config,service.settings())
        service.set_observation(True)
        assert service.observer is not None and service.observation_enabled
        assert config_snapshot(service.config,service.settings())['id']==before['id']
        service.set_observation(False)
        assert not service.observation_enabled and service.observer is not None
        assert not service.scan['active'] and service.telegram.calls==0 and service.provider.calls==0
        service.scan['active']=True
        with pytest.raises(RuntimeError):service.set_observation(True)
        service.scan['active']=False
        assert service.store.settings({})['selection_observation'] is False
        # Pending experimental work can recover after restart without new capture.
        service.observer.db.close()
    asyncio.run(run())


def test_paged_api_summary_filters_toggle_and_feature_roles(tmp_path):
    cfg=Config(database=str(tmp_path/'api.db'),telegram_token='',telegram_chat='')
    with TestClient(create_app(cfg)) as client:
        service=client.app.state.service
        for i in range(23):
            s=signal(i);s['evidence']['configuration']['id']=str(i);service.store.add_signal(s,str(i))
        with patch.object(service.store,'all_signals',side_effect=AssertionError('Full report should not be called')):
            summary=client.get('/api/validation/summary').json()
            page=client.get('/api/validation/groups?page=2').json()
        assert summary['counts']['generated']==23 and len(page['items'])==10 and page['total']==23
        assert summary['features']['news']['active_filter'] is False
        assert summary['features']['higher_timeframe']['active_filter'] is False
        assert client.put('/api/validation/observation',json={'enabled':True}).json()['enabled'] is True
        settings=service.settings()
        assert client.put('/api/settings',json={k:v for k,v in settings.items() if k!='selection_observation'}).status_code==200
        assert service.observation_enabled is True
        assert client.put('/api/validation/observation',json={'enabled':False}).status_code==200
        assert not service.observation_enabled
        assert client.get('/api/validation/groups?page_size=51').status_code==422
        service.scan['active']=True
        assert client.put('/api/validation/observation',json={'enabled':True}).status_code==409
        service.scan['active']=False


def test_messages_and_image_use_observed_percentage_without_technical_score():
    store=Store(':memory:');seed(store);s=signal(90)
    s['observed_win_rate']=store.win_rate_snapshot(s,200000)
    s['engine_explanation']=build_explanation(s)
    message=caption(s)
    assert 'Taxa de acerto histórica: 60,0%' in rate_text(s)[0]
    assert '30 resultados apurados' in rate_text(s)[1]
    assert 'Taxa de acerto' not in message
    assert ' pts)' not in message and '/100' not in message
    assert 'indicativos' in rate_text(s)[1] and len(message.encode('utf-16-le'))//2<=1024
    assert card(s).startswith(b'\x89PNG')
    legacy=caption({k:v for k,v in s.items() if k!='observed_win_rate'})
    assert 'Taxa de acerto' not in legacy and '💱Ativo:' in legacy


def test_paged_future_result_stays_pending_and_out_of_order_counts_are_stable():
    store=Store(':memory:');seed(store)
    store.patch('1',{'settled_at':300000},'later result',300000)
    page=store.validation_page(200000)['items'][0]
    assert page['evaluated']==29 and page['pending']==1
    assert store.win_rate_snapshot(signal(90),200000)['sample']==29
    store.patch('1',{'settled_at':61000},'known result',61000)
    assert store.validation_page(200000)['items'][0]['evaluated']==30
    assert store.win_rate_snapshot(signal(90),200000)['rate_pct']==60


@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_bad_legacy_number_does_not_break_projection_or_become_price(value):
    store=Store(':memory:');store.add_signal(signal(0,entry_price=value),'bad-number')
    assert store.validation_counts(200000)['generated']==1
    assert store.win_rate_snapshot(signal(90),200000)['sample']==0
    page=store.validation_page(200000)
    assert 'NaN' not in json.dumps(page,allow_nan=False)
    assert page['items'][0]['price_verified']['observed_rate_pct'] is None
    assert page['items'][0]['legacy_unverified_outcomes']==1
    assert page['items'][0]['wins']==1  # Registered history is retained, not repaired silently.
    assert not math.isfinite(store.signal('0')['entry_price'])
