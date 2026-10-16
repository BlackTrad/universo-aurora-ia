from __future__ import annotations

from typing import Any, Dict, List, Sequence


class BaseAgent:
    name = "base"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "direcao": "NEUTRO",
            "confianca": 25,
            "justificativa": "Agente base sem decisão específica.",
            "evidencias": [],
            "alertas": [],
            "dados_insuficientes": True,
        }


class MercuryAgent(BaseAgent):
    name = "mercury"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        base = dados.get("pre_sinal", {})
        return {
            "direcao": str(base.get("direcao", "NEUTRO")).upper(),
            "confianca": 70,
            "justificativa": "Mercury: leitura rápida da estrutura e impulso.",
            "evidencias": ["estrutura_candles", "impulso"],
            "alertas": [],
            "dados_insuficientes": False,
        }


class VenusAgent(BaseAgent):
    name = "venus"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "direcao": "CALL" if dados.get("mercado") else "NEUTRO",
            "confianca": 72,
            "justificativa": "Venus: valida contexto de tendência e rejeição.",
            "evidencias": ["tendencia", "rejeicao"],
            "alertas": [],
            "dados_insuficientes": False,
        }


class EarthAgent(BaseAgent):
    name = "earth"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        indicador = dados.get("indicadores", {})
        direction = "CALL" if indicador.get("rsi", 50) >= 52 else "PUT"
        return {
            "direcao": direction,
            "confianca": 68,
            "justificativa": "Earth: confirma a leitura via RSI e estrutura.",
            "evidencias": ["rsi", "estrutura_smc"],
            "alertas": [],
            "dados_insuficientes": False,
        }


class MarsAgent(BaseAgent):
    name = "mars"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        estrutura = dados.get("estrutura_smc", {})
        direction = "CALL" if estrutura.get("tendencia") == "BULLISH" else "PUT"
        return {
            "direcao": direction,
            "confianca": 74,
            "justificativa": "Mars: prioridade ao BOS/CHoCH e liquidez.",
            "evidencias": ["bos", "liquidity_sweep"],
            "alertas": [],
            "dados_insuficientes": False,
        }


class JupiterAgent(BaseAgent):
    name = "jupiter"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        pre = dados.get("pre_sinal", {})
        direction = str(pre.get("direcao", "NEUTRO")).upper()
        return {
            "direcao": direction,
            "confianca": 76,
            "justificativa": "Jupiter: aumenta a confiança no sinal já validado pelo backend.",
            "evidencias": ["pre_sinal", "estrategias_backend"],
            "alertas": [],
            "dados_insuficientes": False,
        }


class SaturnAgent(BaseAgent):
    name = "saturn"

    async def analisar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "direcao": "NEUTRO",
            "confianca": 60,
            "justificativa": "Saturn: risco prudente para cenário indefinido.",
            "evidencias": ["protecao"],
            "alertas": ["ambiente_em_teste"],
            "dados_insuficientes": True,
        }


def default_planet_agents() -> Sequence[BaseAgent]:
    return [
        MercuryAgent(),
        VenusAgent(),
        EarthAgent(),
        MarsAgent(),
        JupiterAgent(),
        SaturnAgent(),
    ]
