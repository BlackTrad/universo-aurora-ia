import asyncio
from types import SimpleNamespace

import pytest

from app.config import Config
from app.market import Candle, CandleBatch
from app.service import Service
from app.store import Store
from app.telegram import caption, result_caption
from backend.integrations.backend_analysis_adapter import BackendAnalysisAdapter

def test_valid_data_format():
    adapter = BackendAnalysisAdapter()
    data = {
        "ativo": "BTC/USD",
        "mercado": "OPCOES_BINARIAS",
        "timeframe": "M5",
        "expiracao_minutos": 5,
        "timestamp_utc": "2023-10-01T12:00:00Z",
        "preco_atual": 50000,
        "payout": 0.90,
        "candles_m1": [],
        "candles_m5": [],
        "indicadores": {
            "rsi": 50,
            "ema_9": 50000,
            "ema_21": 49500,
            "macd": 0.5
        },
        "estrutura_smc": {
            "tendencia": "BULLISH",
            "bos": None,
            "choch": None,
            "mss": None,
            "liquidity_sweep": None
        },
        "estrategias_backend": [],
        "filtros_backend": [],
        "pre_sinal": {
            "direcao": "CALL",
            "estrategia": None,
            "justificativa": None
        }
    }
    result = adapter.validate_data(data)
    assert result is True

def test_missing_required_field():
    adapter = BackendAnalysisAdapter()
    data = {
        "mercado": "OPCOES_BINARIAS",
        "timeframe": "M5",
        "expiracao_minutos": 5,
        "timestamp_utc": "2023-10-01T12:00:00Z",
        "preco_atual": 50000,
        "payout": 0.90,
        "candles_m1": [],
        "candles_m5": [],
        "indicadores": {
            "rsi": 50,
            "ema_9": 50000,
            "ema_21": 49500,
            "macd": 0.5
        },
        "estrutura_smc": {
            "tendencia": "BULLISH",
            "bos": None,
            "choch": None,
            "mss": None,
            "liquidity_sweep": None
        },
        "estrategias_backend": [],
        "filtros_backend": [],
        "pre_sinal": {
            "direcao": "CALL",
            "estrategia": None,
            "justificativa": None
        }
    }
    with pytest.raises(ValueError):
        adapter.validate_data(data)

def test_invalid_data_type():
    adapter = BackendAnalysisAdapter()
    data = {
        "ativo": "BTC/USD",
        "mercado": "OPCOES_BINARIAS",
        "timeframe": "M5",
        "expiracao_minutos": "five",  # Invalid type
        "timestamp_utc": "2023-10-01T12:00:00Z",
        "preco_atual": 50000,
        "payout": 0.90,
        "candles_m1": [],
        "candles_m5": [],
        "indicadores": {
            "rsi": 50,
            "ema_9": 50000,
            "ema_21": 49500,
            "macd": 0.5
        },
        "estrutura_smc": {
            "tendencia": "BULLISH",
            "bos": None,
            "choch": None,
            "mss": None,
            "liquidity_sweep": None
        },
        "estrategias_backend": [],
        "filtros_backend": [],
        "pre_sinal": {
            "direcao": "CALL",
            "estrategia": None,
            "justificativa": None
        }
    }
    with pytest.raises(TypeError):
        adapter.validate_data(data)


def test_adapter_builds_package_only_from_explicit_backend_data():
    candle_m1 = {'t': 1_760_000_000, 'o': 1.1, 'h': 1.2, 'l': 1.0, 'c': 1.15, 'v': None}
    candle_m5 = {'t': 1_760_000_000, 'o': 1.1, 'h': 1.2, 'l': 1.0, 'c': 1.15, 'v': None}
    adapter = BackendAnalysisAdapter.from_backend(
        asset='BTC/USD', market='OPCOES_BINARIAS', source='Deriv', timeframe='M5',
        expiration_minutes=5, timestamp_utc='2026-10-14T12:00:00Z', price=1.15,
        payout=None, candles_m1=[candle_m1], candles_m5=[candle_m5],
        indicators={'rsi': 55.0, 'ema_9': None, 'ema_21': None, 'macd': None},
        structure={'regime': 'transicao', 'direction': None}, strategies=['pa_engulfing'],
        filters=[], candidate={'direction': 'CALL', 'strategy_resolved': 'pa_engulfing', 'reasons': ['padrão confirmado']})

    assert adapter.data['ativo'] == 'BTC/USD'
    assert adapter.data['fonte'] == 'Deriv'
    assert adapter.data['candles_m1'] == [candle_m1]
    assert adapter.data['indicadores']['ema_9'] is None
    assert adapter.data['estrutura_smc']['bos'] is None
    assert adapter.data['pre_sinal']['direcao'] == 'CALL'


def test_service_ai_package_uses_closed_m1_m5_from_same_source():
    now = 1_800_000_301

    def candles(interval, count=50):
        last = (now // interval) * interval - interval
        first = last - (count - 1) * interval
        return [Candle(first + i * interval, 100 + i * .01, 100.03 + i * .01,
                       99.99 + i * .01, 100.02 + i * .01) for i in range(count)]

    class Provider:
        name = 'Deriv'
        identity = 'deriv:forex:public'
        assets = ['EUR/USD']
        timeframes = ['M1', 'M5']

        async def candles(self, asset, interval=300, count=100):
            assert asset == 'EUR/USD'
            return CandleBatch(candles(interval, count), self.identity)

    store = Store(':memory:')
    config = Config(database=':memory:', asset_scope='forex', assets=['EUR/USD'])
    service = Service(config, store, Provider(), SimpleNamespace(configured=False), clock=lambda: now)
    rows_m5 = candles(300)
    candidate = {'direction': 'CALL', 'strategy_resolved': 'pa_engulfing', 'reasons': ['padrão confirmado']}

    package = asyncio.run(service.build_ai_payload(
        service.provider, 'EUR/USD', candidate, timeframe='M5', interval=300,
        closed=rows_m5, source_identity='deriv:forex:public', received_at=now,
        duration=300, entry_at=now + 60, payout=.90))

    assert len(package.data['candles_m1']) == 100
    assert len(package.data['candles_m5']) == 50
    assert package.data['fonte'] == 'Deriv'
    assert package.data['source_identity'] == 'deriv:forex:public'
    assert package.data['indicadores']['rsi'] is not None
    assert package.data['indicadores']['ema_9'] is not None
    assert package.data['indicadores']['ema_21'] is not None
    assert package.data['indicadores']['macd'] is not None
    assert package.data['preco_atual'] is None
    assert package.data['payout'] == .90
    store.close()


def test_service_snapshot_exposes_ai_status_and_runtime_decision():
    store = Store(':memory:')
    cfg = Config(database=':memory:')
    provider = SimpleNamespace(name='mock', ready=True, assets=['EUR/USD'], timeframes=['M5'])
    telegram = SimpleNamespace(configured=False)
    service = Service(cfg, store, provider, telegram, clock=lambda: 1000)

    snapshot = service.snapshot()
    assert 'ai' in snapshot
    assert snapshot['ai']['status'] == 'unavailable'

    payload = {
        'ativo': 'EUR/USD',
        'mercado': 'OPCOES_BINARIAS',
        'timeframe': 'M5',
        'expiracao_minutos': 5,
        'timestamp_utc': '2023-10-01T12:00:00Z',
        'preco_atual': 1.1034,
        'payout': 0.90,
        'candles_m1': [],
        'candles_m5': [],
        'indicadores': {'rsi': 62, 'ema_9': 1.1031, 'ema_21': 1.1019, 'macd': 0.2},
        'estrutura_smc': {'tendencia': 'BULLISH', 'bos': 1.0, 'choch': None, 'mss': 1.0, 'liquidity_sweep': None},
        'estrategias_backend': ['candlestick_sr'],
        'filtros_backend': ['volatilidade'],
        'pre_sinal': {'direcao': 'CALL', 'estrategia': 'candlestick_sr', 'justificativa': 'estrutura bullish'}
    }

    result = asyncio.run(service.analyze_with_ai(payload))
    assert result['approved'] is False
    assert service.ai_status['status'] == 'unavailable'


def test_telegram_caption_includes_ai_status():
    signal = {
        'asset': 'EUR/USD',
        'direction': 'CALL',
        'entry_at': 1730000000,
        'duration_seconds': 300,
        'ai_consensus': {
            'approved': True,
            'decision': {'direcao': 'CALL', 'confianca': 92},
            'votes': [{'agente':'Groq','status':'disponivel','direcao':'CALL','confianca':92,'dados_insuficientes':False}],
        }
    }

    text = caption(signal)
    assert 'IA' in text
    assert 'CALL' in text
    assert 'evidência 92%' in text
    assert '92%' in text

    result = result_caption({'result': 'WIN', 'asset': 'EUR/USD', 'direction': 'CALL', 'entry_at': 1730000000,
                            'expiry_at': 1730000180, 'duration_seconds': 300, 'state': 'encerrado',
                            'ai_consensus': {'approved': True,'decision': {'direcao': 'CALL', 'confianca': 96},
                                'votes': [{'agente':'Groq','status':'disponivel','direcao':'CALL','confianca':96,'dados_insuficientes':False}]}},
                           {'wins': 3, 'losses': 1, 'win_rate_pct': 75.0})
    assert 'IA' in result
    assert '96%' in result


def test_telegram_uses_signal_specific_consensus_over_global_status():
    signal = {
        'asset': 'BTC/USD', 'direction': 'CALL', 'entry_at': 1730000000, 'duration_seconds': 300,
            'ai_status': {'status': 'rejected', 'last_decision': {'direcao': 'PUT', 'confianca': 99}},
        'ai_consensus': {
            'approved': True,
            'decision': {'direcao': 'CALL', 'confianca': 84},
            'votes': [
                {'agente': name, 'status': 'disponivel', 'direcao': 'CALL', 'confianca': 84, 'dados_insuficientes': False}
                for name in ('Groq', 'NVIDIA', 'Nemotron', 'GLM')
            ] + [
                {'agente': name, 'status': 'disponivel', 'direcao': 'PUT', 'confianca': 80, 'dados_insuficientes': False}
                for name in ('Gemini', 'Kimi')
            ],
        },
    }

    text = caption(signal)

    assert 'Consenso IA: CALL · 4/6 · evidência 84%' in text
    assert 'PUT 99%' not in text