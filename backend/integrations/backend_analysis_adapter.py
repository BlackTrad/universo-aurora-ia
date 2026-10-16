from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Dict, Optional
from pydantic import ValidationError
from backend.ai.schemas import AnalysisPackageSchema


class BackendAnalysisAdapter:
    REQUIRED_FIELDS = (
        "ativo",
        "mercado",
        "timeframe",
        "expiracao_minutos",
        "timestamp_utc",
        "preco_atual",
        "payout",
        "candles_m1",
        "candles_m5",
        "indicadores",
        "estrutura_smc",
        "estrategias_backend",
        "filtros_backend",
        "pre_sinal",
    )

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data = data or {}

    @classmethod
    def from_backend(cls, *, asset: str, market: str, source: Optional[str], timeframe: str,
                     expiration_minutes: int, timestamp_utc: str, price: Optional[float],
                     payout: Optional[float], candles_m1, candles_m5, indicators: Dict[str, Any],
                     structure: Dict[str, Any], strategies: list[str], filters: list[Any],
                     candidate: Dict[str, Any], entry_at_utc: Optional[str] = None,
                     expiration_at_utc: Optional[str] = None,
                     source_identity: Optional[str] = None) -> "BackendAnalysisAdapter":
        def candle_rows(rows):
            output=[]
            for row in rows:
                if hasattr(row,'json') and callable(row.json):
                    row=row.json()
                elif hasattr(row,'model_dump') and callable(row.model_dump):
                    row=row.model_dump()
                if not isinstance(row,dict):
                    raise TypeError('backend candles must be dictionaries or candle objects')
                output.append(dict(row))
            return output

        reasons=candidate.get('reasons',[])
        if not isinstance(reasons,list) or any(not isinstance(reason,str) for reason in reasons):
            raise TypeError('backend candidate reasons must be a list of strings')
        package={
            'ativo':asset,
            'mercado':market,
            'fonte':source,
            'source_identity':source_identity,
            'timeframe':timeframe,
            'expiracao_minutos':expiration_minutes,
            'timestamp_utc':timestamp_utc,
            'entrada_utc':entry_at_utc,
            'expiracao_utc':expiration_at_utc,
            'preco_atual':price,
            'payout':payout,
            'candles_m1':candle_rows(candles_m1),
            'candles_m5':candle_rows(candles_m5),
            'indicadores':dict(indicators),
            'estrutura_smc':{
                'tendencia':structure.get('regime','indisponivel'),
                'bos':structure.get('bos'),
                'choch':structure.get('choch'),
                'mss':structure.get('mss'),
                'liquidity_sweep':structure.get('liquidity_sweep'),
                'status':structure.get('status','indisponivel'),
            },
            'estrategias_backend':list(strategies),
            'filtros_backend':list(filters),
            'pre_sinal':{
                'direcao':str(candidate.get('direction','NEUTRO')).upper(),
                'estrategia':candidate.get('strategy_resolved'),
                'justificativa':'; '.join(reasons) or None,
            },
        }
        adapter=cls(package)
        adapter.validate_data()
        return adapter

    def validate_data(self, data: Optional[Dict[str, Any]] = None) -> bool:
        payload = self.data if data is None else data
        if not isinstance(payload, dict):
            raise TypeError("backend analysis payload must be a dictionary")

        missing = [field for field in self.REQUIRED_FIELDS if field not in payload]
        if missing:
            raise ValueError(f"Missing required field(s): {', '.join(missing)}")

        if not isinstance(payload["expiracao_minutos"], int) or isinstance(payload["expiracao_minutos"], bool):
            raise TypeError("expiracao_minutos must be an integer")

        if not isinstance(payload["candles_m1"], list):
            raise TypeError("candles_m1 must be a list")
        if not isinstance(payload["candles_m5"], list):
            raise TypeError("candles_m5 must be a list")
        if not isinstance(payload["indicadores"], dict):
            raise TypeError("indicadores must be a dictionary")
        if not isinstance(payload["estrutura_smc"], dict):
            raise TypeError("estrutura_smc must be a dictionary")
        if not isinstance(payload["pre_sinal"], dict):
            raise TypeError("pre_sinal must be a dictionary")
        if not isinstance(payload["estrategias_backend"], list):
            raise TypeError("estrategias_backend must be a list")
        if not isinstance(payload["filtros_backend"], list):
            raise TypeError("filtros_backend must be a list")
        if payload.get("fonte") is not None and not isinstance(payload["fonte"], str):
            raise TypeError("fonte must be a string or None")
        if not isinstance(payload.get("ativo"), str) or not payload["ativo"]:
            raise ValueError("ativo must be a non-empty string")
        if not isinstance(payload.get("timeframe"), str) or not payload["timeframe"]:
            raise ValueError("timeframe must be a non-empty string")
        if not isinstance(payload.get("timestamp_utc"), str) or not payload["timestamp_utc"]:
            raise ValueError("timestamp_utc must be a non-empty string")
        if payload["pre_sinal"].get("direcao") not in {"CALL", "PUT", "NEUTRO"}:
            raise ValueError("pre_sinal.direcao must be CALL, PUT or NEUTRO")
        try:
            AnalysisPackageSchema.model_validate(payload)
        except ValidationError:
            raise ValueError("backend analysis payload does not match the AI schema") from None

        return True

    def format_data(self, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = self.data if data is None else data
        self.validate_data(payload)

        formatted = dict(payload)
        formatted.setdefault("preco_atual", None)
        formatted.setdefault("payout", 0.0)
        formatted.setdefault("estrategias_backend", [])
        formatted.setdefault("filtros_backend", [])
        formatted.setdefault(
            "pre_sinal",
            {"direcao": "NEUTRO", "estrategia": None, "justificativa": None},
        )
        return formatted

    def create_analysis_package(self, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = self.format_data(data)
        payload["identificador_unico"] = self.generate_unique_id(payload)
        payload["timestamp_captura"] = datetime.now(timezone.utc).isoformat()
        return payload

    def generate_unique_id(self, data: Optional[Dict[str, Any]] = None) -> str:
        payload = self.data if data is None else data
        return f"{payload['ativo']}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex}"