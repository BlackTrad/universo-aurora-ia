import asyncio

from backend.ai.orchestrator import PlanetaryAgentOrchestrator


class VoteAgent:
    name = 'groq'
    provider_key = 'groq'
    model = 'test-model'

    def __init__(self, vote=None, delay=0, error=None):
        self.vote = vote or {
            'direcao': 'CALL',
            'confianca': 80,
            'justificativa': 'Estrutura informada no pacote.',
            'evidencias': ['pre_sinal'],
            'alertas': [],
            'dados_insuficientes': False,
        }
        self.delay = delay
        self.error = error
        self.calls = 0

    async def analisar(self, data):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return self.vote


def test_orchestrator_validates_votes_and_reports_agent_status():
    invalid = VoteAgent(vote={
        'direcao': 'HOLD', 'confianca': 120, 'justificativa': 'inventado',
        'evidencias': [], 'alertas': [], 'dados_insuficientes': False,
    })
    orchestrator = PlanetaryAgentOrchestrator([VoteAgent(), invalid])

    votes = asyncio.run(orchestrator.run_agents({'ativo': 'BTC/USD'}))

    assert votes[0]['status'] == 'disponivel'
    assert votes[0]['agente'] == 'groq'
    assert votes[1]['status'] == 'erro'
    assert votes[1]['dados_insuficientes'] is True
    assert votes[1]['direcao'] == 'NEUTRO'


def test_orchestrator_times_out_individual_agent():
    orchestrator = PlanetaryAgentOrchestrator([VoteAgent(delay=0.05)], agent_timeout=0.01)

    vote = asyncio.run(orchestrator.run_agents({'ativo': 'BTC/USD'}))[0]

    assert vote['status'] == 'timeout'
    assert vote['dados_insuficientes'] is True


def test_orchestrator_preserves_provider_unavailable_and_rate_limit_states():
    class ProviderFailure(Exception):
        def __init__(self, status, retry_after=None):
            self.status = status
            self.retry_after = retry_after

    class FailingAgent(VoteAgent):
        def __init__(self, status, retry_after=None):
            super().__init__()
            self.status = status
            self.retry_after = retry_after

        async def analisar(self, data):
            raise ProviderFailure(self.status, self.retry_after)

    orchestrator = PlanetaryAgentOrchestrator([
        FailingAgent('indisponivel'), FailingAgent('rate_limit', 7),
    ])

    votes = asyncio.run(orchestrator.run_agents({'ativo': 'BTC/USD'}))

    assert [vote['status'] for vote in votes] == ['indisponivel', 'rate_limit']
    assert votes[1]['retry_after'] == 7


def test_orchestrator_caches_vote_for_same_closed_candles():
    agent = VoteAgent()
    orchestrator = PlanetaryAgentOrchestrator([agent])
    payload = {
        'ativo': 'BTC/USD',
        'timeframe': 'M5',
        'pre_sinal': {'direcao': 'CALL', 'estrategia': 'pa_engulfing'},
        'candles_m1': [{'t': 100}],
        'candles_m5': [{'t': 300}],
    }

    async def run_twice():
        await orchestrator.run_agents(payload)
        await orchestrator.run_agents(payload)

    asyncio.run(run_twice())

    assert agent.calls == 1
