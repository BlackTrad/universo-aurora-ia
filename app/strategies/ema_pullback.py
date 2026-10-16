"""EMA9/EMA21 continuation strategy. All values use closed candles only."""
from .helpers import atr, body_ratio, closes, ema_series, score_result, valid_setup_candle, distance_growing

def evaluate(candles):
    if len(candles) < 30:
        return None, 'Histórico insuficiente para EMA9/EMA21 e ATR(14).'
    values = closes(candles); fast = ema_series(values, 9); slow = ema_series(values, 21)
    average_range = atr(candles)
    last = candles[-1]
    if average_range is None or last.h - last.l > 2.5 * average_range:
        return None, 'Volatilidade incompatível com o setup EMA.'
    candidates = []
    for direction in ('CALL', 'PUT'):
        up = direction == 'CALL'
        trend = all((fast[i] > slow[i]) if up else (fast[i] < slow[i]) for i in range(len(candles)-3, len(candles)))
        touch = last.l <= fast[-1] <= last.h
        no_cross = last.c >= slow[-1] if up else last.c <= slow[-1]
        confirmation = (last.c > last.o if up else last.c < last.o) and body_ratio(last) >= .5
        if not (trend and touch and no_cross and confirmation):
            continue
        gaps = [abs(fast[i] - slow[i]) for i in range(len(candles)-3, len(candles))]
        growing = distance_growing(gaps)
        controlled = valid_setup_candle(last, average_range)
        reasons = [f'EMA9 {">" if up else "<"} EMA21 nos últimos 3 candles fechados (30 pts)',
                   'Pullback tocou a EMA9 sem fechar contra a EMA21 (25 pts)',
                   f'Candle de confirmação de {"alta" if up else "baixa"} com corpo ≥ 50% (20 pts)']
        score = 75
        if growing: score += 15; reasons.append('Distância EMA9/EMA21 crescente (15 pts)')
        if controlled: score += 10; reasons.append('Amplitude entre 0,3 e 1,5 ATR(14) (10 pts)')
        if score >= 85: candidates.append(score_result(direction, score, reasons, last))
    if len(candidates) != 1:
        return None, 'Sem combinação válida de tendência EMA, pullback e confirmação.'
    return candidates[0], ''
