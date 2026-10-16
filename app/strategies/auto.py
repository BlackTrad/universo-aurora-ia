"""Route before setup evaluation; never fall back to another strategy."""
from .regime import classify, metrics
from .ema_pullback import evaluate as evaluate_ema_pullback
from .range_pivot import evaluate as evaluate_range_pivot

def evaluate(candles):
    regime = classify(candles)
    if regime == 'tendencial':
        resolved, evaluator = 'ema_pullback', evaluate_ema_pullback
    elif regime == 'lateral':
        resolved, evaluator = 'range_pivot', evaluate_range_pivot
    else:
        return None, 'Regime de mercado indefinido — sem tendência clara nem lateralização.'
    candidate, reason = evaluator(candles)
    if candidate is None:
        return None, f'Regime {regime}; estratégia {resolved}: {reason}'
    return {**candidate, 'strategy_resolved': resolved, 'regime': regime,
            'regime_metrics': metrics(candles),
            'reasons': [f'Regime {regime} detectado (EMA9/EMA21 vs ATR; sem pontos extras)'] + candidate['reasons']}, reason
