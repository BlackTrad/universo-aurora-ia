"""Third independent visit to a frozen level: research, never an order API.

Two strictly confirmed pivots define a horizontal support or an ascending
support line. The existing research evaluator mirrors this geometry for PUT.
All tolerances are predeclared hypotheses, not fitted performance claims.
"""
from dataclasses import asdict, dataclass
import math
from itertools import combinations

from .strategies.helpers import atr, closes, ema_series
from .strategies.regime import classify

VERSION = 'third-touch-1.0.0'


@dataclass(frozen=True)
class Rules:
    lookback_bars: int = 80
    pivot_sides: int = 2
    minimum_anchor_gap: int = 5
    minimum_visit_gap: int = 3
    tolerance_atr: float = .15
    minimum_departure_atr: float = .75
    minimum_departure_close_atr: float = .35
    maximum_penetration_atr: float = .35
    minimum_rise_atr: float = .25
    minimum_slope_atr_per_bar: float = .01
    maximum_slope_atr_per_bar: float = .30
    maximum_wait_bars: int = 20
    close_tolerance_atr: float = .15
    minimum_range_atr: float = .30
    maximum_range_atr: float = 1.50
    minimum_room_atr: float = .50


RULES = Rules()


def snapshot():
    return {'version': VERSION, 'parameters': asdict(RULES),
            'operational_enabled': False, 'performance_validated': False}


def invalidation_limit(line, timestamp):
    """Project only the sealed two-anchor line; never fit it to new quotes."""
    required = ('reference_at', 'reference_price', 'slope_per_second', 'margin')
    if (not isinstance(line, dict) or line.get('side') not in ('support', 'resistance')
            or any(type(line.get(k)) not in (int, float) or not math.isfinite(line[k]) for k in required)
            or type(timestamp) not in (int, float) or not math.isfinite(timestamp)
            or line['reference_at'] <= 0 or line['reference_price'] <= 0 or line['margin'] <= 0):
        raise ValueError('Linha experimental de invalidação inválida.')
    level = line['reference_price'] + line['slope_per_second'] * (timestamp - line['reference_at'])
    limit = level - line['margin'] if line['side'] == 'support' else level + line['margin']
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError('Projeção experimental inválida.')
    return limit


def pattern(rows, average, audit):
    # Lazy imports avoid a module cycle and reuse the same causal pivot/room rules.
    from .experiments import _check, _space, pivots

    before = rows[:-1]
    touch = rows[-1]
    interval = touch.t - rows[-2].t
    swings = pivots(before, RULES.pivot_sides)
    lows = [p for p in swings['low'] if p['index'] >= max(20, len(rows) - RULES.lookback_bars)]
    fast = ema_series(closes(before), 9)[-1]
    slow = ema_series(closes(before), 21)[-1]
    if not _check(audit, 'compatible_pressure', classify(before) != 'tendencial' or fast >= slow,
                  'Suporte/LTA não autoriza CALL contra pressão tendencial de baixa; espelho para PUT.'):
        return None
    matches = []
    rejected = []
    for first, second in combinations(lows, 2):
        gap = second['index'] - first['index']
        known_index = second['index'] + RULES.pivot_sides
        defined_at = rows[known_index].t + interval
        anchor_atr = atr(rows[:known_index + 1])
        if (gap < RULES.minimum_anchor_gap or anchor_atr is None or anchor_atr <= 0
                or defined_at > touch.t
                or len(rows) - 1 - second['index'] > RULES.maximum_wait_bars):
            continue
        tolerance = RULES.tolerance_atr * anchor_atr
        rise = second['price'] - first['price']
        if abs(rise) <= tolerance:
            kind = 'suporte'
            reference = (first['price'] + second['price']) / 2
            slope = 0.0
        elif (rise >= RULES.minimum_rise_atr * anchor_atr
              and RULES.minimum_slope_atr_per_bar <= rise / gap / anchor_atr <= RULES.maximum_slope_atr_per_bar):
            kind = 'LTA'
            reference = second['price']
            slope = rise / (second['at'] - first['at'])
        else:
            continue

        def level_at(t):
            return reference + slope * (t - second['at'])

        # A prior pivot already on this line makes the nominated visit at least
        # fourth in the available window; do not relabel the last two anchors.
        if any(p['index'] < first['index'] and abs(p['price'] - level_at(p['at'])) <= tolerance for p in lows):
            rejected.append('earlier_visit'); continue
        between = rows[first['index'] + 1:second['index']]
        departed = any(c.h - level_at(c.t) >= RULES.minimum_departure_atr * anchor_atr
                       and c.c - level_at(c.t) >= RULES.minimum_departure_close_atr * anchor_atr for c in between)
        if not departed:
            rejected.append('anchors_not_independent'); continue
        margin = RULES.maximum_penetration_atr * anchor_atr
        if any(c.c < level_at(c.t) - margin or c.l < level_at(c.t) - margin
               for c in rows[first['index']:]):
            rejected.append('line_broken'); continue

        armed = False
        last_contact = first['index']
        visits = [[last_contact, last_contact]]
        for i in range(first['index'] + 1, len(rows)):
            candle = rows[i]; level = level_at(candle.t)
            contact = candle.l <= level + tolerance and candle.h >= level - tolerance
            if contact:
                if armed and i - last_contact >= RULES.minimum_visit_gap:
                    visits.append([i, i])
                else:
                    visits[-1][1] = i
                last_contact = i
                armed = False
            elif (candle.h - level >= RULES.minimum_departure_atr * anchor_atr
                  and candle.c - level >= RULES.minimum_departure_close_atr * anchor_atr):
                armed = True
        if (len(visits) != 3 or not visits[1][0] <= second['index'] <= visits[1][1]
                or visits[2][0] != len(rows) - 1):
            rejected.append('third_touch_consumed' if len(visits) >= 3 else 'no_third_visit'); continue
        touch_level = level_at(touch.t)
        width = touch.h - touch.l
        if not (RULES.minimum_range_atr * anchor_atr <= width <= RULES.maximum_range_atr * anchor_atr
                and abs(touch.c - touch_level) <= RULES.close_tolerance_atr * anchor_atr):
            rejected.append('close_not_on_mark'); continue
        if not _space(before, touch.c, average):
            rejected.append('opposing_region_close'); continue
        matches.append({'region': [touch_level - tolerance, touch_level + tolerance],
                        'invalidation': [touch_level - margin], 'anchor_atr': anchor_atr,
                        'level_kind': kind, 'touch_count': 3,
                        'close_distance_atr':abs(touch.c-touch_level)/anchor_atr,
                        'entry_policy':'next_analysis_candle','target_entry_at':touch.t+interval,
                        'entry_window_end':touch.t+2*interval,
                        'line': {'first_anchor': {**first, 'confirmation_candle_at': first['confirmed_at']-interval},
                                 'second_anchor': {**second, 'confirmation_candle_at': second['confirmed_at']-interval}, 'defined_at': defined_at,
                                 'reference_at': second['at'], 'reference_price': reference,
                                 'slope_per_second': slope, 'tolerance': tolerance, 'side': 'support'},
                        'invalidation_line': {'reference_at': second['at'], 'reference_price': reference,
                                              'slope_per_second': slope, 'margin': margin, 'side': 'support'},
                        'sequence': {'first_touch_at': first['at'], 'second_touch_at': second['at'],
                                     'zone_defined_at': defined_at, 'third_touch_at': touch.t,
                                     'confirmation_at': touch.t, 'confirmation_closed_at': touch.t+interval}})
    _check(audit, 'frozen_two_anchor_line', len(matches) == 1,
           'Dois pivôs 2+2 anteriores, separados ≥5 candles e por afastamento ≥0,75 ATR; linha congelada antes do terceiro toque.')
    audit.append({'code': 'third_touch_diagnostics', 'passed': len(matches) == 1,
                  'detail': 'A própria vela da terceira visita deve fechar na marcação; nenhuma vela posterior confirma retrospectivamente.',
                  'rejections': sorted(set(rejected)), 'matching_lines': len(matches)})
    if len(matches) != 1:
        return None
    _check(audit, 'third_independent_visit', True, 'Exatamente terceira visita na janela disponível, nunca três candles adjacentes.')
    _check(audit, 'third_candle_closes_on_mark', True, 'N fecha a até 0,15 ATR da marcação congelada; entrada de referência na abertura de N+1.')
    _check(audit, 'line_intact_and_room', True, 'Penetração máxima 0,35 ATR e espaço mínimo 0,5 ATR até região contrária conhecida.')
    return matches[0]
