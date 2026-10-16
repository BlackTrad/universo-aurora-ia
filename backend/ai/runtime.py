from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .consensus import AuroraConsensusEngine
from .orchestrator import PlanetaryAgentOrchestrator
from .risk_guard import RiskGuard


class AgentRuntime:
    def __init__(self, agents: Optional[Iterable[Any]] = None, *, min_confidence: float = 75.0):
        self.orchestrator = PlanetaryAgentOrchestrator(list(agents) if agents is not None else [])
        self.consensus = AuroraConsensusEngine(min_votes_concordantes=3, min_confianca=min_confidence,
                                               min_agentes_validos=3)
        self.risk_guard = RiskGuard(min_confidence=min_confidence)

    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        votes = await self.orchestrator.run_agents(payload)
        decision = self.consensus.aggregate_votes(votes)
        signal = {
            "ativo": payload.get("ativo"),
            "mercado": payload.get("mercado"),
            "payout": payload.get("payout"),
            "direcao": decision.get("direcao"),
            "confianca": decision.get("confianca", 0.0),
        }
        if not self.risk_guard.should_allow(signal):
            decision = {**decision, "direcao": None, "motivo": "bloqueado_pelo_risk_guard"}
        return {
            "approved": decision.get("direcao") is not None,
            "decision": decision,
            "votes": votes,
        }
