"""Causal regime selection; no setup evaluation or mutable state."""
from .helpers import atr, closes, ema_series, distance_growing

def metrics(candles):
    if len(candles) < 30:
        return None
    fast, slow = ema_series(closes(candles), 9), ema_series(closes(candles), 21)
    gaps = [abs(fast[i] - slow[i]) for i in range(len(candles)-3, len(candles))]
    ranges = [atr(candles[:i+1]) for i in range(len(candles)-3, len(candles))]
    if any(value is None or value <= 0 for value in ranges):
        return None
    distances = [gap / value for gap, value in zip(gaps, ranges)]
    return {'distances': distances, 'gaps': gaps, 'growing': distance_growing(gaps)}

def classify(candles):
    values = metrics(candles)
    if values is None:
        return None
    if values['distances'][-1] >= .6 and values['growing']:
        return 'tendencial'
    if all(distance < .4 for distance in values['distances']):
        return 'lateral'
    return None
