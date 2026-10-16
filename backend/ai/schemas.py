from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Union

class IndicatorSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rsi: Optional[float] = None
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    macd: Optional[float] = None

class StructureSMCSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tendencia: str
    bos: Optional[float] = None
    choch: Optional[float] = None
    mss: Optional[float] = None
    liquidity_sweep: Optional[float] = None
    status: str = "indisponivel"

class PreSignalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direcao: str
    estrategia: Optional[str] = None
    justificativa: Optional[str] = None

class AnalysisPackageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ativo: str
    mercado: str
    fonte: Optional[str] = None
    source_identity: Optional[str] = None
    timeframe: str
    expiracao_minutos: int
    timestamp_utc: str
    entrada_utc: Optional[str] = None
    expiracao_utc: Optional[str] = None
    preco_atual: Optional[float] = None
    payout: Optional[float] = None
    candles_m1: List[dict]
    candles_m5: List[dict]
    indicadores: IndicatorSchema
    estrutura_smc: StructureSMCSchema
    estrategias_backend: List[str]
    filtros_backend: List[Union[str, dict]]
    pre_sinal: PreSignalSchema

class AgentVoteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    direcao: str = Field(strict=True, pattern="^(CALL|PUT|NEUTRO)$")
    confianca: int = Field(strict=True, ge=0, le=100)
    justificativa: str
    evidencias: List[str]
    alertas: List[str]
    dados_insuficientes: bool

class ConsensusResultSchema(BaseModel):
    votos: List[AgentVoteSchema]
    direcao_final: str
    confianca_media: float
    motivo_rejeicao: Optional[str] = None