"""Original geometry, causal descriptive rates and read-only dashboard APIs."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from app.config import Config
from app.learning import RULES_VERSION, PIPELINE_VERSION
from app.main import create_app
from app.market import Candle
from app.signal_visuals import annotations
from app.store import Store
from app.strategy_review import report
from tests.test_system import Feed, TG

BASE = 1800000000


def signal(index=0, ratio=.5, result='WIN', **changes):
    entry = BASE + index * 600
    price = 101 if result == 'WIN' else 99 if result == 'LOSS' else 100
    s = {'id': str(index), 'asset': 'GBP/USD', 'source_key': 'deriv', 'source': 'TESTE SINTÉTICO',
         'source_identity': 'deriv:test', 'timeframe': 'M5', 'duration_seconds': 300,
         'direction': 'CALL', 'strategy': 'scan_all', 'strategy_resolved': 'fibonacci',
         'created_at': entry - 60, 'entry_at': entry, 'expiry_at': entry + 300,
         'state': 'encerrado', 'result': result, 'settlement_pending': False,
         'settled_at': entry + 365, 'entry_price': 100, 'expiry_price': price,
         'settlement_source_identity': 'deriv:test', 'settlement_entry_at': entry,
         'settlement_expiry_at': entry + 300, 'telegram': {'status': 'enviado'},
         'settlement_evidence': [Candle(entry,100,101,99,100).json(),
                                 Candle(entry+300,price,price+1,price-1,price).json()],
         'evidence': {'rules_version': RULES_VERSION, 'pipeline_version': PIPELINE_VERSION,
                      'regime': 'tendencial', 'configuration': {'id': 'fixed-test-config'},
                      'candles': [Candle(entry-3000,99.2,99.5,99,99.4).json(),
                                  Candle(entry-1200,100.7,101,100.5,100.8).json()]},
         'setup_contract': {'version': '1.26.2-setup-1', 'retracement_ratio': ratio,
                            'anchors': [entry-3000,entry-1200], 'confirmed_at': entry-300,
                            'levels': {'frozen_fibonacci_level': 101-ratio*2}}}
    s.update(changes)
    return s


@pytest.mark.parametrize('ratio', [.5, .618, .786])
def test_fibonacci_chart_levels_are_frozen_original_geometry(ratio):
    original = signal(ratio=ratio)
    before = deepcopy(original)
    visual = annotations(original)
    assert original == before
    assert [round(level['price'],3) for level in visual['levels']] == [100,99.764,99.428]
    selected = [level for level in visual['levels'] if level['selected']]
    assert len(selected) == 1 and selected[0]['price'] == 101-ratio*2
    assert visual['entry_at'] == original['entry_at'] and visual['expiry_at'] == original['expiry_at']
    assert visual['entry_price_inferred'] is False


def test_missing_or_inconsistent_fibonacci_anchors_never_invent_extra_levels():
    s = signal()
    s['evidence']['candles'] = []
    assert len(annotations(s)['levels']) == 1
    s = signal()
    s['setup_contract']['levels']['frozen_fibonacci_level'] = 500
    assert annotations(s)['levels'] == [{'id':'frozen_fibonacci_level','label':'Fibo 50%',
                                         'price':500,'selected':True}]


def test_downward_fibonacci_and_future_anchor_are_not_confused():
    s=signal(ratio=.618,direction='PUT')
    s['setup_contract']['levels']['frozen_fibonacci_level']=99+.618*2
    assert [round(level['price'],3) for level in annotations(s)['levels']]==[100,100.236,100.572]
    s['setup_contract']['confirmed_at']=BASE-3600
    assert len(annotations(s)['levels'])==1


@pytest.mark.parametrize('changes', [{'source_identity':''}, {'source_key':'unknown'},
                                    {'expiry_at':BASE+301}, {'direction':'unknown'},
                                    {'entry_at':True}, {'duration_seconds':60}])
def test_invalid_signal_identity_or_times_do_not_get_chart_annotations(changes):
    assert annotations(signal(**changes)) is None


def test_bad_legacy_contract_is_ignored_without_breaking_snapshot():
    s = signal()
    s['setup_contract'] = {'anchors': 12, 'levels': 'legacy', 'geometry': 'legacy'}
    s['evidence'] = 'legacy'
    assert annotations(s)['levels'] == []


def test_fibonacci_levels_and_contexts_are_separate_and_rates_require_sample():
    store = Store(':memory:')
    try:
        for i in range(30):
            store.add_signal(signal(i,result='WIN' if i<20 else 'LOSS'),str(i))
        store.add_signal(signal(30,ratio=.618),'30')
        store.add_signal(signal(31,asset='EUR/USD'),'31')
        s = signal(32);s['evidence']['configuration']['id']='other-configuration'
        store.add_signal(s,'32')
        result = report(store,BASE+100000)
        assert result['total'] == 4
        known = next(item for item in result['items'] if item['sample']==30)
        assert known['rate_pct'] == 66.67 and known['losses'] == 10
        assert all(item['rate_pct'] is None for item in result['items'] if item['sample']<30)
        filtered = report(store,BASE+100000,ratio='61.8')
        assert filtered['total'] == 1 and filtered['items'][0]['sample']==1
        assert result['operational_rules_changed'] is True and result['automatic_promotion'] is False
    finally:store.close()


def test_future_outcome_and_unproven_quote_are_excluded_from_rates():
    store = Store(':memory:')
    try:
        s = signal();store.add_signal(s,'future')
        result = report(store,BASE+360)['items'][0]
        assert result['sample']==0 and result['pending']==1
        bad = signal(1,expiry_price=102)
        store.add_signal(bad,'bad-proof')
        result = report(store,BASE+10000)['items'][0]
        assert result['sample']==1 and result['unverified']==1
    finally:store.close()


def test_unknown_level_is_not_inferred_and_read_reports_do_not_mutate_origins():
    store = Store(':memory:')
    try:
        s = signal();s['setup_contract'].pop('retracement_ratio');s['reasons']=['50%']
        store.add_signal(s,'legacy')
        before = store.db.execute('SELECT payload FROM signal_origins').fetchone()[0]
        result = report(store,BASE+10000,ratio='unknown')
        assert result['total']==1 and result['items'][0]['retracement_ratio'] is None
        assert report(store,BASE+10000,ratio='50')['total']==0
        assert store.db.execute('SELECT payload FROM signal_origins').fetchone()[0] == before
    finally:store.close()


def test_review_is_paged_and_validates_filters():
    store = Store(':memory:')
    try:
        store.add_signal(signal(),'0');store.add_signal(signal(1,asset='EUR/USD'),'1')
        a=report(store,BASE+10000,page_size=1);b=report(store,BASE+10000,page=2,page_size=1)
        assert a['total']==2 and a['items'][0]['group_id']!=b['items'][0]['group_id']
        assert report(store,BASE+10000,page=999,page_size=1)['page']==2
        assert report(store,BASE+10000,source='binance')['total']==0
        with pytest.raises(ValueError):report(store,BASE+10000,ratio="50' OR 1=1")
    finally:store.close()


def test_large_legacy_sample_without_context_or_level_does_not_get_a_rate():
    for missing in ('configuration','level'):
        store=Store(':memory:')
        try:
            for i in range(30):
                s=signal(i)
                if missing=='configuration':s['evidence']['configuration']={}
                else:s['setup_contract'].pop('retracement_ratio')
                store.add_signal(s,str(i))
            item=report(store,BASE+100000)['items'][0]
            assert item['sample']==30 and item['rate_pct'] is None
            assert item['evidence_status']=='incomplete_context'
        finally:store.close()


def test_review_api_is_read_only_versioned_and_does_not_start_scanning(tmp_path):
    app=create_app(Config(database=':memory:'),Feed(),TG(),credentials_path=tmp_path/'.env')
    with TestClient(app) as client:
        before=client.get('/api/state').json()
        assert client.get('/api/strategy-review').status_code==200
        assert client.get('/api/strategy-review?ratio=wrong').status_code==422
        assert client.get('/api/strategy-review?page_size=200').status_code==422
        after=client.get('/api/state').json()
        from app.version import VERSION
        assert after['version']==VERSION
        assert not after['scan']['active'] and after['analyses']==[]
        assert before['settings']==after['settings']
        assert json.dumps(before['signals'])==json.dumps(after['signals'])


def test_current_signal_annotations_keep_original_anchors_without_exposing_candles(tmp_path):
    app=create_app(Config(database=':memory:'),Feed(),TG(),credentials_path=tmp_path/'.env')
    with TestClient(app) as client:
        s=signal(state='aguardando entrada',settlement_pending=True)
        app.state.service.store.add_signal(s,'original-anchors')
        payload=client.get('/api/state').json()
        assert 'evidence' not in payload['current_signal']
        assert len(payload['current_signal']['chart_annotations']['levels'])==3
        assert payload['current_signal']['chart_annotations']==payload['signals'][0]['chart_annotations']
