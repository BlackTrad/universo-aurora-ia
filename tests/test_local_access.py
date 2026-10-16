import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from tests.test_system import Feed,TG

def test_no_token_needed_even_if_old_env_contains_short_token(tmp_path):
    import app.config as module
    (tmp_path/'.env').write_text('ADMIN_TOKEN=x\n')
    with patch.dict(os.environ,{},clear=True),patch.object(module,'ROOT',tmp_path):
        cfg=Config.load()
        assert cfg.admin_token==''
    cfg.database=str(tmp_path/'db')
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.get('/api/state').status_code==200
        assert client.get('/api/history').status_code==200
        assert client.get('/api/credentials').status_code==200
        assert 'id="login"' not in client.get('/').text
        assert client.post('/api/analyze',json={'provider':'deriv','assets':['BTC/USD']}).status_code==202

def test_origin_and_host_protections_remain(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as client:
        assert client.put('/api/credentials',json={'api_key':'x'},headers={'Origin':'https://foreign.invalid'}).status_code==403
        assert client.get('/api/state',headers={'Host':'foreign.invalid'}).status_code==403
