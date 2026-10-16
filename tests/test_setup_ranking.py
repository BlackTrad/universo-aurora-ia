"""Synthetic accounting, causality and migration; not strategy efficacy tests."""
import json
from datetime import datetime
from copy import deepcopy
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.store import Store
from app.config import Config
from app.main import create_app
from app.learning import PIPELINE_VERSION
from tests.test_evolution_1250 import signal, manual, settlement_quotes
from tests.test_system import Feed, TG


def put(store, i, result='WIN', **changes):
    row=signal(i,result)
    row.update(changes)
    if changes.get('duration_seconds'):
        row.update(expiry_at=row['entry_at']+row['duration_seconds'],
                   settlement_expiry_at=row['entry_at']+row['duration_seconds'],
                   settled_at=row['entry_at']+row['duration_seconds']+65)
    if 'settlement_evidence' not in changes: row['settlement_evidence']=settlement_quotes(row)
    assert store.add_signal(row,str(i))
    return row


def test_rank_is_by_win_count_not_rate_score_or_last_page():
    store=Store(':memory:')
    for i in range(50):put(store,i,'WIN' if i<31 else 'LOSS',score=70)
    put(store,50,strategy_resolved='bb_reversion',score=100)
    with patch.object(store,'all_signals',side_effect=AssertionError('No original-payload scan')):
        report=store.setup_ranking(200000)
    assert [(r['strategy'],r['wins']) for r in report['items']]==[('cci',31),('bb_reversion',1)]
    assert report['items'][0]['sample']==50 and report['items'][0]['win_rate_pct']==62
    assert report['items'][1]['win_rate_pct'] is None and report['items'][1]['sample']==1
    assert 'score' not in json.dumps(report) and report['probability_estimate'] is False


@pytest.mark.parametrize('field,value',[('source_key','coinbase'),('source_identity','other'),
    ('timeframe','M1'),('duration_seconds',60)])
def test_ranking_keeps_sources_timeframes_and_expirations_separate(field,value):
    store=Store(':memory:');put(store,0)
    changes={field:value}
    if field=='source_identity':changes['settlement_source_identity']=value
    put(store,1,**changes)
    rows=store.setup_ranking(200000)['items']
    assert len(rows)==2 and sum(r['wins'] for r in rows)==2


def test_rules_versions_default_current_and_explicit_legacy_never_merge():
    store=Store(':memory:');put(store,0)
    evidence=deepcopy(signal()['evidence']);evidence['pipeline_version']='old-pipeline'
    put(store,1,evidence=evidence)
    evidence['rules_version']='different-rules'
    put(store,2,evidence=evidence)
    assert store.setup_ranking(200000)['total']==1
    all_versions=store.setup_ranking(200000,pipeline='')['items']
    assert len(all_versions)==3 and all(r['wins']==1 for r in all_versions)
    assert len(store.setup_ranking(200000,pipeline='old-pipeline')['items'])==2


def test_incomplete_and_future_outcomes_do_not_inflate_wins():
    store=Store(':memory:');put(store,0)
    put(store,1,settlement_source_identity='wrong')
    put(store,2,entry_price=None)
    put(store,3,expiry_price=99)
    put(store,4,settled_at=300000)
    put(store,5,'EMPATE')
    put(store,6,'Resultado indisponível')
    put(store,7,state='aguardando entrada',settlement_pending=True)
    put(store,8,state='cancelado')
    row=store.setup_ranking(200000)['items'][0]
    assert (row['wins'],row['losses'],row['ties'])==(1,0,1)
    assert (row['unverified'],row['pending'],row['unavailable'],row['cancelled'])==(3,2,1,1)
    assert row['sample']==1
    assert store.setup_ranking(400000)['items'][0]['wins']==2


def test_manual_declarations_latest_known_correction_and_nonexecution():
    store=Store(':memory:');put(store,0)
    store.confirm_manual('0',manual(result='WIN',broker='A'),100000)
    store.confirm_manual('0',manual(result='LOSS',broker='B'),110000)
    early=store.setup_ranking(105000,scope='manual')
    assert early['items'][0]['wins']==1 and early['items'][0]['broker']=='A'
    assert store.setup_ranking(120000,scope='manual')['items']==[]
    assert store.setup_ranking(120000,scope='manual')['counts']['losses']==1
    assert store.setup_ranking(120000)['items'][0]['wins']==1
    store.confirm_manual('0',manual(executed=False),130000)
    assert store.setup_ranking(140000,scope='manual')['counts']['signals']==0


def test_manual_brokers_remain_separate():
    store=Store(':memory:')
    for i,broker in enumerate(('A','B')):
        put(store,i);store.confirm_manual(str(i),manual(result='WIN',broker=broker),100000+i)
    report=store.setup_ranking(200000,scope='manual')
    assert len(report['items'])==2 and {r['broker'] for r in report['items']}=={'A','B'}
    assert report['basis']=='manual_user_declared'


def at(value):return datetime.fromisoformat(value).timestamp()


def test_day_and_month_boundaries_use_entry_time_in_brasilia():
    store=Store(':memory:')
    times=('2026-10-06T02:30:00+00:00','2026-10-06T03:30:00+00:00',
           '2026-10-01T02:30:00+00:00','2026-10-01T03:30:00+00:00')
    for i,value in enumerate(times):
        entry=at(value)
        put(store,i,entry_at=entry,created_at=entry-90,expiry_at=entry+300,
            settled_at=entry+365,settlement_entry_at=entry,settlement_expiry_at=entry+300)
    now=at('2026-10-06T15:00:00+00:00')
    assert store.setup_ranking(now,period='day')['items'][0]['wins']==1
    assert store.setup_ranking(now,period='month')['items'][0]['wins']==3
    assert store.setup_ranking(now,period='all')['items'][0]['wins']==4


def test_filters_stable_ties_limit_and_unattributed_signals():
    store=Store(':memory:')
    for i,name in enumerate(('cci','bb_reversion','ema_pullback','fibonacci','stochastic','ichimoku')):
        put(store,i,strategy_resolved=name)
    put(store,7,strategy_resolved='scan_all')
    report=store.setup_ranking(200000)
    assert report['total']==6 and len(report['items'])==5 and report['unknown_strategy']==1
    assert report['items']==store.setup_ranking(200000)['items']
    assert len(store.setup_ranking(200000,limit=10)['items'])==6
    assert store.setup_ranking(200000,source="' OR 1=1 --")['items']==[]
    assert store.setup_ranking(200000,asset='GBP/USD')['items']==[]
    assert store.setup_ranking(200000,timeframe='M1')['items']==[]
    assert store.setup_ranking(200000,duration_seconds=60)['items']==[]
    assert store.setup_ranking(200000,source='deriv',asset='EUR/USD',timeframe='M5',duration_seconds=300)['total']==6


def test_patch_after_out_of_order_results_updates_projection_once_and_keeps_archive():
    store=Store(':memory:')
    put(store,0,settlement_pending=True)
    put(store,1,'LOSS')
    assert store.setup_ranking(200000)['items']==[]
    original=store.signal('0')
    store.patch('0',{'settlement_pending':False})
    assert store.setup_ranking(200000)['items'][0]['wins']==1
    store.patch('0',{'settlement_pending':False})
    assert store.setup_ranking(200000)['items'][0]['wins']==1
    store.clear_history('all',None,200000)
    assert store.setup_ranking(200000)['items'][0]['wins']==1
    assert store.setup_ranking(200000)['history_scope']=='complete_including_archived'
    assert store.signal('0')['entry_price']==original['entry_price']


def test_old_projection_migration_restart_variants_and_originals_preserved(tmp_path):
    path=tmp_path/'old.db';store=Store(path)
    put(store,0,strategy_resolved='order_block',apex_setup='Order Block + FVG')
    put(store,1,strategy_resolved='order_block',apex_setup='Order Block')
    original=[tuple(r) for r in store.db.execute('SELECT * FROM signals ORDER BY id')]
    store.db.execute('DROP TABLE metric_projection')
    store.db.execute('CREATE TABLE metric_projection(signal_id TEXT,scope TEXT,group_id TEXT,metadata TEXT,payload TEXT,created REAL,PRIMARY KEY(signal_id,scope))')
    store.db.execute("UPDATE metric_projection_version SET version='1.25.0-1'");store.db.commit();store.db.close()
    store=Store(path)
    assert [tuple(r) for r in store.db.execute('SELECT * FROM signals ORDER BY id')]==original
    report=store.setup_ranking(200000)
    assert report['total']==2 and {r['apex_setup'] for r in report['items']}=={'Order Block','Order Block + FVG'}
    store.db.close();store=Store(path)
    assert store.setup_ranking(200000)['items']==report['items']


def test_api_validation_idle_and_no_network_effect(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'api.db')),Feed(),TG())) as client:
        service=client.app.state.service;service.clock=lambda:200000
        put(service.store,0)
        risk=service.store.risk_settings()
        with patch.object(service.provider,'candles',side_effect=AssertionError('No market request')):
            response=client.get('/api/validation/setups')
            assert response.status_code==200 and response.json()['items'][0]['wins']==1
        assert response.json()['filters']['pipeline']==PIPELINE_VERSION
        for query in ('scope=experimental','period=invalid','limit=11','limit=0','duration_seconds=10','source='+'x'*41):
            assert client.get('/api/validation/setups?'+query).status_code==422
        assert service.scan['active'] is False and service.store.risk_settings()==risk
        assert len(service.store.signals())==1
