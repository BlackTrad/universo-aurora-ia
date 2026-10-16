import asyncio
import json
import time

import httpx
import pytest

from backend.ai.providers import AIProviderError, GEMINI_URL, GROQ_URL, NVIDIA_URL, PlanetaryAgent, ProviderCoordinator


def make_agent(handler, *, api_key='provider-secret', model='listed-model', base_url=GROQ_URL):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    coordinator = ProviderCoordinator('groq', base_url, api_key, client)
    return PlanetaryAgent(name='Groq', provider_key='groq', model=model, role='Price Action', coordinator=coordinator), client


def test_agent_checks_exact_model_before_calling_and_parses_json():
    requests = []
    vote = {
        'direcao': 'CALL', 'confianca': 82, 'justificativa': 'Pré-sinal alinhado.',
        'evidencias': ['$.pre_sinal.direcao'], 'alertas': [], 'dados_insuficientes': False,
    }

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'listed-model'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(vote)}}]})

    agent, client = make_agent(handler)
    result = asyncio.run(agent.analisar({'ativo': 'BTC/USD', 'pre_sinal': {'direcao': 'CALL'}}))
    asyncio.run(client.aclose())

    assert [request.method for request in requests] == ['GET', 'POST']
    assert requests[1].url.path.endswith('/chat/completions')
    assert requests[1].headers['authorization'] == 'Bearer provider-secret'
    assert result == vote
    assert agent.status_snapshot()['status'] == 'disponivel'


def test_unlisted_model_is_unavailable_without_chat_fallback():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'different-model'}]})

    agent, client = make_agent(handler)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(agent.analisar({'ativo': 'BTC/USD'}))
    asyncio.run(client.aclose())

    assert error.value.status == 'indisponivel'
    assert len(requests) == 1
    assert requests[0].url.path.endswith('/models')


def test_missing_key_never_sends_a_request():
    requests = []
    agent, client = make_agent(lambda request: requests.append(request), api_key='')

    with pytest.raises(AIProviderError) as error:
        asyncio.run(agent.analisar({'ativo': 'BTC/USD'}))
    asyncio.run(client.aclose())

    assert error.value.status == 'indisponivel'
    assert requests == []
    assert agent.status_snapshot()['status'] == 'indisponivel'


def test_invented_evidence_is_rejected():
    def handler(request):
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'listed-model'}]})
        vote = {'direcao': 'CALL', 'confianca': 90, 'justificativa': 'ok',
                'evidencias': ['preço vai subir'], 'alertas': [], 'dados_insuficientes': False}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(vote)}}]})

    agent, client = make_agent(handler)
    with pytest.raises(AIProviderError) as error:
        asyncio.run(agent.analisar({'ativo': 'BTC/USD', 'pre_sinal': {'direcao': 'CALL'}}))
    asyncio.run(client.aclose())

    assert error.value.status == 'erro'
    assert agent.status_snapshot()['status'] == 'erro'


def test_rate_limit_cooldown_is_shared_by_agents_on_provider():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={'Retry-After': '3'}, json={'error': 'limited'})

    agent, client = make_agent(handler)
    with pytest.raises(AIProviderError) as first:
        asyncio.run(agent.analisar({'ativo': 'BTC/USD'}))
    with pytest.raises(AIProviderError) as second:
        asyncio.run(agent.analisar({'ativo': 'BTC/USD'}))
    asyncio.run(client.aclose())

    assert first.value.status == 'rate_limit'
    assert second.value.status == 'rate_limit'
    assert len(requests) == 1
    assert agent.coordinator.cooldown_until > time.monotonic()


def test_fixed_provider_endpoints_are_not_overridden():
    assert GROQ_URL == 'https://api.groq.com/openai/v1'
    assert NVIDIA_URL == 'https://integrate.api.nvidia.com/v1'
    assert GEMINI_URL == 'https://generativelanguage.googleapis.com/v1beta/openai/'
