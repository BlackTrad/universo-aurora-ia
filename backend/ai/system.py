from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .consensus import AuroraConsensusEngine
from .orchestrator import PlanetaryAgentOrchestrator
from .risk_guard import RiskGuard


@dataclass
class AIConfig:
    min_votes_concordantes: int = 3
    min_confianca: float = 75.0
    min_agentes_validos: int = 3
    require_direction: bool = True


class AIAgentSystem:
    def __init__(self, agents: Optional[Iterable[Any]] = None, config: Optional[AIConfig] = None):
        self.config = config or AIConfig()
        self.orchestrator = PlanetaryAgentOrchestrator(list(agents) if agents is not None else [])
        self.consensus = AuroraConsensusEngine(
            min_votes_concordantes=self.config.min_votes_concordantes,
            min_confianca=self.config.min_confianca,
            min_agentes_validos=self.config.min_agentes_validos,
        )
        self.risk_guard = RiskGuard(
            min_confidence=self.config.min_confianca,
            require_direction=self.config.require_direction,
        )

    def with_agents(self, agents: Iterable[Any]) -> "AIAgentSystem":
        self.orchestrator = PlanetaryAgentOrchestrator(list(agents))
        return self

    async def process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        votes = await self.orchestrator.run_agents(payload)
        decision = self.consensus.aggregate_votes(votes)
        signal = {
            "ativo": payload.get("ativo"),
            "mercado": payload.get("mercado"),
            "payout": payload.get("payout"),
            "direcao": decision.get("direcao"),
            "confianca": decision.get("confianca", 0.0),
        }
        allowed = self.risk_guard.should_allow(signal)
        return {
            "approved": allowed,
            "decision": decision,
            "votes": votes,
        }


def build_ai_system(agents: Optional[Iterable[Any]] = None, config: Optional[AIConfig] = None) -> AIAgentSystem:
    return AIAgentSystem(agents=agents, config=config)
