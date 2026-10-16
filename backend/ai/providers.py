from __future__ import annotations

import asyncio
from collections import deque
import json
import math
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .schemas import AgentVoteSchema


GROQ_URL = 'https://api.groq.com/openai/v1'
NVIDIA_URL = 'https://integrate.api.nvidia.com/v1'
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/'
PROVIDER_REQUESTS_PER_MINUTE = 12
MODEL_ALIASES = {'z-ai/glm-5-3': 'z-ai/glm-5.3'}


def retry_after_seconds(value: str) -> int:
    """Retry-After permits both seconds and an HTTP date."""
    try:
        delay=float(value)
    except (TypeError,ValueError,OverflowError):
        try:delay=parsedate_to_datetime(value).timestamp()-time.time()
        except (TypeError,ValueError,OverflowError,OSError):return 60
    return max(1,min(86400,math.ceil(delay))) if math.isfinite(delay) else 60


class AIProviderError(Exception):
    def __init__(self, status: str, retry_after: int | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(status)


class ProviderBudget:
    """One quota and cooldown per provider, including its separate credentials."""
    def __init__(self):
        self.semaphore=asyncio.Semaphore(2)
        self.request_times=deque()
        self.cooldown_until=0.0


class ProviderCoordinator:
    def __init__(self, provider_key: str, base_url: str, api_key: str, client: httpx.AsyncClient,
                 *, budget: ProviderBudget | None = None):
        self.provider_key=provider_key
        self.base_url=base_url.rstrip('/')
        self.api_key=api_key
        self.client=client
        self.budget=budget if budget is not None else ProviderBudget()
        self.semaphore=self.budget.semaphore
        self.models_lock=asyncio.Lock()
        self.models_cache: set[str] | None=None
        self.models_until=0.0
        self.request_times=self.budget.request_times

    @property
    def cooldown_until(self) -> float:
        return self.budget.cooldown_until

    @cooldown_until.setter
    def cooldown_until(self, value: float) -> None:
        self.budget.cooldown_until=value

    def _check_budget(self) -> None:
        now=time.monotonic()
        delay=self.cooldown_until-now
        if delay>0:
            raise AIProviderError('rate_limit',max(1,int(delay)+1))
        while self.request_times and now-self.request_times[0]>=60:
            self.request_times.popleft()
        if len(self.request_times)>=PROVIDER_REQUESTS_PER_MINUTE:
            raise AIProviderError('rate_limit',max(1,int(60-(now-self.request_times[0]))+1))

    def configured(self) -> bool:
        return bool(self.api_key)

    async def request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
        if not self.configured():
            raise AIProviderError('indisponivel')
        self._check_budget()
        headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'}
        async with self.semaphore:
            # Requests already queued must also observe a sibling's 429 and
            # the shared rolling budget before sending another credential.
            self._check_budget()
            self.request_times.append(time.monotonic())
            try:
                response=await self.client.request(method,self.base_url+path,headers=headers,json=body,timeout=8)
            except httpx.TimeoutException:
                raise AIProviderError('timeout') from None
            except httpx.HTTPError:
                raise AIProviderError('erro') from None
        if response.status_code==429:
            retry_after=retry_after_seconds(response.headers.get('Retry-After','60'))
            # Concurrent responses must never shorten a pause already imposed
            # by the same provider on another request/model.
            self.cooldown_until=max(self.cooldown_until,time.monotonic()+retry_after)
            raise AIProviderError('rate_limit',max(1,math.ceil(self.cooldown_until-time.monotonic())))
        if response.status_code in (401,403):
            raise AIProviderError('indisponivel')
        if response.status_code>=400:
            raise AIProviderError('erro')
        try:return response.json()
        except ValueError:
            raise AIProviderError('erro') from None

    async def models(self) -> set[str]:
        now=time.monotonic()
        if self.models_cache is not None and now<self.models_until:
            return self.models_cache
        async with self.models_lock:
            now=time.monotonic()
            if self.models_cache is not None and now<self.models_until:
                return self.models_cache
            result=await self.request('GET','/models')
            rows=result.get('data',result.get('models',[])) if isinstance(result,dict) else []
            if not isinstance(rows,list):
                raise AIProviderError('erro')
            names=set()
            for row in rows:
                if not isinstance(row,dict):continue
                value=row.get('id') or row.get('name')
                if isinstance(value,str):names.add(value.removeprefix('models/'))
            self.models_cache=names
            self.models_until=time.monotonic()+300
            return names


class PlanetaryAgent:
    def __init__(self, *, name: str, provider_key: str, model: str, role: str,
                 coordinator: ProviderCoordinator):
        self.name=name
        self.provider_key=provider_key
        self.requested_model=model.strip()
        self.model=MODEL_ALIASES.get(self.requested_model,self.requested_model) if provider_key=='nvidia' else self.requested_model
        self.role=role
        self.coordinator=coordinator
        self.state='indisponivel'
        self.detail='Aguardando credencial, modelo e validação do provedor.'

    def status_snapshot(self) -> dict[str, Any]:
        if not self.coordinator.configured():
            return {'agente':self.name,'status':'indisponivel','detail':'Credencial ausente no ambiente local.','model':self.model or None}
        if not self.model:
            return {'agente':self.name,'status':'indisponivel','detail':'Modelo não configurado ou não validado.','model':None}
        return {'agente':self.name,'status':self.state,'detail':self.detail,'model':self.model}

    async def _ensure_model(self) -> None:
        if not self.coordinator.configured() or not self.model:
            self.state='indisponivel'
            self.detail='Credencial ou modelo ausente no ambiente local.'
            raise AIProviderError('indisponivel')
        try:
            models=await self.coordinator.models()
        except AIProviderError as error:
            self.state=error.status
            self.detail='Falha ao validar o catálogo do provedor.'
            raise
        if self.model not in models:
            self.state='indisponivel'
            self.detail='O modelo configurado não consta no catálogo do provedor.'
            raise AIProviderError('indisponivel')
        self.state='disponivel'
        self.detail='Modelo confirmado no catálogo do provedor.'

    async def analisar(self, dados: dict[str, Any]) -> dict[str, Any]:
        try:
            await self._ensure_model()
            self.state='analisando'
            self.detail='Análise independente em andamento.'
            system=(
                f'Você é o planeta {self.name}, especialista em {self.role}. '
                'Analise somente o pacote JSON recebido. Não invente preço, candle, indicador, horário, payout ou evento. '
                'Se um dado necessário não existir, responda NEUTRO e marque dados_insuficientes=true. '
                'Não veja nem use votos de outros agentes. Confiança mede suporte da evidência, não probabilidade de vitória. '
                'Retorne apenas um objeto JSON com direcao (CALL, PUT ou NEUTRO), confianca (inteiro 0-100), '
                'justificativa, evidencias (somente caminhos JSON presentes no pacote), alertas e dados_insuficientes (booleano).'
            )
            body={
                'model':self.model,
                'temperature':0,
                'max_tokens':500,
                'response_format':{'type':'json_object'},
                'messages':[
                    {'role':'system','content':system},
                    {'role':'user','content':json.dumps(dados,ensure_ascii=False,separators=(',',':'),allow_nan=False)},
                ],
            }
            result=await self.coordinator.request('POST','/chat/completions',body=body)
            choice=result['choices'][0]
            if choice.get('finish_reason') not in (None,'stop'):
                raise ValueError('Incomplete model response')
            content=choice['message']['content']
            if not isinstance(content,str):raise ValueError()
            vote=AgentVoteSchema.model_validate(json.loads(content)).model_dump()
            allowed=self._evidence_paths(dados)
            if any(path not in allowed for path in vote['evidencias']):raise ValueError()
        except asyncio.CancelledError:
            self.state='timeout'
            self.detail='Análise encerrada por prazo ou cancelamento; nenhum voto disponível.'
            raise
        except AIProviderError as error:
            self.state=error.status
            self.detail='Provedor indisponível, limitado ou sem resposta válida.'
            raise
        except (KeyError,IndexError,TypeError,ValueError):
            self.state='erro'
            self.detail='Resposta fora do contrato JSON do planeta.'
            raise AIProviderError('erro') from None
        self.state='disponivel'
        self.detail='Modelo respondeu com JSON validado.'
        return vote

    @staticmethod
    def _evidence_paths(value: Any, path: str = '$') -> set[str]:
        paths={path}
        if isinstance(value,dict):
            for key,item in value.items():paths.update(PlanetaryAgent._evidence_paths(item,f'{path}.{key}'))
        elif isinstance(value,list):
            for index,item in enumerate(value):paths.update(PlanetaryAgent._evidence_paths(item,f'{path}[{index}]'))
        return paths


def build_planetary_agents(config, client: httpx.AsyncClient) -> list[PlanetaryAgent]:
    groq=ProviderCoordinator('groq',GROQ_URL,config.groq_api_key,client)
    nvidia=ProviderCoordinator('nvidia',NVIDIA_URL,config.nvidia_api_key,client)
    gemini=ProviderCoordinator('gemini',GEMINI_URL,config.gemini_api_key,client)
    # Catalogs belong to credentials; rate limits belong to the provider.
    # Reuse the old NVIDIA key when a separate key was not supplied.
    nvidia_credentials={config.nvidia_api_key:nvidia}
    def nvidia_for(field):
        key=getattr(config,field,'') or config.nvidia_api_key
        if key not in nvidia_credentials:
            nvidia_credentials[key]=ProviderCoordinator('nvidia',NVIDIA_URL,key,client,budget=nvidia.budget)
        return nvidia_credentials[key]
    specifications=(
        ('Groq','groq',config.groq_model,'Price Action: força e comando das velas, rejeições, exaustão, continuação e correções.',groq),
        ('NVIDIA','nvidia',config.nvidia_trend_model,'tendência: EMA, RSI, MACD, momentum M1/M5, continuação e retração.',nvidia),
        ('Nemotron','nvidia',config.nvidia_nemotron_model,'estrutura SMC: BOS, CHoCH, MSS e liquidez, somente quando presentes.',nvidia_for('nemotron_api_key')),
        ('GLM','nvidia',config.nvidia_glm_model,'liquidez: falsos rompimentos, reversões, rejeições e Fibonacci, somente com dados presentes.',nvidia_for('glm_api_key')),
        ('Gemini','gemini',config.gemini_model,'confluências, contradições, volatilidade e fatores desfavoráveis.',gemini),
        ('Kimi','nvidia',config.nvidia_kimi_model,'análise crítica, armadilhas, cenários alternativos e condições para NEUTRO.',nvidia_for('kimi_api_key')),
    )
    return [PlanetaryAgent(name=name,provider_key=provider,model=model,role=role,coordinator=coordinator)
            for name,provider,model,role,coordinator in specifications]
