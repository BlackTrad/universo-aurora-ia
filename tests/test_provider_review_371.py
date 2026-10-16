"""Provider regressions use isolated fake transports, never a real credential."""
import asyncio
import json

import httpx
import pytest

from backend.ai import providers
from backend.ai.providers import AIProviderError,PlanetaryAgent,ProviderCoordinator,NVIDIA_URL


def vote():
    return {'direcao':'CALL','confianca':82,'justificativa':'Somente fatos recebidos.',
            'evidencias':['$.ativo'],'alertas':[],'dados_insuficientes':False}


def test_queued_request_observes_another_request_rate_limit():
    async def scenario():
        calls=[]
        started=asyncio.Event();release=asyncio.Event()
        async def handler(request):
            calls.append(request)
            started.set()
            await release.wait()
            return httpx.Response(429,headers={'Retry-After':'30'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator=ProviderCoordinator('nvidia',NVIDIA_URL,'fake-secret',client)
            coordinator.semaphore=asyncio.Semaphore(1)
            first=asyncio.create_task(coordinator.request('GET','/models'))
            await started.wait()
            second=asyncio.create_task(coordinator.request('GET','/models'))
            await asyncio.sleep(0)
            release.set()
            results=await asyncio.gather(first,second,return_exceptions=True)
            assert len(calls)==1
            assert all(isinstance(result,AIProviderError) and result.status=='rate_limit' for result in results)
    asyncio.run(scenario())


def test_rolling_budget_is_shared_and_recovers_after_window(monkeypatch):
    now=[100.0];monkeypatch.setattr(providers.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(providers,'PROVIDER_REQUESTS_PER_MINUTE',2)
    async def scenario():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json={'ok':True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator=ProviderCoordinator('nvidia',NVIDIA_URL,'fake-secret',client)
            await coordinator.request('POST','/chat/completions')
            await coordinator.request('POST','/chat/completions')
            with pytest.raises(AIProviderError) as error:await coordinator.request('POST','/chat/completions')
            assert error.value.status=='rate_limit' and len(calls)==2
            now[0]+=60
            await coordinator.request('POST','/chat/completions')
            assert len(calls)==3
    asyncio.run(scenario())


@pytest.mark.parametrize('finish',['length','content_filter','tool_calls'])
def test_valid_looking_json_from_unfinished_completion_is_rejected(finish):
    async def scenario():
        def handler(request):
            if request.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':'listed'}]})
            return httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':json.dumps(vote())}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator=ProviderCoordinator('nvidia',NVIDIA_URL,'fake-secret',client)
            agent=PlanetaryAgent(name='NVIDIA',provider_key='nvidia',model='listed',role='trend',coordinator=coordinator)
            with pytest.raises(AIProviderError):await agent.analisar({'ativo':'BTC/USD'})
            assert agent.status_snapshot()['status']=='erro'
    asyncio.run(scenario())


def test_glm_page_slug_is_mapped_to_exact_api_model():
    async def scenario():
        requests=[]
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':'z-ai/glm-5.3'}]})
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(vote())}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator=ProviderCoordinator('nvidia',NVIDIA_URL,'fake-secret',client)
            agent=PlanetaryAgent(name='GLM',provider_key='nvidia',model='z-ai/glm-5-3',role='liquidity',coordinator=coordinator)
            assert await agent.analisar({'ativo':'BTC/USD'})==vote()
            assert json.loads(requests[-1].content)['model']=='z-ai/glm-5.3'
            assert agent.status_snapshot()['model']=='z-ai/glm-5.3'
    asyncio.run(scenario())


def test_cancelled_agent_does_not_remain_analyzing():
    async def scenario():
        entered=asyncio.Event()
        async def handler(request):
            if request.url.path.endswith('/models'):return httpx.Response(200,json={'data':[{'id':'listed'}]})
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            coordinator=ProviderCoordinator('nvidia',NVIDIA_URL,'fake-secret',client)
            agent=PlanetaryAgent(name='NVIDIA',provider_key='nvidia',model='listed',role='trend',coordinator=coordinator)
            task=asyncio.create_task(agent.analisar({'ativo':'BTC/USD'}))
            await entered.wait()
            assert agent.status_snapshot()['status']=='analisando'
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert agent.status_snapshot()['status']=='timeout'
    asyncio.run(scenario())
