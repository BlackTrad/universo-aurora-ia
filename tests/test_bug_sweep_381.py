"""Regressions from the 3.8.0 bug sweep; no external APIs or messages."""
import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
import time

import httpx
import pytest

from backend.ai.providers import AIProviderError, PlanetaryAgent, ProviderCoordinator, NVIDIA_URL
from scripts.start import frontend_fingerprint, frontend_needs_build
from scripts.verify_frontend_build import fingerprint
from unittest.mock import patch
import os

from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from tests.test_public_auth import configured, ORIGIN
from tests.test_system import Feed, TG


def test_concurrent_rate_limits_cannot_shorten_provider_pause():
    async def scenario():
        gates = [asyncio.Event(), asyncio.Event()]
        entered = [asyncio.Event(), asyncio.Event()]
        calls = []

        async def handler(request):
            index = len(calls)
            calls.append(request)
            entered[index].set()
            await gates[index].wait()
            return httpx.Response(429, headers={'Retry-After': '90' if index == 0 else '1'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator = ProviderCoordinator('nvidia', NVIDIA_URL, 'fake-key', client)
            tasks = [asyncio.create_task(coordinator.request('GET', '/models')) for _ in range(2)]
            await asyncio.gather(*(event.wait() for event in entered))
            gates[0].set()
            with pytest.raises(AIProviderError):
                await tasks[0]
            first_deadline = coordinator.cooldown_until
            gates[1].set()
            with pytest.raises(AIProviderError):
                await tasks[1]
            assert coordinator.cooldown_until >= first_deadline
            with pytest.raises(AIProviderError) as error:
                await coordinator.request('GET', '/models')
            assert error.value.status == 'rate_limit' and error.value.retry_after >= 89
            assert len(calls) == 2

    asyncio.run(scenario())


def test_rate_limit_accepts_retry_after_http_date():
    async def scenario():
        deadline = format_datetime(datetime.fromtimestamp(time.time() + 120, timezone.utc), usegmt=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={'Retry-After': deadline}))) as client:
            coordinator = ProviderCoordinator('nvidia', NVIDIA_URL, 'fake-key', client)
            with pytest.raises(AIProviderError) as error:
                await coordinator.request('GET', '/models')
            assert 118 <= error.value.retry_after <= 121

    asyncio.run(scenario())


def test_cancelled_catalogue_clears_previous_ready_status():
    async def scenario():
        entered = asyncio.Event()

        async def handler(request):
            entered.set()
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator = ProviderCoordinator('nvidia', NVIDIA_URL, 'fake-key', client)
            agent = PlanetaryAgent(name='NVIDIA', provider_key='nvidia', model='listed',
                                  role='trend', coordinator=coordinator)
            agent.state = 'disponivel'
            task = asyncio.create_task(agent.analisar({'ativo': 'BTC/USD'}))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert agent.status_snapshot()['status'] == 'timeout'
            assert not coordinator.models_lock.locked()

    asyncio.run(scenario())


def test_unserializable_input_does_not_leave_agent_analyzing():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'data': [{'id': 'listed'}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator = ProviderCoordinator('nvidia', NVIDIA_URL, 'fake-key', client)
            agent = PlanetaryAgent(name='NVIDIA', provider_key='nvidia', model='listed',
                                  role='trend', coordinator=coordinator)
            with pytest.raises(AIProviderError):
                await agent.analisar({'ativo': 'BTC/USD', 'price': float('nan')})
            assert agent.status_snapshot()['status'] == 'erro'
            assert len(calls) == 1 and calls[0].method == 'GET'

    asyncio.run(scenario())


@pytest.mark.parametrize('parent', ['dist', 'node_modules', '__pycache__'])
def test_launcher_fingerprint_does_not_ignore_installation_directory(tmp_path, parent):
    root = tmp_path / parent / 'aurora'
    source = root / 'frontend-src'
    (source / 'src').mkdir(parents=True)
    (source / 'src/main.tsx').write_text('export const ready = true')
    (source / 'package.json').write_text('{}')
    assert frontend_fingerprint(root) == fingerprint(source)
    before = frontend_fingerprint(root)
    (source / 'src/main.tsx').write_text('export const ready = false')
    assert frontend_fingerprint(root) != before


def test_launcher_detects_missing_public_login_styles(tmp_path):
    source = tmp_path / 'frontend-src'
    (source / 'src').mkdir(parents=True)
    (source / 'public').mkdir()
    (source / 'package.json').write_text('{}')
    (source / 'public/login.css').write_text('body{color:white}')
    bundle = tmp_path / 'frontend'
    bundle.mkdir()
    (bundle / 'index.html').write_text('<script src="/static/app.js"></script>')
    (bundle / 'app.js').write_text('ready()')
    (bundle / 'aurora-source.sha256').write_text(frontend_fingerprint(tmp_path))
    assert frontend_needs_build(tmp_path)
    (bundle / 'login.css').write_text('body{color:white}')
    assert not frontend_needs_build(tmp_path)


@pytest.mark.parametrize('host', ['aurora.example.org/healthz#', 'aurora.example.org/healthz?',
                                 'aurora.example.org/static/#'])
def test_malformed_host_cannot_select_auth_exemption(tmp_path, host):
    with patch.dict(os.environ, configured(), clear=True):
        with TestClient(create_app(Config(database=str(tmp_path/'db')), Feed(), TG()), base_url=ORIGIN) as client:
            assert client.get('/api/state').status_code == 401
            assert client.get('/api/state', headers={'Host': host}, follow_redirects=False).status_code == 403
            # Platform liveness remains available for its actual route.
            assert client.get('/healthz', headers={'Host': 'internal.railway.local'}).status_code == 200
