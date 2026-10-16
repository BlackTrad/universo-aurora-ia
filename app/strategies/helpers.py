"""Indicadores causais pequenos, sem volume e sem candles futuros."""
import math
from ..indicators import atr as atr, rsi as rsi, rma as rma, true_range as true_range
__all__=['atr','rsi','rma','true_range','closes','ema','ema_series','body_ratio',
         'bollinger','valid_setup_candle','score_result','distance_growing']

def closes(candles):
    return [c.c for c in candles]

def ema(values, period):
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value
    return value

def ema_series(values, period):
    if len(values) < period:
        return []
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    result = [None] * (period - 1) + [value]
    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value
        result.append(value)
    return result

def body_ratio(candle):
    width = candle.h - candle.l
    return abs(candle.c - candle.o) / width if width > 0 else 0

def bollinger(values, period=20, deviations=2):
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((v - mean) ** 2 for v in window) / period
    sd = math.sqrt(variance)
    return mean - deviations * sd, mean, mean + deviations * sd

def valid_setup_candle(candle, average_range):
    return 0.3 * average_range <= candle.h - candle.l <= 1.5 * average_range

def score_result(direction, score, reasons, setup):
    return {'direction': direction, 'score': score, 'reasons': reasons, 'setup_candle': setup.t}
def distance_growing(gaps):
    """Preserve the original EMA setup's exact growth criterion."""
    return len(gaps) == 3 and gaps[-1] > gaps[0] and gaps[-1] >= gaps[-2]
