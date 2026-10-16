import json
import logging
from unittest.mock import patch
from app.logging_setup import configure, event

def test_safe_exception_and_no_duplicate_handlers(tmp_path):
    logger=configure(tmp_path)
    assert configure(tmp_path) is logger and len(logger.handlers)==1
    try:
        raise ValueError('SECRET_TOKEN must never appear')
    except ValueError:
        event('test.failure',level=logging.ERROR,exc_info=True,signal_id='abc',token='SECRET_TOKEN',payload={'secret':'SECRET_TOKEN'})
    value=(tmp_path/'aurora.log').read_text()
    assert 'SECRET_TOKEN' not in value
    row=json.loads(value)
    assert row['signal_id']=='abc' and row['error_type']=='ValueError' and row['frames']
    assert row['at_utc'].endswith('+00:00')

def test_rotation(tmp_path):
    logger=configure(tmp_path);handler=logger.handlers[0];handler.maxBytes=180;handler.backupCount=2
    for _ in range(15):event('rotation.test',status='ok')
    assert len(list(tmp_path.glob('aurora.log*')))==3
    for file in tmp_path.glob('aurora.log*'):
        for line in file.read_text().splitlines():assert json.loads(line)['event']=='rotation.test'

def test_requests_and_analysis_logged_without_credentials(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config
    from tests.test_system import Feed, TG
    cfg=Config(admin_token='secret-admin-'+'x'*32,database=str(tmp_path/'db'))
    with patch('app.main.configure',side_effect=lambda:configure(tmp_path)):
        with TestClient(create_app(cfg,Feed(),TG())) as client:
            response=client.get('/api/state?token=QUERY_SECRET',headers={'Authorization':'Bearer BAD_SECRET'})
            assert response.status_code==200 and response.headers['X-Request-ID']
            headers={'Authorization':'Bearer '+cfg.admin_token}
            assert client.get('/api/state',headers=headers).status_code==200
            response=client.post('/api/analyze',headers=headers,json={'provider':'deriv','assets':['EUR/USD']})
            assert response.status_code==202
    text=(tmp_path/'aurora.log').read_text()
    assert all(secret not in text for secret in [cfg.admin_token,'QUERY_SECRET','BAD_SECRET'])
    events=[json.loads(line)['event'] for line in text.splitlines()]
    assert 'backend.ready' in events and 'http.completed' in events and 'analysis.state' in events and 'backend.stopped' in events


def test_nested_diagnostics_redact_registered_credentials(tmp_path):
    from app.logging_setup import register_secrets
    configure(tmp_path)
    register_secrets('test-secret-unique-xyz')
    event('test.details',reason='Falha test-secret-unique-xyz',candidate={'token':'hidden','reason':'test-secret-unique-xyz'})
    row=json.loads((tmp_path/'aurora.log').read_text())
    assert row['at_brasilia'].endswith('-03:00') and row['project']=='AURORA'
    assert 'test-secret-unique-xyz' not in str(row) and 'hidden' not in str(row)


def test_export_contains_logs_not_env_or_database(tmp_path):
    import io,zipfile
    from app.diagnostics import build_archive
    configure(tmp_path)
    event('test.export',reason='diagnostic')
    (tmp_path/'.env').write_text('PRIVATE')
    (tmp_path/'db.sqlite').write_text('PRIVATE')
    (tmp_path/'untrusted.log').write_text('PRIVATE')
    (tmp_path/'startup.log').write_text('{"event":"startup"}\npartial')
    with zipfile.ZipFile(io.BytesIO(build_archive(tmp_path))) as z:
        assert set(z.namelist())=={'logs/aurora.log','logs/startup.log','diagnostico.json'}
        assert json.loads(z.read('diagnostico.json'))['incomplete_lines_skipped']==1
        assert all(b'PRIVATE' not in z.read(name) for name in z.namelist())


def test_check_results_are_logged_once_with_reason(tmp_path):
    from app.store import Store
    configure(tmp_path)
    store=Store(str(tmp_path/'db'))
    a={'id':'test','created_at':1,'status':'analisando','checks':[{'asset':'EUR/USD','reason':'Mercado fechado','code':'market_closed'}]}
    store.analysis(a);store.analysis(a)
    store.db.close()
    rows=[json.loads(line) for line in (tmp_path/'aurora.log').read_text().splitlines()]
    checks=[r for r in rows if r['event']=='analysis.asset_result']
    assert len(checks)==1 and checks[0]['reason']=='Mercado fechado'


def test_all_engines_report_decisions(tmp_path,monkeypatch):
    from app.strategies import STRATEGIES,scan_all
    configure(tmp_path)
    for key in list(STRATEGIES):
        if key not in ('auto','scan_all'):monkeypatch.setitem(STRATEGIES,key,lambda candles:(None,'sem setup de teste'))
    assert scan_all([])[0] is None
    rows=[json.loads(line) for line in (tmp_path/'aurora.log').read_text().splitlines()]
    assert {r['strategy'] for r in rows if r['event']=='strategy.evaluated'}==set(__import__('app.strategies',fromlist=['ACTIVE_KEYS']).ACTIVE_KEYS)


def test_trace_context_survives_nested_calls_and_is_reset(tmp_path):
    import asyncio
    from app.logging_setup import CONTEXT,traced
    configure(tmp_path)
    @traced('inner')
    async def inner():return {'status':'ok'}
    @traced('outer')
    async def outer():await inner();return {'status':'ok'}
    asyncio.run(outer())
    assert CONTEXT.get()=={}
    rows=[json.loads(line) for line in (tmp_path/'aurora.log').read_text().splitlines()]
    assert rows[0]['request_trace']==rows[-1]['request_trace']
    assert rows[1]['request_trace']==rows[2]['request_trace']!=rows[0]['request_trace']


def test_log_download_and_frontend_error_endpoint(tmp_path):
    import io,zipfile
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config
    from tests.test_system import Feed,TG
    cfg=Config(database=str(tmp_path/'db'))
    (tmp_path/'frontend').mkdir()
    with patch('app.main.configure',side_effect=lambda:configure(tmp_path/'logs')),patch('app.main.ROOT',tmp_path):
        with TestClient(create_app(cfg,Feed(),TG())) as client:
            assert client.post('/api/diagnostics/frontend',json={'kind':'error','line':14,'column':2}).status_code==200
            response=client.get('/api/diagnostics/logs')
            assert response.status_code==200
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                from app.version import VERSION
                assert json.loads(z.read('diagnostico.json'))['snapshot']['version']==VERSION
