"""Bollinger/RSI reversion strategy using closed candles only."""
from .helpers import atr, body_ratio, bollinger, closes, ema, rsi, score_result, valid_setup_candle

def evaluate(candles):
    if len(candles) < 35:
        return None, 'Histórico insuficiente para Bollinger(20), RSI(14) e ATR(14).'
    values = closes(candles); average_range = atr(candles)
    if average_range is None or candles[-1].h - candles[-1].l > 2.5 * average_range:
        return None, 'Volatilidade incompatível com a reversão por bandas.'
    previous = candles[-2]; last = candles[-1]
    previous_band = bollinger(values[:-1]); current_band = bollinger(values)
    if previous_band is None or current_band is None: return None, 'Bandas insuficientes.'
    _, _, upper = previous_band; lower, _, _ = previous_band
    fast = ema(values, 9); slow = ema(values, 21); momentum = rsi(values[:-1])
    if momentum is None: return None, 'RSI insuficiente.'
    lateral = abs(fast - slow) < .5 * average_range
    candidates = []
    if previous.c > upper and last.c < last.o and last.c <= current_band[2] and body_ratio(last) >= .5 and momentum >= 70:
        score = 75 + 15 * int(lateral) + 10 * int(valid_setup_candle(last, average_range))
        reasons = ['Candle N-1 fechou acima da banda superior Bollinger(20,2) (30 pts)',
                   'Candle N voltou para dentro em direção oposta com corpo ≥ 50% (25 pts)',
                   'RSI(14) do candle N-1 em extremo ≥ 70 (20 pts)']
        if lateral: reasons.append('Mercado sem tendência forte: distância EMA9/EMA21 < 0,5 ATR (15 pts)')
        if valid_setup_candle(last, average_range): reasons.append('Amplitude entre 0,3 e 1,5 ATR(14) (10 pts)')
        if score >= 85: candidates.append(score_result('PUT', score, reasons, last))
    if previous.c < lower and last.c > last.o and last.c >= current_band[0] and body_ratio(last) >= .5 and momentum <= 30:
        score = 75 + 15 * int(lateral) + 10 * int(valid_setup_candle(last, average_range))
        reasons = ['Candle N-1 fechou abaixo da banda inferior Bollinger(20,2) (30 pts)',
                   'Candle N voltou para dentro em direção oposta com corpo ≥ 50% (25 pts)',
                   'RSI(14) do candle N-1 em extremo ≤ 30 (20 pts)']
        if lateral: reasons.append('Mercado sem tendência forte: distância EMA9/EMA21 < 0,5 ATR (15 pts)')
        if valid_setup_candle(last, average_range): reasons.append('Amplitude entre 0,3 e 1,5 ATR(14) (10 pts)')
        if score >= 85: candidates.append(score_result('CALL', score, reasons, last))
    if len(candidates) != 1: return None, 'Sem reversão válida nas bandas com RSI extremo e candle de retorno.'
    return candidates[0], ''
