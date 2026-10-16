from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from backend.integrations.backend_analysis_adapter import BackendAnalysisAdapter
from .consensus import AuroraConsensusEngine
from .orchestrator import PlanetaryAgentOrchestrator
from .risk_guard import RiskGuard


class AIPipeline:
    def __init__(self, agents: Optional[Iterable[Any]] = None, *, min_confidence: float = 75.0):
        self.adapter = BackendAnalysisAdapter()
        self.orchestrator = PlanetaryAgentOrchestrator(list(agents) if agents is not None else [])
        self.consensus = AuroraConsensusEngine(min_votes_concordantes=3, min_confianca=min_confidence,
                                               min_agentes_validos=3)
        self.risk_guard = RiskGuard(min_confidence=min_confidence)

    async def process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.adapter.validate_data(payload)
        normalized = self.adapter.create_analysis_package(payload)
        votes = await self.orchestrator.run_agents(normalized)
        decision = self.consensus.aggregate_votes(votes)
        signal = {
            "ativo": normalized.get("ativo"),
            "mercado": normalized.get("mercado"),
            "payout": normalized.get("payout"),
            "direcao": decision.get("direcao"),
            "confianca": decision.get("confianca", 0.0),
        }
        approved = self.risk_guard.should_allow(signal)
        if not approved and decision.get("direcao") in {"CALL", "PUT"}:
            decision = {**decision, "direcao": None, "motivo": "bloqueado_pelo_risk_guard"}
        return {
            "approved": approved,
            "decision": decision,
            "votes": votes,
            "payload": normalized,
        }


async def process_backend_analysis(payload: Dict[str, Any], agents: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    pipeline = AIPipeline(agents=agents)
    return await pipeline.process(payload)
