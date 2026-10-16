"""Support/resistance pivot reaction, independent of SMC terminology."""
from .helpers import atr, body_ratio, score_result, valid_setup_candle

def evaluate(candles):
    if len(candles) < 35: return None, 'Histórico insuficiente para pivô dos últimos 20 candles e ATR.'
    previous = candles[-21:-1]; last = candles[-1]; average_range = atr(candles)
    if average_range is None or last.h - last.l > 2.5 * average_range: return None, 'Volatilidade incompatível com o repique.'
    tolerance = .1 * average_range; candidates = []
    low_level = min(c.l for c in previous); high_level = max(c.h for c in previous)
    for direction, level, support in (('CALL', low_level, True), ('PUT', high_level, False)):
        touches = sum(abs((c.l if support else c.h) - level) <= tolerance for c in previous)
        touches_ok = touches >= 2
        touches_signal = last.l <= level + tolerance if support else last.h >= level - tolerance
        away = last.c > level + tolerance if support else last.c < level - tolerance
        confirmation = (last.c > last.o if support else last.c < last.o) and body_ratio(last) >= .5
        rejection_wick = (min(last.o, last.c) - last.l >= .3 * (last.h-last.l)) if support else (last.h-max(last.o,last.c) >= .3 * (last.h-last.l))
        if not (touches_ok and touches_signal and away and confirmation and rejection_wick): continue
        # A meaningful tick is required; rounding every FX price to four
        # decimals would make this bonus effectively unconditional.
        tick = .01 if abs(level) >= 10 else .0001
        rounded = abs(level / tick - round(level / tick)) <= .05
        controlled = valid_setup_candle(last, average_range)
        score = 75 + 15 * int(rounded) + 10 * int(controlled)
        reasons = [f'Nível de {"suporte" if support else "resistência"} tocado pelo menos 2 vezes em 20 candles (30 pts)',
                   'Candle do sinal tocou o nível e fechou afastando-se, corpo ≥ 50% (25 pts)',
                   'Pavio na direção do nível ≥ 30% da amplitude (20 pts)']
        if rounded: reasons.append('Nível próximo de número redondo (15 pts)')
        if controlled: reasons.append('Amplitude entre 0,3 e 1,5 ATR(14) (10 pts)')
        if score >= 85: candidates.append(score_result(direction, score, reasons, last))
    if len(candidates) != 1: return None, 'Sem repique válido em suporte/resistência com toque duplo e rejeição.'
    return candidates[0], ''
