"""Credential routing must keep per-key catalogs and provider-wide limits."""
import asyncio
import json

import httpx
import pytest

from app.config import Config
from backend.ai.providers import AIProviderError, build_planetary_agents


def test_separate_keys_reach_only_their_agent_catalog_and_chat():
    async def run():
        credentials={
            'NVIDIA': ('unit-nvidia-key','unit-trend-model'),
            'Nemotron': ('unit-nemotron-key','unit-nemotron-model'),
            'GLM': ('unit-glm-key','unit-glm-model'),
            'Kimi': ('unit-kimi-key','unit-kimi-model'),
        }
        calls=[]
        by_key={key:model for key,model in credentials.values()}
        def handler(request):
            key=request.headers['authorization'].removeprefix('Bearer ')
            calls.append((request.url.path,key))
            if request.url.path.endswith('/models'):
                return httpx.Response(200,json={'data':[{'id':by_key[key]}]})
            assert json.loads(request.content)['model']==by_key[key]
            vote={'direcao':'NEUTRO','confianca':0,'justificativa':'Dados insuficientes.',
                  'evidencias':[],'alertas':[],'dados_insuficientes':True}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(vote)}}]})
        config=Config(nvidia_api_key=credentials['NVIDIA'][0],nvidia_trend_model=credentials['NVIDIA'][1],
                      nemotron_api_key=credentials['Nemotron'][0],nvidia_nemotron_model=credentials['Nemotron'][1],
                      glm_api_key=credentials['GLM'][0],nvidia_glm_model=credentials['GLM'][1],
                      kimi_api_key=credentials['Kimi'][0],nvidia_kimi_model=credentials['Kimi'][1])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agents=[a for a in build_planetary_agents(config,client) if a.name in credentials]
            for agent in agents:
                assert (await agent.analisar({}))['direcao']=='NEUTRO'
            assert len(calls)==8
            for key in by_key:
                assert sum(path.endswith('/models') and used==key for path,used in calls)==1
                assert sum(path.endswith('/chat/completions') and used==key for path,used in calls)==1
    asyncio.run(run())


def test_distinct_keys_share_provider_cooldown_and_rolling_budget():
    async def run():
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(429,headers={'Retry-After':'120'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            config=Config(nvidia_api_key='unit-nvidia-key',glm_api_key='unit-glm-key')
            agents={a.name:a for a in build_planetary_agents(config,client)}
            first,second=agents['NVIDIA'],agents['GLM']
            with pytest.raises(AIProviderError) as error:
                await first._ensure_model()
            assert error.value.status=='rate_limit'
            with pytest.raises(AIProviderError) as error:
                await second._ensure_model()
            assert error.value.status=='rate_limit'
            assert len(calls)==1
            assert second.coordinator.cooldown_until==first.coordinator.cooldown_until
            assert second.coordinator.request_times is first.coordinator.request_times
    asyncio.run(run())


def test_existing_shared_nvidia_key_still_validates_catalog_only_once():
    async def run():
        config=Config(nvidia_api_key='unit-shared-key',nvidia_kimi_model='unit-kimi-model')
        models=[config.nvidia_trend_model,config.nvidia_nemotron_model,'z-ai/glm-5.3',config.nvidia_kimi_model]
        calls=[]
        def handler(request):
            calls.append(request)
            assert request.headers['authorization']=='Bearer unit-shared-key'
            return httpx.Response(200,json={'data':[{'id':m} for m in models]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for agent in build_planetary_agents(config,client):
                if agent.provider_key=='nvidia':await agent._ensure_model()
            assert len(calls)==1
    asyncio.run(run())


def test_env_loads_per_agent_keys_without_exposing_them_in_config_repr(monkeypatch):
    values={'NEMOTRON_API_KEY':'unit-nemotron-key','GLM_API_KEY':'unit-glm-key','KIMI_API_KEY':'unit-kimi-key'}
    for name,value in values.items():monkeypatch.setenv(name,value)
    monkeypatch.setenv('DATA_PROVIDER','deriv')
    monkeypatch.setenv('ASSETS','EUR/USD')
    config=Config.load()
    assert config.nemotron_api_key==values['NEMOTRON_API_KEY']
    assert config.glm_api_key==values['GLM_API_KEY']
    assert config.kimi_api_key==values['KIMI_API_KEY']
    assert all(value not in repr(config) for value in values.values())
