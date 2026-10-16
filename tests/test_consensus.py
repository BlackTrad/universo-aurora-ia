import pytest
from backend.ai.consensus import AuroraConsensusEngine
from backend.ai.pipeline import AIPipeline
from backend.ai.risk_guard import RiskGuard
from backend.ai.runtime import AgentRuntime
from backend.ai.system import build_ai_system

def test_consensus_with_majority_call():
    votes = [
        {"direcao": "CALL", "confianca": 80},
        {"direcao": "CALL", "confianca": 75},
        {"direcao": "PUT", "confianca": 70},
        {"direcao": "CALL", "confianca": 85},
        {"direcao": "NEUTRO", "confianca": 50},
    ]
    consensus_engine = AuroraConsensusEngine()
    result = consensus_engine.aggregate_votes(votes)
    assert result['direcao'] == "CALL"
    assert result['confianca'] >= 75

def test_consensus_with_majority_put():
    votes = [
        {"direcao": "CALL", "confianca": 60},
        {"direcao": "PUT", "confianca": 90},
        {"direcao": "PUT", "confianca": 85},
        {"direcao": "PUT", "confianca": 85},
        {"direcao": "CALL", "confianca": 70},
        {"direcao": "NEUTRO", "confianca": 50},
    ]
    consensus_engine = AuroraConsensusEngine()
    result = consensus_engine.aggregate_votes(votes)
    assert result['direcao'] == "PUT"
    assert result['confianca'] >= 80

def test_consensus_with_neutral_votes():
    votes = [
        {"direcao": "NEUTRO", "confianca": 50},
        {"direcao": "NEUTRO", "confianca": 50},
        {"direcao": "NEUTRO", "confianca": 50},
    ]
    consensus_engine = AuroraConsensusEngine()
    result = consensus_engine.aggregate_votes(votes)
    assert result['direcao'] == "NEUTRO"

def test_consensus_with_insufficient_votes():
    votes = [
        {"direcao": "CALL", "confianca": 60},
        {"direcao": "PUT", "confianca": 70},
    ]
    consensus_engine = AuroraConsensusEngine()
    result = consensus_engine.aggregate_votes(votes)
    assert result['direcao'] is None

def test_consensus_with_conflicting_votes():
    votes = [
        {"direcao": "CALL", "confianca": 60},
        {"direcao": "PUT", "confianca": 60},
        {"direcao": "CALL", "confianca": 70},
        {"direcao": "PUT", "confianca": 80},
    ]
    consensus_engine = AuroraConsensusEngine()
    result = consensus_engine.aggregate_votes(votes)
    assert result['direcao'] is None  # Expecting no consensus due to conflict


def test_three_valid_agents_require_unanimous_direction():
    engine = AuroraConsensusEngine()
    unanimous = engine.aggregate_votes([
        {"direcao": "CALL", "confianca": 80},
        {"direcao": "CALL", "confianca": 75},
        {"direcao": "CALL", "confianca": 85},
        {"direcao": "PUT", "confianca": 90, "dados_insuficientes": True},
    ])
    split = engine.aggregate_votes([
        {"direcao": "CALL", "confianca": 95},
        {"direcao": "CALL", "confianca": 95},
        {"direcao": "PUT", "confianca": 30},
    ])

    assert unanimous['direcao'] == 'CALL'
    assert split['direcao'] is None


def test_four_valid_agents_need_three_directional_votes_and_75_average():
    engine = AuroraConsensusEngine()
    approved = engine.aggregate_votes([
        {"direcao": "PUT", "confianca": 80},
        {"direcao": "PUT", "confianca": 75},
        {"direcao": "PUT", "confianca": 70},
        {"direcao": "CALL", "confianca": 90},
    ])
    low_confidence = engine.aggregate_votes([
        {"direcao": "PUT", "confianca": 70},
        {"direcao": "PUT", "confianca": 70},
        {"direcao": "PUT", "confianca": 70},
        {"direcao": "CALL", "confianca": 90},
    ])

    assert approved['direcao'] == 'PUT'
    assert approved['confianca'] == 75
    assert low_confidence['direcao'] is None


def test_neutral_consensus_cannot_pass_risk_guard():
    guard = RiskGuard()

    assert not guard.should_allow({"ativo": "BTC/USD", "direcao": "NEUTRO", "confianca": 100})
    assert not guard.validate_consensus({"direcao": "NEUTRO", "confianca": 100})


def test_risk_guard_requires_binary_market_and_confirmed_ninety_percent_payout():
    guard = RiskGuard()
    base = {"ativo": "BTC/USD", "mercado": "OPCOES_BINARIAS", "direcao": "CALL",
            "confianca": 90, "payout": .90}

    assert guard.should_allow(base)
    assert not guard.should_allow({**base, "payout": .899})
    assert not guard.should_allow({**base, "payout": None})
    assert not guard.should_allow({**base, "mercado": "SPOT"})


def test_all_ai_runtimes_use_the_required_consensus_thresholds():
    runtimes = [AIPipeline(), AgentRuntime(), build_ai_system()]

    assert all(runtime.consensus.min_votes_concordantes == 3 for runtime in runtimes)
    assert all(runtime.consensus.min_agentes_validos == 3 for runtime in runtimes)
    assert all(runtime.consensus.min_confianca == 75 for runtime in runtimes)