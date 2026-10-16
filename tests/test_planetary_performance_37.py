"""Version 3.7: only recordable evidence may enter planetary reports."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Config
from app.store import Store
from app.planetary_performance import summarize_planetary
from tests.test_system import Feed, TG


def settlement(decision_id='d1', *, known_at=600, created_at=120, entry_at=180):
    end = entry_at + 300
    return {'id': 's-' + decision_id, 'decision_id': decision_id,
            'created_at': created_at, 'entry_at': entry_at, 'expiry_at': end,
            'settled_at': known_at, 'duration_seconds': 300,
            'direction': 'CALL', 'result': 'WIN', 'state': 'encerrado',
            'settlement_pending': False, 'source_identity': 'deriv-binary-feed',
            'settlement_source_identity': 'deriv-binary-feed',
            'settlement_entry_at': entry_at, 'settlement_expiry_at': end,
            'entry_price': 100, 'expiry_price': 102,
            'settlement_evidence': [
                {'t': entry_at, 'o': 100, 'h': 101, 'l': 99, 'c': 100.5},
                {'t': end, 'o': 102, 'h': 103, 'l': 101, 'c': 102.5}],
            'broker_confirmed': False}


def decision(did='d1', *, at=120, selection=True):
    return {'id': did, 'created_at': at, 'asset': 'BTC/USD',
            'configuration': {'strategy': 'scan_all'},
            'selection': {'selected': {'direction': 'CALL', 'strategy_resolved': 'fibonacci'}
                          if selection else None}}


def ai_event(*, at=121, approved=True, status='disponivel', direction='CALL', duplicate=False):
    vote = {'agente': 'Groq', 'status': status, 'direcao': direction,
            'confianca': 80, 'latency_ms': 120.0,
            'dados_insuficientes': status != 'disponivel'}
    return {'at': at, 'event': 'ai_consensus', 'approved': approved,
            'decision': {'direcao': 'CALL' if approved else None,
                         'motivo': 'maioria_call' if approved else 'empate_entre_call_put'},
            'votes': [vote, vote] if duplicate else [vote]}


def test_empty_report_has_no_fabricated_performance():
    x = summarize_planetary([], [], now=700)
    assert x['ai_reviewed'] == 0 and x['published_with_price_proof'] == 0
    assert x['agents'] == [] and x['holdout']['holdout_published']['observed_win_pct'] is None
    assert x['holdout']['comparison_status'] == 'no_causal_motor_vs_ai_uplift_estimate'


def test_verified_direction_counts_hypothetical_once_per_agent():
    x = summarize_planetary([{'decision': decision(), 'events': [ai_event(duplicate=True)]}],
                            [settlement()], now=700)
    assert x['technical_candidates'] == 1 and x['ai_reviewed'] == 1
    assert x['published_with_price_proof'] == 1
    assert x['agents'][0]['calls'] == 1
    assert x['agents'][0]['hypothetical_direction_match_pct'] == 100
    assert x['agents'][0]['latency_median_ms'] == 120
    assert x['agents'][0]['latency_p95_ms'] == 120


def test_failed_agent_is_not_successful_vote():
    x = summarize_planetary([{'decision': decision(), 'events': [ai_event(status='timeout')]}],
                            [settlement()], now=700)
    assert x['agents'][0]['available'] == 0 and x['agents'][0]['timeouts'] == 1
    assert x['agents'][0]['hypothetical_direction_match_pct'] is None


def test_rejected_decision_does_not_receive_imputed_result():
    x = summarize_planetary([{'decision': decision(), 'events': [ai_event(approved=False)]}],
                            [], now=700)
    assert x['ai_rejected'] == 1 and x['published_with_price_proof'] == 0
    assert x['ai_rejection_reasons'] == [{'code': 'empate_entre_call_put', 'count': 1}]
    assert x['agents'][0]['verified_hypothetical_votes'] == 0


def test_settlement_from_future_is_not_used():
    x = summarize_planetary([{'decision': decision(), 'events': [ai_event()]}],
                            [settlement(known_at=900)], now=700)
    assert x['published_without_price_proof'] == 1
    assert x['published_with_price_proof'] == 0
    assert x['agents'][0]['verified_hypothetical_votes'] == 0


def test_future_events_are_not_included_and_sampling_marked():
    x = summarize_planetary([{'decision': decision(), 'events': [ai_event(at=800)]}],
                            [], now=700, source_count=15)
    assert x['ai_reviewed'] == 0 and x['sample_truncated']
    assert x['source_decision_count'] == 15


def test_holdout_does_not_read_training_labels_settled_after_boundary():
    items=[]; signals=[]
    for i in range(10):
        created = 120 + i * 1200
        entry = created + 60
        settled = entry + 420
        if i == 1:  # This label would be future information at the split.
            settled = 120 + 9 * 1200 + 1000
        did=f'd{i}'
        items.append({'decision': decision(did, at=created), 'events': [ai_event(at=created+1)]})
        signals.append(settlement(did, known_at=settled, created_at=created, entry_at=entry))
    x=summarize_planetary(items, signals, now=20000)
    assert x['holdout']['causally_available_train']['n'] == 6
    assert x['holdout']['holdout_published']['n'] == 3
    assert x['holdout']['comparison_status'] == 'no_causal_motor_vs_ai_uplift_estimate'


def test_store_persists_and_serves_report_without_mutating_decisions(tmp_path):
    store=Store(str(tmp_path/'aurora.db'))
    try:
        store.record_decision(decision())
        store.decision_event('d1', 121, {k:v for k,v in ai_event().items() if k != 'at'})
        s=settlement()
        with store.lock, store.db:
            store.db.execute('INSERT INTO signals VALUES(?,?,?,?)',
                             (s['id'], 'unique-fingerprint', s['created_at'], json.dumps(s)))
        before=store.db.total_changes
        result=store.planetary_performance(700)
        assert store.db.total_changes == before
        assert result['published_with_price_proof'] == 1
        assert result['strategies'][0]['strategy'] == 'fibonacci'
        assert store.planetary_performance(500)['published_with_price_proof'] == 0
        with pytest.raises(ValueError):store.planetary_performance(700, limit=10001)
    finally:
        store.close()


def test_api_returns_report_not_raw_credentials(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'aurora.db')), Feed(), TG(), clock=lambda: 700)) as client:
        response=client.get('/api/validation/planetary-performance')
        assert response.status_code == 200
        data=response.json()
        assert data['version'].startswith('3.7')
        assert data['ai_reviewed'] == 0
        assert client.get('/api/validation/planetary-performance?limit=10001').status_code == 422


def test_cached_vote_is_not_reported_as_api_latency():
    event=ai_event()
    event['votes'][0]['cache_hit']=True
    event['votes'][0]['latency_ms']=0
    x=summarize_planetary([{'decision': decision(), 'events': [event]}], [], now=700)
    assert x['agents'][0]['cache_hits'] == 1
    assert x['agents'][0]['latency_samples'] == 0
    assert x['agents'][0]['latency_p95_ms'] is None
