"""Controlled regressions: no real messages, API keys or market orders."""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app.config import Config
from app.market import Candle
from app.store import Store
from app.telegram import Telegram, caption
from tests.test_system import setup, drain
from tests.test_telegram_compact import signal


def totals():
    return {'wins': 1, 'losses': 0, 'ties': 0, 'win_rate_pct': 100}


def closed_signal(**changes):
    return {**signal(), 'state': 'encerrado', 'result': 'WIN',
            'settlement_pending': False, 'telegram': {'status': 'enviado', 'message_id': 123}, **changes}


def test_result_429_returns_retry_instead_of_permanent_failure():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
                429, json={'ok': False, 'error_code': 429, 'parameters': {'retry_after': 17}}))) as client:
            result = await Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client).send_result(closed_signal(), totals())
            assert result['status'] == 'repetir' and result['retry_after'] == 17
    asyncio.run(run())


def test_result_is_a_reply_to_the_original_photo():
    async def run():
        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': 456}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client).send_result(closed_signal(), totals())
        assert result['status'] == 'enviado'
        assert sent[0]['reply_parameters'] == {'message_id': 123, 'allow_sending_without_reply': True}
        assert 'RESULTADO: WIN' in sent[0]['text'] and '🏆Vitórias: 1 · Derrotas: 0' in sent[0]['text']
        assert 'sem confirmação da corretora' not in sent[0]['text']
    asyncio.run(run())


def test_result_retry_survives_restart_and_respects_retry_at(tmp_path):
    async def run():
        service, now = setup(tmp_path)
        s = closed_signal(id='retry', created_at=now[0], telegram_result={'status': 'pendente'})
        service.store.add_signal(s, 'retry')
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(429, json={'ok': False, 'error_code': 429, 'parameters': {'retry_after': 17}}) if len(requests) == 1 else httpx.Response(200, json={'ok': True, 'result': {'message_id': 456}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.telegram = Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client)
            await service.deliver_result(s['id'])
            saved = service.store.signal(s['id'])
            assert saved['telegram_result']['status'] == 'repetir'
            assert saved['telegram_result']['attempt'] == 1
            service.store.db.close()
            service.store = Store(service.config.database)
            service.store.recover(now[0])
            service.telegram = Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client)
            now[0] += 16
            await service.tick(); await drain(service)
            assert len(requests) == 1
            now[0] += 1
            await asyncio.gather(service.deliver_result(s['id']), service.deliver_result(s['id']))
            await service.tick(); await drain(service)
        assert len(requests) == 2
        assert service.store.signal(s['id'])['telegram_result']['status'] == 'enviado'
        assert service.store.signal(s['id'])['telegram_result']['attempt'] == 2
        service.store.db.close()
    asyncio.run(run())


def test_settlement_notifies_when_original_photo_delivery_was_uncertain(tmp_path):
    async def run():
        service, now = setup(tmp_path)
        service.start(['EUR/USD'], 60); await drain(service)
        s = service.store.signals()[0]
        service.store.patch(s['id'], {'telegram': {'status': 'incerto'}})
        now[0] = s['expiry_at'] + 65
        service.provider.settlement = [Candle(s['entry_at'], 100, 102, 99, 101), Candle(s['expiry_at'], 101, 102, 100, 101)]
        messages = []
        async def send_result(saved, summary):
            messages.append(saved); return {'status': 'enviado', 'message_id': 456}
        service.telegram.send_result = send_result
        await service.settle(s); await drain(service)
        assert len(messages) == 1 and messages[0]['result'] == 'WIN'
        assert service.store.signal(s['id'])['telegram']['status'] == 'incerto'
        service.store.db.close()
    asyncio.run(run())


def test_late_delivery_acknowledgement_does_not_lose_the_result(tmp_path):
    async def run():
        service, now = setup(tmp_path)
        service.start(['EUR/USD'], 60); await drain(service)
        s = service.store.signals()[0]
        service.store.patch(s['id'], {'telegram': {'status': 'pendente'}})
        messages = []
        async def send(saved):
            service.store.patch(saved['id'], {'state': 'encerrado', 'result': 'WIN', 'settlement_pending': False})
            return {'status': 'enviado', 'message_id': 123}
        async def send_result(saved, summary):
            messages.append(saved); return {'status': 'enviado', 'message_id': 456}
        service.telegram.send = send; service.telegram.send_result = send_result
        await service.deliver(s['id']); await service.tick(); await drain(service)
        assert len(messages) == 1
        assert service.store.signal(s['id'])['telegram_result']['status'] == 'enviado'
        service.store.db.close()
    asyncio.run(run())


def test_incomplete_result_cannot_be_claimed_or_posted():
    store = Store(':memory:')
    s = closed_signal(id='pending', created_at=1, settlement_pending=True, telegram_result={'status': 'pendente'})
    store.add_signal(s, 'pending')
    assert not store.claim_result_message('pending', 100)
    store.db.close()






def test_caption_uses_original_engine_reasons_and_ignores_historical_ai():
    s = signal(); s['spectro_ai'] = {'status': 'indisponível', 'code': 'quota', 'detail': 'Limite do Gemini atingido.'}
    message = caption(s)
    assert '📋 Motor:' not in message and 'rejeição do suporte' not in message
    assert '💱Ativo:' in message
    assert 'Gemini' not in message and 'Spectro Ai' not in message


def test_result_stops_after_three_explicit_rejections(tmp_path):
    async def run():
        service, now = setup(tmp_path)
        s = closed_signal(id='rejected', created_at=now[0], telegram_result={'status': 'pendente'})
        service.store.add_signal(s, 'rejected'); calls = []
        async def send_result(saved, summary):
            calls.append(saved); return {'status': 'repetir', 'retry_after': 5}
        service.telegram.send_result = send_result
        for _ in range(4):
            await service.deliver_result(s['id']); now[0] += 5
        assert len(calls) == 3
        saved = service.store.signal(s['id'])
        assert saved['result'] == 'WIN' and saved['telegram_result']['status'] == 'falha'
        assert saved['telegram_result']['code'] == 'retry_limit'
        service.store.db.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['timeout', 'http_500', 'invalid_json'])
def test_uncertain_result_is_not_retried_on_tick_or_restart(tmp_path, failure):
    async def run():
        service, now = setup(tmp_path)
        s = closed_signal(id='uncertain', created_at=now[0], telegram_result={'status': 'pendente'})
        service.store.add_signal(s, 'uncertain'); calls = []
        def handle(request):
            calls.append(request)
            if failure == 'timeout': raise httpx.ReadTimeout('TEST_ONLY_PRIVATE')
            return httpx.Response(500, json={'ok': False}) if failure == 'http_500' else httpx.Response(200, text='NOT JSON')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.telegram = Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client)
            await service.deliver_result(s['id']); await service.tick(); await drain(service)
            service.store.recover(now[0]); now[0] += 120
            await service.deliver_result(s['id']); await service.tick(); await drain(service)
        assert len(calls) == 1
        assert service.store.signal(s['id'])['telegram_result']['status'] == 'incerto'
        service.store.db.close()
    asyncio.run(run())


def test_changed_destination_cannot_receive_or_reply_to_another_chats_result():
    async def run():
        requests = []
        cfg = Config(telegram_token='123:TEST', telegram_chat='1')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
            tg = Telegram(cfg, client); s = closed_signal()
            s['telegram']['destination_key'] = tg.destination_key()
            cfg.telegram_chat = '2'
            result = await tg.send_result(s, totals())
        assert result['status'] == 'falha' and result['code'] == 'destination_changed'
        assert not requests and 'TEST' not in json.dumps(result)
    asyncio.run(run())


def test_shared_telegram_queue_spaces_simultaneous_results_and_keeps_replies():
    async def run():
        import time
        requests = []
        async def handle(request):
            requests.append((time.monotonic(), json.loads(request.content)))
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(requests) + 500}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            tg = Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client)
            a = closed_signal(); b = closed_signal(asset='GBP/USD', result='LOSS', telegram={'status': 'enviado', 'message_id': 124})
            results = await asyncio.gather(tg.send_result(a, totals()), tg.send_result(b, totals()))
        assert all(r['status'] == 'enviado' for r in results)
        assert requests[1][0] - requests[0][0] >= 1
        assert {r[1]['reply_parameters']['message_id'] for r in requests} == {123, 124}
        assert any('RESULTADO: LOSS' in r[1]['text'] for r in requests)
    asyncio.run(run())


def test_signal_deadline_is_checked_after_waiting_for_telegram_queue():
    async def run():
        import time
        requests = []; s = signal()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
            tg = Telegram(Config(telegram_token='123:TEST', telegram_chat='1'), client)
            tg._last_post = time.monotonic()
            with patch('app.telegram.time.time', side_effect=[s['entry_at']-30, s['entry_at']-13, s['entry_at']-11]):
                result = await tg.send(s)
        assert result['status'] == 'expirado' and not requests
    asyncio.run(run())




def test_atomic_result_queue_survives_restart_without_backfilling_history(tmp_path):
    path = str(tmp_path/'outbox.db'); store = Store(path)
    s = closed_signal(id='atomic', created_at=1, telegram={'status':'incerto'})
    store.add_signal(s, 'atomic')
    store.patch('atomic', {'result':'WIN'}, 'apuração', 100, prepare_result=True)
    legacy = closed_signal(id='legacy', created_at=0)
    store.add_signal(legacy, 'legacy'); store.db.close()
    store = Store(path); store.recover(110)
    assert [s['id'] for s in store.pending_result_messages(110)] == ['atomic']
    assert 'telegram_result' not in store.signal('legacy')
    original = json.loads(store.db.execute("SELECT payload FROM signal_origins WHERE id='atomic'").fetchone()[0])
    assert 'telegram_result' not in original
    store.db.close()


def test_restart_preserves_original_destination_and_attempt_metadata():
    async def run():
        store = Store(':memory:'); requests = []
        cfg = Config(telegram_token='123:TEST', telegram_chat='1')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
            tg = Telegram(cfg, client); destination = tg.destination_key()
            store.add_signal(closed_signal(id='restart-destination', created_at=1,
                telegram={'status':'enviando', 'attempt':2, 'destination_key':destination}), 'restart-destination')
            store.recover(100)
            saved = store.signal('restart-destination')
            assert saved['telegram']['status'] == 'incerto'
            assert saved['telegram']['destination_key'] == destination and saved['telegram']['attempt'] == 2
            cfg.telegram_chat = '2'
            result = await tg.send_result(saved, totals())
        assert result['status'] == 'falha' and result['code'] == 'destination_changed'
        assert not requests
        store.db.close()
    asyncio.run(run())
