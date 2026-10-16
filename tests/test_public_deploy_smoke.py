"""Offline read-only tests for the Railway public deployment smoke checker."""
import httpx
import pytest
from scripts.smoke_public import InvalidTarget, inspect, validate_url


@pytest.mark.parametrize('url', [
    'http://aurora.example.org',
    'http://192.168.0.4:8000',
    'https://user:pass@aurora.example.org',
    'https://aurora.example.org/login',
    'https://aurora.example.org?token=secret',
    'https://aurora.example.org/#fragment',
    ' https://aurora.example.org ',
])
def test_refuses_unsafe_or_noncanonical_public_targets(url):
    with pytest.raises(InvalidTarget):
        validate_url(url)


def test_ci_local_http_requires_explicit_opt_in():
    assert validate_url('https://aurora.example.org/') == 'https://aurora.example.org'
    assert validate_url('http://127.0.0.1:8000', allow_local_http=True) == 'http://127.0.0.1:8000'
    with pytest.raises(InvalidTarget):
        validate_url('http://evil.example.org', allow_local_http=True)


def _transport(request):
    route=request.url.path
    if request.method!='GET':
        raise AssertionError('Smoke checker must never mutate server data')
    if route=='/healthz':
        return httpx.Response(200,json={'status':'ok'})
    if route=='/login':
        return httpx.Response(200,text='<title>AURORA</title><input type="password"/>')
    if route=='/static/login.css':
        return httpx.Response(200,text='main{display:block}')
    if route.startswith('/api/'):
        return httpx.Response(401,headers={'Cache-Control':'no-store'},json={'detail':'login'})
    return httpx.Response(303,headers={'Location':'/login'})


def test_smoke_checks_only_safe_anonymous_routes():
    with httpx.Client(transport=httpx.MockTransport(_transport)) as client:
        assert inspect('https://aurora.example.org',client=client)==[]


def test_smoke_detects_private_api_exposure():
    def exposed(request):
        if request.url.path=='/api/state':
            return httpx.Response(200,json={'secret':'never should be visible'})
        return _transport(request)
    with httpx.Client(transport=httpx.MockTransport(exposed)) as client:
        result=inspect('https://aurora.example.org',client=client)
    assert any('/api/state' in issue and 'HTTP 200' in issue for issue in result)


def test_smoke_detects_bad_redirect_and_liveness():
    def incorrect(request):
        if request.url.path=='/healthz':
            return httpx.Response(200,text='not json')
        if request.url.path=='/sinais':
            return httpx.Response(303,headers={'Location':'/dashboard'})
        return _transport(request)
    with httpx.Client(transport=httpx.MockTransport(incorrect)) as client:
        result=inspect('https://aurora.example.org',client=client)
    assert any('/healthz' in issue for issue in result)
    assert any('/sinais' in issue for issue in result)


def test_smoke_against_real_fastapi_authenticated_public_mode():
    """Use the production route table and middleware, not just mocked responses."""
    import os
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config
    from app.public_auth import make_password_hash

    origin = 'https://aurora.example.org'
    settings = {
        'AURORA_PUBLIC_MODE': 'true',
        'AURORA_PUBLIC_ORIGIN': origin,
        'AURORA_ADMIN_PASSWORD_HASH': make_password_hash(
            'smoke-test-only-password', salt=b'0123456789abcdef'),
        'AURORA_SESSION_SECRET': 'testing-' + '0123456789abcdef' * 5,
    }
    with patch.dict(os.environ, settings, clear=True):
        with TestClient(create_app(Config(database=':memory:')), base_url=origin,
                        follow_redirects=False) as client:
            assert inspect(origin, client=client) == []


def test_smoke_detects_exposure_of_history_or_credentials():
    for sensitive in ('/api/history', '/api/credentials', '/api/diagnostics/logs', '/api/news', '/api/validation/planetary-performance'):
        def exposed(request):
            if request.url.path == sensitive:
                return httpx.Response(200, json={'private': True})
            return _transport(request)
        with httpx.Client(transport=httpx.MockTransport(exposed)) as client:
            problems = inspect('https://aurora.example.org', client=client)
        assert any(sensitive in issue and 'HTTP 200' in issue for issue in problems)
