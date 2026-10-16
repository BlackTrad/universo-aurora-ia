"""Security regression tests; no external market or provider access."""
from unittest.mock import patch
import os

from fastapi.testclient import TestClient
import pytest

from app.public_auth import make_password_hash, PublicAuth
from app.container_start import settings
from app.config import Config
from app.main import create_app
from tests.test_system import Feed, TG

ORIGIN='https://aurora.example.org'
PASSWORD='correct-horse-battery-9493'


def configured():
    return {'AURORA_PUBLIC_MODE':'true','AURORA_PUBLIC_ORIGIN':ORIGIN,
            'AURORA_ADMIN_PASSWORD_HASH':make_password_hash(PASSWORD,salt=b'0123456789abcdef'),
            'AURORA_SESSION_SECRET':'s3cure_'+('0123456789abcdef' * 5)}


def test_missing_secrets_never_enable_public_mode():
    with patch.dict(os.environ,{'AURORA_PUBLIC_MODE':'true'},clear=True):
        with pytest.raises(RuntimeError):
            PublicAuth.from_env()
        with pytest.raises(RuntimeError):
            settings()


def test_invalid_origin_and_secret_fail_closed():
    for key,value in [('AURORA_PUBLIC_ORIGIN','http://aurora.example.org'),
                      ('AURORA_PUBLIC_ORIGIN','https://aurora.example.org/secret'),
                      ('AURORA_SESSION_SECRET','weak')]:
        bad=configured();bad[key]=value
        with patch.dict(os.environ,bad,clear=True):
            with pytest.raises(RuntimeError):PublicAuth.from_env()


def test_strict_login_cookie_logout_and_host(tmp_path):
    with patch.dict(os.environ,configured(),clear=True):
        app=create_app(Config(database=str(tmp_path/'db')),Feed(),TG())
        with TestClient(app,base_url=ORIGIN) as client:
            assert client.get('/healthz').json()=={'status':'ok'}
            assert client.get('/api/state').status_code==401
            assert client.get('/sinais',follow_redirects=False).headers['location']=='/login'
            assert client.get('/login').status_code==200
            assert 'type="password"' in client.get('/login').text
            assert client.get('/static/login.css').status_code==200
            assert client.get('/api/state',headers={'Host':'evil.example.org'}).status_code==403
            # Missing Origin and cross-site Origin cannot authenticate.
            assert client.post('/api/auth/login',data={'password':PASSWORD},follow_redirects=False).status_code==403
            assert client.post('/api/auth/login',data={'password':PASSWORD},
                headers={'Origin':'https://evil.example.org'},follow_redirects=False).status_code==403
            invalid=client.post('/api/auth/login',data={'password':'wrongwrongwrong'},
                headers={'Origin':ORIGIN},follow_redirects=False)
            assert invalid.status_code==303 and 'Set-Cookie' not in invalid.headers
            result=client.post('/api/auth/login',data={'password':PASSWORD},
                headers={'Origin':ORIGIN},follow_redirects=False)
            assert result.status_code==303
            assert 'Secure' in result.headers['set-cookie']
            assert 'HttpOnly' in result.headers['set-cookie']
            assert 'SameSite=strict' in result.headers['set-cookie']
            assert client.get('/api/state').status_code==200
            assert client.get('/api/auth/status').json()=={'public_access':True,'authenticated':True,'user':{'id':'admin','name':'Administrador','role':'admin'}}
            forbidden = client.put('/api/credentials', json={'telegram_chat':'@public'},headers={'Origin':ORIGIN})
            assert forbidden.status_code == 403
            assert 'variáveis privadas' in forbidden.json()['detail']
            assert client.post('/api/auth/logout',headers={'Origin':'https://evil.example.org'},follow_redirects=False).status_code==403
            assert client.post('/api/auth/logout',headers={'Origin':ORIGIN},follow_redirects=False).status_code==303
            assert client.get('/api/state').status_code==401


def test_signature_and_expiry():
    with patch.dict(os.environ,configured(),clear=True):
        auth=PublicAuth.from_env()
        token=auth.new_cookie(now=1_000_000_000)
        assert auth.valid_cookie(token,now=1_000_000_001)
        assert not auth.valid_cookie(token+'x',now=1_000_000_001)
        assert not auth.valid_cookie(token,now=1_000_000_000+14_400)
        assert not auth.valid_cookie(token,now=1_000_000_000-200)


def test_container_binding_is_local_unless_public():
    with patch.dict(os.environ,{'AURORA_PUBLIC_MODE':'false','PORT':'8100'},clear=True):
        assert settings()==('127.0.0.1',8100)
    with patch.dict(os.environ,{**configured(),'PORT':'8000'},clear=True):
        assert settings()==('0.0.0.0',8000)
