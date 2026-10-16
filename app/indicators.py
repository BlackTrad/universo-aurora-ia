"""Wilder indicators shared by all engines, without registry dependencies."""
import math

def true_range(candle, previous):
    return max(candle.h - candle.l, abs(candle.h - previous.c), abs(candle.l - previous.c))

def rma(values, period):
    if period < 1 or len(values) < period:
        return None
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = (value * (period - 1) + item) / period
    return value

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    values = [true_range(c, p) for p, c in zip(candles, candles[1:])]
    value = rma(values, period)
    return value if math.isfinite(value) and value > 0 else None

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    changes = [b - a for a, b in zip(values, values[1:])]
    gains = rma([max(x, 0) for x in changes], period)
    losses = rma([max(-x, 0) for x in changes], period)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - 100 / (1 + gains / losses)

