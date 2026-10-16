"""EMA9/21 crossover confirmed by MACD(12,26,9), using closed bars only."""
from .helpers import atr, body_ratio, closes, ema_series, score_result, valid_setup_candle


def macd_values(candles):
    values=closes(candles)
    e12,e26=ema_series(values,12),ema_series(values,26)
    macd=[a-b for a,b in zip(e12[25:],e26[25:])]
    return macd,ema_series(macd,9)


def evaluate(candles):
    if len(candles) < 40:
        return None, 'Histórico insuficiente para EMA9/21 e MACD(12,26,9).'
    values = closes(candles)
    fast, slow = ema_series(values, 9), ema_series(values, 21)
    macd, signal = macd_values(candles)
    if len(signal) < 2 or signal[-2] is None:
        return None, 'Sinal MACD ainda não calculável.'
    last = candles[-1]
    average = atr(candles)
    if average is None or last.h - last.l > 2.5 * average:
        return None, 'Volatilidade incompatível com cruzamento EMA/MACD.'
    candidates = []
    for direction in ('CALL', 'PUT'):
        up = direction == 'CALL'
        cross = fast[-2] <= slow[-2] and fast[-1] > slow[-1] if up else fast[-2] >= slow[-2] and fast[-1] < slow[-1]
        macd_ok = macd[-1] > signal[-1] and macd[-1] > macd[-2] if up else macd[-1] < signal[-1] and macd[-1] < macd[-2]
        confirmation = (last.c > last.o if up else last.c < last.o) and body_ratio(last) >= .5
        if not (cross and macd_ok and confirmation):
            continue
        controlled = valid_setup_candle(last, average)
        score = 85 + 10 * int(controlled)
        reasons = ['Cruzamento EMA9/EMA21 confirmado no último candle fechado (35 pts)',
                   'MACD(12,26,9) alinhado com a direção e inclinação (30 pts)',
                   'Candle direcional com corpo ≥ 50% (20 pts)']
        if controlled: reasons.append('Amplitude entre 0,3 e 1,5 ATR(14) (10 pts)')
        candidates.append(score_result(direction, score, reasons, last))
    if len(candidates) != 1:
        return None, 'Sem cruzamento EMA/MACD confirmado.'
    return candidates[0], ''
