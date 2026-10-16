"""Regression coverage for signal-quality guards; no real broker or AI calls."""
import asyncio
from copy import deepcopy

from backend.ai.consensus import AuroraConsensusEngine
from backend.ai.orchestrator import PlanetaryAgentOrchestrator
from backend.ai.pipeline import AIPipeline


class RecordingAgent:
    name = 'Groq'
    provider_key = 'groq'
    model = 'test-model'
    role = 'price_action'

    def __init__(self):
        self.calls = 0

    async def analisar(self, payload):
        self.calls += 1
        return {'direcao': 'CALL', 'confianca': 82, 'justificativa': 'mock',
                'evidencias': [], 'alertas': [], 'dados_insuficientes': False}


def package():
    return {
        'ativo': 'BTC/USD', 'mercado': 'OPCOES_BINARIAS', 'timeframe': 'M5',
        'fonte': 'Deriv', 'source_identity': 'deriv:crypto:binary:public',
        'pre_sinal': {'direcao': 'CALL'}, 'payout': .90,
        'entrada_utc': '2026-10-15T10:05:00Z',
        'expiracao_utc': '2026-10-15T10:10:00Z',
        'indicadores': {'rsi': 60},
        'estrutura_smc': {'tendencia': 'alta'},
        'estrategias_backend': ['pa'],
        'filtros_backend': [{'name': 'broker', 'passed': True}],
        'candles_m1': [{'t': 100, 'o': 1, 'h': 2, 'l': 1, 'c': 1.4}],
        'candles_m5': [{'t': 300, 'o': 1, 'h': 2, 'l': 1, 'c': 1.4}],
    }


def test_cached_agent_vote_cannot_cross_payout_source_window_or_filter():
    agent = RecordingAgent()
    orchestrator = PlanetaryAgentOrchestrator([agent])
    async def run():
        original = package()
        await orchestrator.run_agents(original)
        # Capture metadata is not decision input, so a retry can use cache.
        await orchestrator.run_agents({**original, 'identificador_unico': 'new',
                                      'timestamp_captura': '2026-10-15T10:00:03Z'})
        assert agent.calls == 1
        for delta in ({'payout': .94}, {'source_identity': 'different-broker-feed'},
                      {'entrada_utc': '2026-10-15T10:06:00Z'},
                      {'filtros_backend': [{'name': 'broker', 'passed': False}]},
                      {'preco_atual': 999}):
            changed = {**original, **delta}
            await orchestrator.run_agents(changed)
        assert agent.calls == 6
        agent.model = 'new-model'
        await orchestrator.run_agents(original)
        assert agent.calls == 7
    asyncio.run(run())


def test_consensus_does_not_count_duplicate_agent_or_failed_agent():
    engine = AuroraConsensusEngine()
    original = {'agente': 'Groq', 'status': 'disponivel',
                'direcao': 'CALL', 'confianca': 99, 'dados_insuficientes': False}
    votes = [original, dict(original),
             {'agente': 'NVIDIA', 'status': 'disponivel', 'direcao': 'CALL',
              'confianca': 99, 'dados_insuficientes': False},
             {'agente': 'Gemini', 'status': 'timeout', 'direcao': 'CALL',
              'confianca': 99, 'dados_insuficientes': False}]
    decision = engine.aggregate_votes(votes)
    assert decision['direcao'] is None
    assert decision['agentes_validos'] == 2


def test_consensus_three_independent_healthy_votes_can_approve():
    votes = [{'agente': name, 'status': 'disponivel', 'direcao': 'PUT',
              'confianca': 80, 'dados_insuficientes': False}
             for name in ('Groq', 'NVIDIA', 'Gemini')]
    assert AuroraConsensusEngine().aggregate_votes(votes)['direcao'] == 'PUT'


def test_empty_ai_consensus_keeps_diagnostic_reason():
    payload = {
        'ativo': 'BTC/USD', 'mercado': 'OPCOES_BINARIAS', 'timeframe': 'M5',
        'expiracao_minutos': 5, 'timestamp_utc': '2026-10-15T12:00:00Z',
        'preco_atual': None, 'payout': .90, 'candles_m1': [], 'candles_m5': [],
        'indicadores': {'rsi': None},
        'estrutura_smc': {'tendencia': 'indisponivel'},
        'estrategias_backend': [], 'filtros_backend': [],
        'pre_sinal': {'direcao': 'CALL'},
    }
    result = asyncio.run(AIPipeline().process(payload))
    assert result['approved'] is False
    assert result['decision']['motivo'] == 'sem_votos'


def test_final_binary_proposal_blocks_lower_payout_and_preserves_broker_provenance():
    """Functional production flow, with mock Deriv and mock consensus; no HTTP."""
    from types import SimpleNamespace
    from app.config import Config
    from app.market import CandleBatch
    from app.service import Service
    from app.store import Store
    from tests.test_system import fixture

    class Broker:
        name = 'Deriv'
        identity = 'deriv:crypto:binary:public'
        assets = ['BTC/USD']
        timeframes = ['M1', 'M5']

        def __init__(self, last_payout):
            self.last_payout = last_payout
            self.checks = 0

        async def candles(self, *args, **kwargs):
            return CandleBatch(fixture(), self.identity)

        async def supports(self, *args):
            pass

        async def confirmed_payout(self, *args):
            self.checks += 1
            return .90 if self.checks == 1 else self.last_payout

    async def scenario(final_payout):
        store = Store(':memory:')
        broker = Broker(final_payout)
        now = fixture()[-1].t + 305
        config = Config(database=':memory:', asset_scope='crypto_binary',
                        assets=['BTC/USD'], strategy='candlestick_sr')
        service = Service(config, store, broker, SimpleNamespace(configured=False),
                          providers={'deriv': broker}, automatic_sources=True,
                          clock=lambda: now)

        async def fake_payload(*args, **kwargs):
            return SimpleNamespace(data={'test': 'safe'})

        async def fake_consensus(*args, **kwargs):
            return {'approved': True, 'decision': {'direcao': 'CALL', 'confianca': 90}, 'votes': []}

        service.build_ai_payload = fake_payload
        service.analyze_with_ai = fake_consensus
        analysis = service.start(['BTC/USD'], 300, 'M5')
        assert analysis is not None
        await service.current_analysis_task
        signals = store.signals()
        checks = store.analyses()[0]['checks']
        assert broker.checks == 2, 'one proposal for agents and one before commit'
        if final_payout < .90:
            assert signals == []
            assert any(item.get('code') == 'payout_unavailable' for item in checks)
        else:
            assert len(signals) == 1
            signal = signals[0]
            assert signal['payout_pct'] == final_payout * 100
            assert signal['payout_kind'] == 'broker_proposal_verified'
            assert signal['broker_confirmed'] is False  # No order executed
            quote = signal['evidence']['binary_contract_final_check']
            assert quote['payout_pct_before'] == 90
            assert quote['payout_pct_final'] == final_payout * 100
            assert quote['kind'] == 'broker_proposal_read_only'
        store.close()

    asyncio.run(scenario(.85))
    asyncio.run(scenario(.95))
