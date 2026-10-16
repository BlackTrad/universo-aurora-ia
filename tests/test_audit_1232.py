"""Audit regressions. No external provider or recipient is contacted."""
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.store import Store
from app.telegram import Telegram
from app.deriv import DerivProvider
from app.market import DataError
from tests.test_system import Feed, TG
from scripts import start


@pytest.mark.parametrize('source', ['binance', 'coinbase', 'deriv', 'twelvedata'])
def test_history_accepts_current_and_legacy_sources(source):
    with TestClient(create_app(Config(database=':memory:'), Feed(), TG())) as client:
        assert client.get('/api/history', params={'source':source}).status_code == 200


def test_history_snapshot_excludes_insertions_between_pages():
    store = Store(':memory:')
    for i in range(6):
        store.add_signal(dict(id=str(i), created_at=i, entry_at=600+i), str(i))
    first = store.history(page_size=3)
    store.add_signal(dict(id='new', created_at=10, entry_at=700), 'new')
    second = store.history(page=2, page_size=3, snapshot_id=first['snapshot_id'])
    ids = [s['id'] for s in first['items']+second['items']]
    assert ids == ['5','4','3','2','1','0'] and second['total'] == 6
    store.db.close()


def test_telegram_requires_http_success_and_strict_confirmation():
    async def run():
        for status, body in [(500, {'ok':True,'result':{'message_id':1}}),
                             (200, {'ok':'true','result':{'message_id':1}}),
                             (200, {'ok':True,'result':{'message_id':True}})]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body))) as client:
                result = await Telegram(Config(telegram_token='123:TEST', telegram_chat='123'),client).send_result(
                    dict(result='WIN',asset='EUR/USD',direction='CALL',entry_at=600,duration_seconds=60),
                    dict(wins=1,losses=0,ties=0,win_rate_pct=100))
                assert result['status'] != 'enviado'
    asyncio.run(run())


def test_current_deriv_error_envelope_sets_rate_limit():
    async def run():
        ws=MagicMock()
        async def send(payload): pass
        async def recv():return '{"errors":[{"code":"RateLimit","message":"private external text"}]}'
        ws.send=send;ws.recv=recv
        connection=MagicMock()
        connection.__aenter__=AsyncMock(return_value=ws);connection.__aexit__=AsyncMock(return_value=None)
        with patch('app.deriv.connect',return_value=connection):
            with pytest.raises(DataError) as caught:
                await DerivProvider(Config()).request({'active_symbols':'brief'}, 'active_symbols')
        assert caught.value.code == 'rate_limit'
        assert 'private external text' not in str(caught.value)
    asyncio.run(run())


@pytest.mark.parametrize('state',['cancelado','encerrado'])
def test_restart_recovers_send_status_even_outside_active_signals(tmp_path,state):
    path=str(tmp_path/'history.db')
    store=Store(path)
    store.add_signal(dict(id='saved',created_at=1,entry_at=600,expiry_at=660,state=state,
                          result='WIN',settlement_pending=False,telegram={'status':'enviando'}),'saved')
    store.db.close()
    store=Store(path);store.recover(1000)
    saved=store.signal('saved')
    assert saved['telegram']['status']=='incerto' and saved['result']=='WIN'
    assert store.events('saved')[-1]['kind']=='telegram'
    assert not store.pending_result_messages()
    store.db.close()


def test_cached_launcher_probes_missing_packages():
    with patch.object(start.subprocess,'run',return_value=MagicMock(returncode=1)) as probe:
        assert not start.dependencies_available('python')
        assert 'websockets.asyncio.client' in probe.call_args.args[0][-1]


def test_metrics_do_not_count_unresolved_or_inconsistent_outcomes():
    store=Store(':memory:')
    for i,(state,pending,result) in enumerate([
        ('encerrado',False,'WIN'),('aguardando entrada',False,'WIN'),
        ('encerrado',True,'WIN'),('encerrado',False,None)]):
        store.add_signal(dict(id=str(i),created_at=i,state=state,settlement_pending=pending,result=result),str(i))
    group=store.apex_metrics()[0]
    assert group['wins']==1 and group['pending']==2 and group['unavailable']==1
    assert store.result_totals()['wins']==1
    store.db.close()


def test_stop_during_fetch_prevents_any_new_signal(tmp_path):
    from tests.test_system import setup
    async def run():
        service,clock=setup(tmp_path)
        entered=asyncio.Event()
        async def blocked(*args):
            entered.set();await asyncio.Event().wait()
        service.provider.candles=blocked
        assert service.start_scan(['EUR/USD'],60,'M1')
        assert service.start_scan(['EUR/USD'],60,'M1') is None
        await entered.wait()
        await service.stop_scan()
        await service.tick()
        assert not service.scan['active'] and not service.analysis_active
        assert not service.store.signals() and not service.store.busy(clock[0])
    asyncio.run(run())
