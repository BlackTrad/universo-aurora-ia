"""Local regression fixtures; no provider, AI or Telegram service is contacted."""
import asyncio
import copy
import json

import httpx
import pytest

from app.config import Config
from app.explanations import build_explanation, technical_summary, text_units
from app.store import Store
from app.telegram import Telegram, caption
from tests.test_system import setup, drain
from tests.test_telegram_compact import signal


def test_original_signal_already_contains_engine_explanation_without_telegram(tmp_path):
    async def run():
        service, _ = setup(tmp_path)
        service.telegram.configured = False
        async def offline(s):
            assert s.get('engine_explanation', {}).get('source') == 'motor'
            return {'status': 'não configurado'}
        service.telegram.send = offline
        service.start(['EUR/USD'], 300)
        await drain(service)
        saved = service.store.signals()[0]
        assert saved.get('engine_explanation', {}).get('source') == 'motor'
        original = json.loads(service.store.db.execute(
            'SELECT payload FROM signal_origins WHERE id=?', (saved['id'],)).fetchone()[0])
        assert original['engine_explanation'] == saved['engine_explanation']
        assert 'spectro_ai' not in saved
        service.store.db.close()
    asyncio.run(run())


def test_configured_legacy_gemini_never_receives_a_request():
    async def run():
        requests = []
        def handle(request):
            requests.append(request)
            if request.url.host != 'api.telegram.org':
                return httpx.Response(429, json={'error': 'TEST_ONLY'})
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': 1}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            tg = Telegram(Config(telegram_token='123:test', telegram_chat='1', gemini_api_key='TEST_ONLY'), client)
            assert (await tg.send(signal()))['status'] == 'enviado'
        assert [r.url.host for r in requests] == ['api.telegram.org']
        assert '💱Ativo:' in requests[0].content.decode('utf8', 'ignore')
        assert '📋 Motor:' not in requests[0].content.decode('utf8', 'ignore')
    asyncio.run(run())


def test_retired_model_cannot_prevent_startup():
    Config(gemini_api_key='TEST_ONLY', gemini_model='retired/ignored value').validate()


def test_explanation_copies_only_original_evidence_not_outcomes_or_open_candles():
    s = signal()
    s['strategy_resolved'] = 'ema_pullback'
    s['reasons'] = [' Retração  confirmada ', 'Retração confirmada', None, 'Retomada de alta']
    s['evidence'] = {'rules_version': 'original-rule', 'regime': 'trend_up', 'candles': [{'t': 9999999999}],
                     'decision_trace': {'recorded_at': 123, 'filters': {'htf': 'disabled'},
                                        'mandatory_criteria': [{'criterion': 'Fechamento', 'passed': True}],
                                        'score_composition': {'final_score': 85}}}
    before = copy.deepcopy(s)
    explanation = build_explanation(s)
    assert s == before
    assert explanation['reasons'] == ['Retração confirmada', 'Retomada de alta']
    assert explanation['regime'] == 'trend_up' and explanation['recorded_at'] == 123
    assert explanation['rules_version'] == 'original-rule'
    assert 'candles' not in explanation and 'probability' not in explanation
    s['result'] = 'LOSS'; s['expiry_price'] = 0.001
    assert build_explanation(s) == explanation
    s['evidence']['decision_trace']['filters']['htf'] = 'MODIFIED'
    assert explanation['filters']['htf'] == 'disabled'


@pytest.mark.parametrize('direction', ['CALL', 'PUT'])
@pytest.mark.parametrize('duration', [60, 120, 180, 300])
def test_motor_caption_direction_duration_and_unicode_budget(direction, duration):
    s = signal(); s.update(direction=direction, duration_seconds=duration, score=90,
                           reasons=['Fechamento confirmado 🧠 ' * 200, 'Rejeição da região'])
    s['engine_explanation'] = build_explanation(s)
    message = caption(s)
    assert '📋 Motor:' not in message and '💱Ativo:' in message
    assert ('Compra' if direction == 'CALL' else 'Venda') in message
    assert f'Expiração: {duration//60} ' in message
    assert text_units(message) <= 1024 and '90%' not in message
    assert s['engine_explanation']['score_kind'] == 'technical_setup_score'


@pytest.mark.parametrize('reasons', [[], None, 'untrusted', [None, 90]])
def test_missing_reasons_never_invent_a_setup(reasons):
    s = signal(); s['reasons'] = reasons
    value = build_explanation(s)
    assert 'Motivos técnicos não disponíveis' in value['text']
    assert not value['mandatory_criteria'] and value['regime'] is None
    assert 'rejeição' not in value['text'].lower()


def test_persistent_engine_text_and_historical_ai_are_not_rewritten(tmp_path):
    path = str(tmp_path/'persist.db'); store = Store(path)
    s = signal(); s.update(state='aguardando entrada', created_at=1, source_key='deriv')
    s['engine_explanation'] = build_explanation(s); original = copy.deepcopy(s['engine_explanation'])
    assert store.add_signal(s, 'unique')
    legacy = signal(); legacy.update(id='legacy', created_at=2, state='encerrado', result='LOSS',
                                     spectro_ai={'status':'gerada','text':'ORIGINAL_HISTORICAL_AI'})
    assert store.add_signal(legacy, 'legacy')
    store.patch(s['id'], {'state':'encerrado','result':'WIN','settlement_pending':False}, 'apuração', 3)
    store.db.close(); store = Store(path)
    saved = store.signal(s['id'])
    assert saved['engine_explanation'] == original
    assert technical_summary(saved) == original['text']
    assert store.signal('legacy')['spectro_ai'] == legacy['spectro_ai']
    assert 'engine_explanation' not in store.signal('legacy')
    assert 'ORIGINAL_HISTORICAL_AI' not in caption(store.signal('legacy'))
    assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] >= 1
    store.db.close()


def test_service_explanation_records_closed_candles_without_extra_queries(tmp_path):
    async def run():
        service, _ = setup(tmp_path)
        service.start(['EUR/USD'], 300); await drain(service)
        saved = service.store.signals()[0]
        explanation = saved['engine_explanation']; trace = saved['evidence']['decision_trace']
        assert explanation['direction'] == saved['direction']
        assert explanation['score_composition'] == trace['score_composition']
        assert explanation['recorded_at'] <= saved['emitted_at'] < saved['entry_at']
        assert trace['last_candle_at'] + trace['interval_seconds'] <= explanation['recorded_at']
        assert explanation['duration_seconds'] == 300 and saved['timeframe'] == 'M5'
        assert service.provider.calls == 1
        assert 'gemini' not in service.snapshot()
        assert service.snapshot()['explanation']['source'] == 'motor'
        service.store.db.close()
    asyncio.run(run())
