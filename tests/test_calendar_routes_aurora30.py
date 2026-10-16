"""Regression tests for the six-page dashboard and full-day indicative results."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from tests.test_system import Feed, TG


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_new_routes_serve_frontend_and_are_local_only(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path / 'db')), Feed(), TG())) as client:
        for path in ('/sinais', '/calendario', '/telegram', '/universo-ia'):
            response = client.get(path)
            assert response.status_code == 200
            assert '/static/assets/' in response.text
        assert client.get('/api/nonexistent').status_code == 404


def test_daily_summary_uses_brasilia_boundaries_and_all_rows(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path / 'db')), Feed(), TG())) as client:
        store=client.app.state.service.store
        midnight=utc('2026-10-14T03:00:00')
        for index in range(66):
            entry=midnight+index*60
            record={'id':f'signal-{index}', 'created_at':entry-100, 'entry_at':entry,
                    'expiry_at':entry+60, 'asset':'EUR/USD', 'direction':'CALL',
                    'state':'encerrado', 'settlement_pending':False,
                    'result':'WIN' if index<35 else 'LOSS'}
            assert store.add_signal(record,record['id'])
        assert store.add_signal({'id':'before','created_at':midnight-150,
                                 'entry_at':midnight-1,'state':'encerrado',
                                 'result':'WIN'},'before')
        assert store.add_signal({'id':'pending','created_at':midnight+200,
                                 'entry_at':midnight+200,'state':'em andamento'},'pending')
        summary=client.get('/api/history/daily-summary?day=2026-10-14')
        assert summary.status_code == 200
        value=summary.json()
        assert value['total']==67
        assert (value['wins'],value['losses'],value['pending']) == (35,31,1)
        assert value['win_rate_pct']==round(35*100/66,2)
        assert client.get('/api/history?start_date=2026-10-14&end_date=2026-10-14').json()['total']==67
        assert client.get('/api/history/daily-summary?day=2026-10-13').json()['total']==1
        assert client.get('/api/history/daily-summary?day=bad').status_code==422


def test_risk_guard_rejects_nan_infinite_and_malformed_confidence():
    from backend.ai.risk_guard import RiskGuard
    guard=RiskGuard()
    good={'ativo':'BTC/USD','mercado':'OPCOES_BINARIAS','direcao':'CALL',
          'confianca':80,'payout':0.90}
    assert guard.should_allow(good)
    for invalid in (float('nan'),float('inf'),float('-inf'),True,'invalid'):
        assert not guard.should_allow({**good,'confianca':invalid})
    for invalid in (float('nan'),float('inf'),float('-inf')):
        assert not guard.should_allow({**good,'payout':invalid})
