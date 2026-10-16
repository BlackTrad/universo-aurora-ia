from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional


class RiskGuard:
    def __init__(self, min_confidence: float = 75.0, require_direction: bool = True):
        self.min_confidence = min_confidence
        self.require_direction = require_direction

    def validate_signal(self, signal: Dict[str, Any]) -> bool:
        if not isinstance(signal, dict):
            return False
        if not signal.get("ativo"):
            return False
        direction = str(signal.get("direcao", "")).upper()
        if self.require_direction and direction not in {"CALL", "PUT"}:
            return False
        payout=signal.get('payout')
        if (signal.get('mercado')!='OPCOES_BINARIAS' or isinstance(payout,bool)
                or not isinstance(payout,(int,float)) or not math.isfinite(payout) or not 0.9<=payout<=1):
            return False
        try:
            value=signal.get("confianca", 0)
            if isinstance(value,bool):return False
            confidence=float(value)
        except (ValueError,TypeError,OverflowError):
            return False
        if not math.isfinite(confidence) or confidence < self.min_confidence and direction in {"CALL", "PUT"}:
            return False
        return True

    def should_allow(self, signal: Dict[str, Any]) -> bool:
        return self.validate_signal(signal)

    def validate_consensus(self, decision: Dict[str, Any]) -> bool:
        if not isinstance(decision, dict):
            return False
        if decision.get("direcao") not in {"CALL", "PUT"}:
            return False
        try:
            value=decision.get("confianca", 0)
            if isinstance(value,bool):return False
            confidence=float(value)
        except (ValueError,TypeError,OverflowError):return False
        return math.isfinite(confidence) and confidence >= self.min_confidence


class AIAgentRiskGuard(RiskGuard):
    pass
