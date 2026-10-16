from typing import Any, Dict, List, Optional


class AuroraConsensusEngine:
    def __init__(self, min_votes_concordantes: int = 3, min_confianca: float = 75.0,
                 min_agentes_validos: int = 3):
        self.min_votes_concordantes = min_votes_concordantes
        self.min_confianca = min_confianca
        self.min_agentes_validos = min_agentes_validos

    def aggregate_votes(self, votes: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not votes:
            return {"direcao": None, "confianca": 0.0, "motivo": "sem_votos"}

        counts = {"CALL": 0, "PUT": 0, "NEUTRO": 0}
        confidence_by_direction = {"CALL": [], "PUT": [], "NEUTRO": []}

        seen_agents = set()
        for vote in votes:
            if not isinstance(vote, dict):
                continue
            if vote.get("dados_insuficientes"):
                continue
            # A failed/limited agent cannot cast a vote.  A provider must not
            # count twice if its result was accidentally appended again.
            if 'status' in vote and vote['status'] != 'disponivel':
                continue
            agent = vote.get('agente')
            if agent is not None:
                if not isinstance(agent, str) or not agent.strip() or agent in seen_agents:
                    continue
                seen_agents.add(agent)
            direction = str(vote.get("direcao", "")).upper()
            if direction not in counts:
                continue
            count = vote.get("confianca")
            if isinstance(count, bool) or not isinstance(count, (int, float)) or not 0 <= count <= 100:
                continue
            counts[direction] += 1
            confidence_by_direction[direction].append(float(count))

        valid_agents = sum(counts.values())
        if valid_agents < self.min_agentes_validos:
            return {"direcao": None, "confianca": 0.0, "motivo": "agentes_validos_insuficientes",
                    "agentes_validos": valid_agents}

        if counts["CALL"] == 0 and counts["PUT"] == 0 and counts["NEUTRO"] > 0:
            avg = self._average(confidence_by_direction["NEUTRO"])
            return {"direcao": "NEUTRO", "confianca": round(avg, 2), "motivo": "somente_neutro",
                    "agentes_validos": valid_agents}

        if counts["CALL"] == counts["PUT"]:
            return {"direcao": None, "confianca": 0.0, "motivo": "empate_entre_call_put",
                    "agentes_validos": valid_agents}

        winner = "CALL" if counts["CALL"] > counts["PUT"] else "PUT"
        winner_count = counts[winner]
        if winner_count < self.min_votes_concordantes:
            return {"direcao": None, "confianca": 0.0, "motivo": "votos_insuficientes",
                    "agentes_validos": valid_agents}
        if valid_agents == self.min_agentes_validos and winner_count != valid_agents:
            return {"direcao": None, "confianca": 0.0, "motivo": "unanimidade_insuficiente",
                    "agentes_validos": valid_agents}

        avg = self._average(confidence_by_direction[winner])
        if avg < self.min_confianca:
            return {"direcao": None, "confianca": round(avg, 2), "motivo": "confianca_insuficiente",
                    "agentes_validos": valid_agents}
        return {"direcao": winner, "confianca": round(avg, 2), "motivo": "maioria_" + winner.lower(),
                "agentes_validos": valid_agents}

    @staticmethod
    def _average(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def calcular_consenso(self, votos: List[Dict[str, Any]]) -> Dict[str, Any]:
        result = self.aggregate_votes(votos)
        direcao = result.get("direcao")
        return {
            "resultado": "APROVADO" if direcao in {"CALL", "PUT"} else "REJEITADO",
            "direcao": direcao,
            "confianca_media": result.get("confianca", 0.0),
            "motivo": result.get("motivo"),
        }

    def determinar_direcao_final(self, direcao_counts: Dict[str, int]) -> Optional[str]:
        if direcao_counts.get("CALL", 0) > direcao_counts.get("PUT", 0):
            return "CALL"
        if direcao_counts.get("PUT", 0) > direcao_counts.get("CALL", 0):
            return "PUT"
        if direcao_counts.get("CALL", 0) == 0 and direcao_counts.get("PUT", 0) == 0:
            return "NEUTRO"
        return None