from typing import Any, Dict, List
import asyncio
import hashlib
import json
import logging
import time
import math
from collections import OrderedDict, defaultdict
from .schemas import AgentVoteSchema


class LLMAgent:
    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("This method should be overridden by subclasses.")


class PlanetaryAgentOrchestrator:
    def __init__(self, agents: List[LLMAgent], *, agent_timeout: float = 8.0,
                 provider_concurrency: int = 2, cache_size: int = 256):
        self.agents = agents or []
        self.agent_timeout = agent_timeout
        self.provider_concurrency = provider_concurrency
        self.cache_size = cache_size
        self.logger = logging.getLogger(__name__)
        self.provider_semaphores = {}
        self.cache = OrderedDict()
        self.metrics = defaultdict(lambda: {'calls': 0, 'available': 0, 'timeouts': 0,
                            'rate_limits': 0, 'unavailable': 0, 'errors': 0})

    async def run_agents(self, dados: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.agents:
            return []
        tasks = [self.run_agent(agent, dados) for agent in self.agents]
        return await asyncio.gather(*tasks)

    async def run(self, dados: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await self.run_agents(dados)

    async def run_agent(self, agent: LLMAgent, dados: Dict[str, Any]) -> Dict[str, Any]:
        name=getattr(agent,'name',agent.__class__.__name__)
        cache_key=self._cache_key(agent,dados)
        if cache_key is not None:
            cached=self.cache.get(cache_key)
            if cached and cached[0]>time.monotonic():
                self.cache.move_to_end(cache_key)
                return {**cached[1], 'cache_hit': True, 'latency_ms': 0.0}
            if cached:self.cache.pop(cache_key,None)
        started=time.monotonic()
        self.metrics[name]['calls']+=1
        try:
            result = getattr(agent, "analisar", None)
            if result is None:
                result = getattr(agent, "run", None)
            if result is None:
                raise AttributeError(f"Agent {agent!r} does not implement an analysis method")
            semaphore=self.provider_semaphores.setdefault(
                getattr(agent,'provider_key',name),asyncio.Semaphore(self.provider_concurrency))
            # Queue wait is bounded independently; a healthy second wave gets
            # its full request timeout after acquiring the provider slot.
            queue_timeout=self.agent_timeout*max(1,math.ceil(len(self.agents)/self.provider_concurrency))
            await asyncio.wait_for(semaphore.acquire(),queue_timeout)
            call_started=time.monotonic()
            try:
                async def invoke():
                    outcome=result(dados)
                    if asyncio.iscoroutine(outcome):outcome=await outcome
                    return outcome
                outcome=await asyncio.wait_for(invoke(),self.agent_timeout)
            finally:
                semaphore.release()
            vote=self._normalize_vote(outcome)
            normalized={**vote,'agente':name,'status':'disponivel','cache_hit':False,
                        'latency_ms':round((time.monotonic()-call_started)*1000,2),
                        'queue_ms':round((call_started-started)*1000,2)}
            self.metrics[name]['available']+=1
            if cache_key is not None and self.cache_size>0:
                self.cache[cache_key]=(time.monotonic()+300,normalized)
                self.cache.move_to_end(cache_key)
                while len(self.cache)>self.cache_size:self.cache.popitem(last=False)
            return normalized
        except asyncio.TimeoutError:
            return self._failure(name,'timeout',started)
        except Exception as exc:
            self.logger.warning("Agent vote failed validation or execution: %s",name)
            status=getattr(exc,'status','erro')
            if status not in {'indisponivel','rate_limit','timeout','erro'}:status='erro'
            failure=self._failure(name,status,started)
            retry_after=getattr(exc,'retry_after',None)
            if status=='rate_limit' and type(retry_after) is int and retry_after>0:
                failure['retry_after']=retry_after
            return failure

    def _failure(self,name,status,started):
        metric={'timeout':'timeouts','rate_limit':'rate_limits','indisponivel':'unavailable'}.get(status,'errors')
        self.metrics[name][metric]+=1
        return {'agente':name,'status':status,'direcao':'NEUTRO','confianca':0,
                'cache_hit':False,'justificativa':'Resposta do agente indisponível ou inválida.',
                'evidencias':[],'alertas':[],'dados_insuficientes':True,
                'latency_ms':round((time.monotonic()-started)*1000,2)}

    @staticmethod
    def _cache_key(agent, dados):
        """Hash the actual analysis input, not just its last candle.

        An unchanged candle with a changed payout, source, entry window, filter,
        quote, or model/prompt MUST produce a fresh vote.  Capture-only fields
        generated by the adapter may differ on an identical retry and are
        deliberately excluded.  Malformed or unversioned packages without
        candles are never cached.
        """
        if not isinstance(dados, dict) or not isinstance(dados.get('ativo'), str):
            return None
        if not dados.get('candles_m1') and not dados.get('candles_m5'):
            return None
        basis = {key: value for key, value in dados.items()
                 if key not in {'identificador_unico', 'timestamp_captura'}}
        try:
            encoded = json.dumps(basis, sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return None
        identity = '|'.join(str(getattr(agent, key, ''))
                            for key in ('name', 'provider_key', 'model', 'role'))
        return hashlib.sha256((identity + '|' + encoded).encode()).hexdigest()

    @staticmethod
    def _normalize_vote(voto: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(voto, dict):
            raise TypeError("Agent response must be a dictionary")
        return AgentVoteSchema.model_validate(voto).model_dump()
