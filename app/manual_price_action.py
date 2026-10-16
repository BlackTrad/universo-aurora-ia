"""Closed-OHLC translations of the user's manual, with original frozen regions.

The book supplies illustrated hypotheses, not validated performance. Every
engineering threshold is declared here and in the rule snapshot. No SMC, order
API, unclosed bar, future pivot, invented volume or future outcome is used.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from statistics import median

from .market import Candle, valid_candle
from .indicators import atr


def body_ratio(candle):
    return abs(candle.c-candle.o)/(candle.h-candle.l) if candle.h > candle.l else 0

VERSION = '1.26.11-manual-1'
SOURCE = {'title': 'O Manual do Trader Renomado 2.0', 'pages': 83,
          'scope': 'Price Action para opções binárias; hipóteses não validadas'}

# Variations share evidence and do not count as independent confirmation votes.
CATALOG = {
    'pa_harami': ('Harami em região', [33], 'reversal'),
    'pa_engulfing': ('Engolfo em região', [34], 'reversal'),
    'pa_engulfing_three': ('Engolfo de duas / três velas', [34], 'reversal'),
    'pa_hammer': ('Martelo / estrela cadente', [35], 'reversal'),
    'pa_star': ('Estrela da manhã / tarde', [36], 'reversal'),
    'pa_v_range': ('Estrutura em V na lateralização', [37, 38], 'reversal'),
    'pa_v_micro': ('Estrutura em V com microtendência', [39], 'reversal'),
    'pa_v_after_trend': ('Estrutura em V após tendência', [40], 'reversal'),
    'pa_restricted_zone': ('Zona restrita', [41, 42, 43, 44], 'reversal'),
    'pa_pullback_micro': ('Pullback / throwback com microtendência', [45], 'retest'),
    'pa_pullback_harami': ('Pullback / throwback com Harami', [46], 'retest'),
    'pa_breakout_50': ('Rompimento de 50% e reteste', [47, 48], 'retest'),
    'pa_breakout_90': ('Rompimento de 90% e reteste', [49], 'retest'),
    'pa_flow_zone': ('Fluxo após padrão em região', [50, 51, 52, 53, 54], 'flow'),
    'pa_flow_micro': ('Fluxo com microtendência', [55], 'flow'),
    'pa_flow_recovery': ('Fluxo com retomada e confirmação', [56, 57], 'flow'),
    'pa_flow_continuation': ('Fluxo de continuidade até a região', [58, 59], 'flow'),
    'pa_flow_compression': ('Fluxo no rompimento de 5% da vela', [60], 'breakout'),
    'pa_compression_breakout': ('Afunilamento, rompimento e reteste', [61], 'retest'),
    'pa_breakout_pressure': ('Rompimento com pressão da microtendência', [62, 63, 65], 'breakout'),
    'pa_breakout_lateral': ('Rompimento após lateralização próxima', [64], 'breakout'),
    'pa_false_breakout': ('Falso rompimento e retorno', [66, 67], 'reversal'),
    'pa_micro_second': ('Segundo toque da microtendência', [68, 69], 'reversal'),
    'pa_micro_third': ('Terceiro toque da microtendência', [68, 69], 'reversal'),
    'pa_micro_exhaustion': ('Rompimento da microtendência enfraquecida', [70], 'breakout'),
    'pa_battle_reversal': ('Reversão na zona de briga', [71, 72, 73, 74], 'reversal'),
    'pa_wick_retracement': ('Retração com quatro pavios em simetria', [75], 'reversal'),
    'pa_head_shoulders': ('OCO / OCO invertido com reteste', [76, 77], 'retest'),
    'pa_double_extreme': ('Topo / fundo duplo com reteste', [78, 79, 80], 'retest'),
}


@dataclass(frozen=True)
class Rules:
    pivot_sides: int = 2
    lookback: int = 70
    minimum_candles: int = 40
    zone_tolerance_atr: float = .15
    minimum_visit_gap: int = 3
    maximum_wick_body_ratio: float = .10
    strong_body_ratio: float = .70
    confirmation_body_ratio: float = .45
    minimum_room_atr: float = .50
    restricted_minimum_absence: int = 13
    restricted_minimum_departure_atr: float = 3.0
    v_minimum_gap: int = 13
    v_maximum_gap: int = 30
    v_minimum_departure_atr: float = 1.5
    breakout_50_minimum: float = .45
    breakout_50_maximum: float = .55
    breakout_90_minimum: float = .85
    relief_minimum_candles: int = 4
    retest_maximum_candles: int = 10
    symmetry_minimum_candles: int = 4
    symmetry_wick_ratio: float = .35
    symmetry_tolerance_atr: float = .15
    minimum_breakout_fraction: float = .05
    maximum_extension_atr: float = 1.0
    maximum_confirmation_range_atr: float = 2.5


RULES = Rules()


def snapshot():
    value = {'version': VERSION, 'source': SOURCE, 'parameters': asdict(RULES),
             'rule_origin': 'PDF + explicit engineering definitions; no performance claim',
             'percentage_basis': 'portion of the real body beyond the frozen level; not Fibonacci',
             'wick_tolerance': '10% of real body; engineering tolerance for illustrated no-wick bars',
             'timing': 'enter within the next analysis candle, with required manual lead; never roll expired window',
             'interference_pages': [48, 81, 82], 'source_pages_are_pdf_pages': True,
             'independent_votes': False, 'smc_active': False}
    value['sha256'] = sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def pivots(rows):
    """Two bars on both sides must already have closed."""
    found = []
    for i in range(2, len(rows) - 2):
        neighbors = rows[i-2:i] + rows[i+1:i+3]
        for kind, price in (('low', rows[i].l), ('high', rows[i].h)):
            if all(price < c.l if kind == 'low' else price > c.h for c in neighbors):
                found.append({'index': i, 'kind': kind, 'price': price, 'at': rows[i].t,
                              'confirmed_at': rows[i+2].t + rows[i+2].t-rows[i+1].t})
    return sorted(found, key=lambda p: (p['index'], p['kind']))


def structural_context(rows):
    ps = pivots(rows)
    highs, lows = ([p for p in ps if p['kind'] == kind] for kind in ('high', 'low'))
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1]['price'] > highs[-2]['price'] and lows[-1]['price'] > lows[-2]['price']:
            return {'regime': 'tendencial', 'direction': 'CALL'}
        if highs[-1]['price'] < highs[-2]['price'] and lows[-1]['price'] < lows[-2]['price']:
            return {'regime': 'tendencial', 'direction': 'PUT'}
        return {'regime': 'lateral', 'direction': None}
    return {'regime': 'transicao', 'direction': None}


def _bull(c, minimum=None):
    return c.c > c.o and body_ratio(c) >= (RULES.confirmation_body_ratio if minimum is None else minimum)


def _wick(c):
    return min(c.o, c.c)-c.l


def _pattern(key, rows):
    if len(rows) < 4:
        return False
    last, previous = rows[-1], rows[-2]
    if key == 'pa_harami':
        return (previous.c < previous.o and _bull(last) and previous.c <= last.o < last.c <= previous.o
                and abs(previous.c-previous.o) >= 1.5*abs(last.c-last.o))
    if key == 'pa_engulfing':
        return previous.c < previous.o and _bull(last) and last.o <= previous.c and last.c >= previous.o
    if key == 'pa_engulfing_three':
        return any(_bull(last) and all(c.c < c.o for c in prior)
                   and all(b.c < a.c for a,b in zip(prior, prior[1:]))
                   and last.o <= min(c.c for c in prior) and last.c >= max(c.o for c in prior)
                   for prior in (rows[-4:-1],rows[-3:-1]))
    if key == 'pa_hammer':
        body = abs(last.c-last.o)
        return last.c > last.o and body > 0 and _wick(last) >= 2*body and last.h-last.c <= body
    if key == 'pa_star':
        first, middle = rows[-3], rows[-2]
        return (first.c < first.o and body_ratio(first) >= .6 and body_ratio(middle) <= .25
                and max(middle.o,middle.c) <= (first.o+first.c)/2 and _bull(last)
                and last.c > (first.o+first.c)/2)
    return False


PATTERNS = tuple(CATALOG)[:5]


def _visits(rows, level, tolerance):
    visits, touching = [], False
    for i,c in enumerate(rows):
        current = c.l <= level+tolerance and c.h >= level-tolerance
        if current and not touching and (not visits or i-visits[-1] >= RULES.minimum_visit_gap):
            visits.append(i)
        touching = current
    return visits


def _zones(before, average):
    tolerance = RULES.zone_tolerance_atr*average
    groups = []
    for pivot in pivots(before):
        c = before[pivot['index']]
        # Body zones and wick zones remain distinct, as illustrated on page 73.
        body = min(c.o,c.c) if pivot['kind'] == 'low' else max(c.o,c.c)
        for price, basis in ((body, 'body'), (pivot['price'], 'wick')):
            group = next((g for g in groups if g['kind'] == pivot['kind'] and g['basis'] == basis
                          and abs(g['level']-price) <= tolerance), None)
            if group is None:
                group = {'level': price, 'prices': [], 'anchors': [], 'kind': pivot['kind'], 'basis': basis}
                groups.append(group)
            group['prices'].append(price)
            group['anchors'].append(pivot)
            group['level'] = median(group['prices'])
    for group in groups:
        group['visits'] = _visits(before, group['level'], tolerance)
        group['start_at'] = group['anchors'][0]['at']
        group['known_at'] = max(p['confirmed_at'] for p in group['anchors'])
        group['zone_id'] = f"{group['basis']}:{group['kind']}:{group['start_at']}"
    return groups


def _zone_rejection(rows, zones, average, minimum_visits=1):
    last = rows[-1]
    tolerance = RULES.zone_tolerance_atr*average
    matches = [z for z in zones if z['kind'] == 'low' and len(z['visits']) >= minimum_visits
               and abs(last.l-z['level']) <= tolerance and last.c > z['level']
               and last.c-z['level'] <= RULES.maximum_extension_atr*average]
    return min(matches, key=lambda z:(abs(last.l-z['level']), z['basis'] != 'body')) if matches else None


def _line_at(line, at):
    return line['reference_price'] + (at-line['reference_at'])*line['slope_per_second']


def _micro(rows, average, *, breaking=False, touch_number=None):
    before = rows[:-1]
    kind = 'high' if breaking else 'low'
    ps = [p for p in pivots(before) if p['kind'] == kind][-7:]
    tolerance = RULES.zone_tolerance_atr*average
    matches = []
    for i,first in enumerate(ps):
        for second in ps[i+1:]:
            difference = second['price']-first['price']
            if second['index']-first['index'] < 3 or (difference >= 0 if breaking else difference <= 0):
                continue
            line = {'reference_at': first['at'], 'reference_price': first['price'],
                    'slope_per_second': difference/(second['at']-first['at']),
                    'first_at': first['at'], 'second_at': second['at'], 'kind':kind,
                    'known_at': second['confirmed_at']}
            intact = all(c.c <= _line_at(line,c.t)+tolerance if breaking
                         else c.c >= _line_at(line,c.t)-tolerance for c in before[second['index']+1:])
            if not intact:
                continue
            visits, touching = [], False
            for index in range(first['index']+3, len(rows)):
                c = rows[index]
                contact = abs((c.h if breaking else c.l)-_line_at(line,c.t)) <= tolerance
                if contact and not touching and (not visits or index-visits[-1] >= 3):
                    visits.append(index)
                touching = contact
            line.update(visits=visits, level=_line_at(line,rows[-1].t))
            if breaking:
                yes = len([v for v in visits if v < len(rows)-1]) >= 3 and _bull(rows[-1]) and rows[-1].c > line['level']+tolerance
            else:
                reaction = _bull(rows[-1]) or _pattern('pa_hammer',rows)
                yes = bool(visits and visits[-1] == len(rows)-1 and reaction and rows[-1].c > line['level'])
            if yes and (touch_number is None or len(visits) == touch_number):
                matches.append(line)
    return max(matches, key=lambda line:(line['known_at'],len(line['visits']))) if matches else None


def _geometry(zone, *, last, average, entry_kind='region', anchors=(), variant=None):
    level = zone['level']
    tolerance = RULES.zone_tolerance_atr*average
    return {'level': level, 'region': [level-tolerance, level+tolerance],
            'invalidation': level-tolerance, 'zone_id': zone.get('zone_id'),
            'zone_basis': zone.get('basis','frozen_price'), 'start_at': zone.get('start_at',last.t),
            'known_at': zone.get('known_at',last.t), 'visits': len(zone.get('visits',[])),
            'anchors': list(anchors) or [p['at'] for p in zone.get('anchors',[])],
            'entry_kind': entry_kind, 'variant':variant}


def _room(rows, zones, geometry, average):
    price = rows[-1].c
    obstacles = [z['level'] for z in zones if z.get('zone_id') != geometry.get('zone_id')
                 and z['level'] > price]
    distance = min(obstacles)-price if obstacles else None
    return distance is None or distance >= RULES.minimum_room_atr*average, distance


def _fraction(candle, level):
    return (candle.c-level)/(candle.c-candle.o) if candle.c > candle.o and candle.o <= level < candle.c else None


def _break_strong(candle, level):
    body = candle.c-candle.o
    return (body > 0 and _bull(candle,RULES.strong_body_ratio)
            and candle.h-candle.c <= RULES.maximum_wick_body_ratio*body and _fraction(candle,level) is not None)


def _retest(key, rows, zones, average):
    last = rows[-1]
    if not _bull(last):
        return None
    tolerance = RULES.zone_tolerance_atr*average
    for index in range(len(rows)-2,max(5,len(rows)-RULES.retest_maximum_candles-2),-1):
        breaking = rows[index]
        for zone in zones:
            level = zone['level']
            if zone['known_at'] > breaking.t or not _break_strong(breaking,level):
                continue
            ratio = _fraction(breaking,level)
            relief = rows[index+1:-1]
            if key == 'pa_breakout_50' and not RULES.breakout_50_minimum <= ratio <= RULES.breakout_50_maximum:
                continue
            if key == 'pa_breakout_90' and ratio < RULES.breakout_90_minimum:
                continue
            if ratio >= RULES.breakout_90_minimum:
                if len(relief) < RULES.relief_minimum_candles:
                    continue
                if any(abs(c.c-c.o) > .65*(breaking.c-breaking.o) for c in relief):
                    continue
            elif len(relief) > 4:
                continue
            if any(c.c < level-tolerance for c in relief):
                continue
            if any(c.l <= level+tolerance for c in relief):
                # Already retested: do not manufacture a later first return.
                continue
            if not (level-tolerance <= last.l <= level+tolerance and last.c > level):
                continue
            if key == 'pa_pullback_harami' and not _pattern('pa_harami',rows[:index]):
                continue
            if key == 'pa_pullback_micro' and _micro(rows,average) is None:
                continue
            if key == 'pa_compression_breakout' and (
                    not _compression(rows[:index+1],zone,atr(rows[:index]) or average)
                    or _micro(rows,average) is None):
                continue
            geometry = _geometry(zone,last=last,average=average,entry_kind='retest',anchors=[breaking.t,last.t])
            geometry.update(breakout_at=breaking.t,breakout_fraction=ratio,relief_candles=len(relief))
            return geometry
    return None


def _compression(rows, zone, average):
    before = rows[-10:-1]
    level = zone['level']
    near = sum(abs(c.h-level) <= .35*average for c in before)
    early = min(c.l for c in before[:4]); late = min(c.l for c in before[-4:])
    return near >= 2 and late > early+.35*average and all(c.c <= level+.15*average for c in before)


def _breakout(key, rows, zones, average):
    last = rows[-1]
    if not _bull(last):
        return None
    for zone in zones:
        level = zone['level']; fraction = _fraction(last,level)
        if fraction is None or fraction < RULES.minimum_breakout_fraction:
            continue
        if rows[-2].c > level or last.c-level > RULES.maximum_extension_atr*average:
            continue
        compression = _compression(rows,zone,average)
        if key in ('pa_flow_compression','pa_compression_breakout') and not compression:
            continue
        if key == 'pa_breakout_pressure':
            ps = [p for p in pivots(rows[:-1]) if p['kind'] == 'low']
            if len(ps) < 2 or ps[-1]['price'] <= ps[-2]['price'] or not compression:
                continue
        if key == 'pa_breakout_lateral':
            near = rows[-7:-1]
            if max(c.h for c in near)-min(c.l for c in near) > 2*average or sum(abs(c.h-level)<.35*average for c in near) < 2:
                continue
        geometry = _geometry(zone,last=last,average=average,entry_kind='continuation',anchors=[last.t])
        geometry.update(breakout_fraction=fraction,compression=compression)
        return geometry
    return None


def _flow(key, rows, zones, average):
    last = rows[-1]
    if not _bull(last,.55) or last.c <= rows[-2].h:
        return None
    if key in ('pa_flow_zone','pa_flow_micro'):
        matched = next((p for p in PATTERNS if _pattern(p,rows[:-1])),None)
        zone = _zone_rejection(rows[:-1],zones,average)
        if matched is None or zone is None or (key == 'pa_flow_micro' and _micro(rows[:-1],average) is None):
            return None
    elif key == 'pa_flow_recovery':
        uncertain = rows[-5:-2]
        if sum(body_ratio(c) <= .35 and max(_wick(c),c.h-max(c.o,c.c)) >= .35*(c.h-c.l) for c in uncertain) < 2 or not _bull(rows[-2],.55):
            return None
        zone = _zone_rejection(rows[:-2],zones,average)
        matched = 'retomada + confirmação fechada'
    else:
        prior = rows[-4:-1]
        if not (body_ratio(prior[-2]) <= .35 and _bull(prior[-1]) and prior[-1].c > prior[-2].h):
            return None
        targets = [z for z in zones if z['level'] > last.c and z['level']-last.c <= 2*average]
        zone = min(targets,key=lambda z:z['level'],default=None)
        matched = 'continuidade com região-alvo próxima'
    if zone is None:
        return None
    # The entry evidence is the completed continuation, not a fictitious return.
    geometry = _geometry({'level': rows[-2].h,'start_at':rows[-2].t,'known_at':rows[-1].t,
                          'zone_id':zone['zone_id']},last=last,average=average,entry_kind='continuation',
                         anchors=[rows[-2].t,last.t],variant=matched)
    geometry.update(origin_region=zone['level'],origin_region_at=zone['start_at'])
    return geometry


def _large_shapes(key, rows, average):
    """Confirmed shape, a distinct neckline break, then a distinct retest."""
    before = rows[:-2]; ps = pivots(before)
    tolerance = RULES.zone_tolerance_atr*average
    lows = [p for p in ps if p['kind'] == 'low']
    if key == 'pa_head_shoulders':
        for left,head,right in zip(lows,lows[1:],lows[2:]):
            highs1 = [p for p in ps if p['kind']=='high' and left['index'] < p['index'] < head['index']]
            highs2 = [p for p in ps if p['kind']=='high' and head['index'] < p['index'] < right['index']]
            if not highs1 or not highs2 or abs(left['price']-right['price']) > .5*average or head['price'] >= min(left['price'],right['price'])-.5*average:
                continue
            a,b = max(highs1,key=lambda p:p['price']),max(highs2,key=lambda p:p['price'])
            slope = (b['price']-a['price'])/(b['at']-a['at'])
            neck = lambda t:a['price']+slope*(t-a['at'])
            shape = [left,head,right,a,b]
            found = _shape_retest(rows,neck,tolerance,average,shape,head['price'])
            if found:
                found['neckline'] = {'reference_at':a['at'],'reference_price':a['price'],'slope_per_second':slope}
                return found
    else:
        for first,second in zip(lows,lows[1:]):
            if not 4 <= second['index']-first['index'] <= 30 or abs(first['price']-second['price']) > .3*average:
                continue
            between = [p for p in ps if p['kind']=='high' and first['index'] < p['index'] < second['index']]
            if not between:
                continue
            top = max(between,key=lambda p:p['price'])
            if top['price']-max(first['price'],second['price']) < average:
                continue
            # A double bottom must follow a completed downward displacement.
            prior = before[max(0,first['index']-15):first['index']]
            if not prior or prior[0].c-first['price'] < 2*average:
                continue
            found = _shape_retest(rows,lambda _:top['price'],tolerance,average,[first,second,top],min(first['price'],second['price']))
            if found:
                return found
    return None


def _shape_retest(rows,neck,tolerance,average,shape,extreme):
    previous,last = rows[-2:]
    if (previous.c <= neck(previous.t)+tolerance or rows[-3].c > neck(rows[-3].t)+tolerance
            or not _bull(previous) or not _bull(last) or abs(last.l-neck(last.t)) > tolerance or last.c <= neck(last.t)):
        return None
    geometry = _geometry({'level':neck(last.t),'start_at':min(p['at'] for p in shape),
                          'known_at':max(p['confirmed_at'] for p in shape)},last=last,average=average,
                         entry_kind='retest',anchors=[p['at'] for p in shape]+[previous.t,last.t])
    geometry.update(shape_extreme=extreme,breakout_at=previous.t)
    return geometry


def _detect(key, rows, zones, average):
    last = rows[-1]
    if key in PATTERNS:
        zone = _zone_rejection(rows,zones,average)
        if zone and _pattern(key,rows):
            return _geometry(zone,last=last,average=average,anchors=[rows[-2].t,last.t],variant=key)
    elif key.startswith('pa_v_') or key == 'pa_restricted_zone':
        zone = _zone_rejection(rows,zones,average)
        if not zone or not (_bull(last) or (key == 'pa_restricted_zone' and any(_pattern(p,rows) for p in PATTERNS))):
            return None
        contacts = _visits(rows[:-1],zone['level'],.15*average)
        if not contacts:
            return None
        gap = len(rows)-1-contacts[-1]
        excursion = max(c.h for c in rows[contacts[-1]:-1])-zone['level']
        if key == 'pa_restricted_zone':
            if gap < RULES.restricted_minimum_absence or excursion < RULES.restricted_minimum_departure_atr*average:
                return None
            if not any(_pattern(pattern,rows) for pattern in PATTERNS):
                return None
        else:
            if not RULES.v_minimum_gap <= gap <= RULES.v_maximum_gap or excursion < RULES.v_minimum_departure_atr*average:
                return None
            regime = structural_context(rows[:-1])
            if key == 'pa_v_range' and regime['regime'] == 'tendencial':
                return None
            if key == 'pa_v_micro' and _micro(rows,average) is None:
                return None
            if key == 'pa_v_after_trend':
                start = contacts[0]
                earlier = rows[max(0,start-15):start+1]
                if len(contacts) < 2 or len(earlier) < 8 or abs(earlier[0].c-earlier[-1].c) < 3*average:
                    return None
        geometry = _geometry(zone,last=last,average=average,anchors=[rows[contacts[-1]].t,last.t])
        geometry.update(absence_candles=gap,departure_atr=excursion/average)
        return geometry
    elif key in ('pa_pullback_micro','pa_pullback_harami','pa_breakout_50','pa_breakout_90','pa_compression_breakout'):
        return _retest(key,rows,zones,average)
    elif key in ('pa_flow_zone','pa_flow_micro','pa_flow_recovery','pa_flow_continuation'):
        return _flow(key,rows,zones,average)
    elif key in ('pa_flow_compression','pa_breakout_pressure','pa_breakout_lateral'):
        return _breakout(key,rows,zones,average)
    elif key == 'pa_false_breakout':
        previous = rows[-2]
        if not _bull(last):
            return None
        for zone in zones:
            level = zone['level']
            if previous.c < level-.15*average and last.c > level and last.l < level and last.c-level <= average:
                geometry = _geometry(zone,last=last,average=average,anchors=[previous.t,last.t])
                geometry['invalidation'] = min(previous.l,last.l)-.15*average
                return geometry
    elif key in ('pa_micro_second','pa_micro_third','pa_micro_exhaustion'):
        required = 2 if key == 'pa_micro_second' else 3
        micro = _micro(rows,average,breaking=key == 'pa_micro_exhaustion',
                       touch_number=None if key == 'pa_micro_exhaustion' else required)
        if micro and (key == 'pa_micro_exhaustion' or len(micro['visits']) == required):
            zone = _zone_rejection(rows,zones,average)
            if key != 'pa_micro_exhaustion' and zone is None:
                return None
            geometry = _geometry(zone or {'level':micro['level'],'start_at':micro['first_at'],
                                 'known_at':micro['known_at']},last=last,average=average,
                                 entry_kind='continuation' if key == 'pa_micro_exhaustion' else 'region',
                                 anchors=[micro['first_at'],micro['second_at'],last.t])
            geometry.update(micro_line=micro,touch_number=len(micro['visits']))
            return geometry
    elif key == 'pa_battle_reversal':
        zone = _zone_rejection(rows,zones,average,minimum_visits=3)
        if zone and (any(_pattern(pattern,rows) for pattern in PATTERNS)
                     or (_bull(last) and _wick(last) >= .35*(last.h-last.l))):
            return _geometry(zone,last=last,average=average,anchors=[last.t])
    elif key == 'pa_wick_retracement':
        zone = _zone_rejection(rows,zones,average)
        if not zone or structural_context(rows[:-1])['regime'] == 'tendencial':
            return None
        previous = rows[-5:-1]
        aligned = all(abs(c.l-zone['level']) <= RULES.symmetry_tolerance_atr*average
                      and c.h > c.l and _wick(c) >= RULES.symmetry_wick_ratio*(c.h-c.l) for c in previous)
        if aligned and last.c > last.o and last.c > zone['level']:
            geometry = _geometry(zone,last=last,average=average,anchors=[c.t for c in previous]+[last.t])
            geometry['symmetric_candles'] = len(previous)
            return geometry
    elif key in ('pa_head_shoulders','pa_double_extreme'):
        return _large_shapes(key,rows,average)
    return None


def _restore_geometry(geometry, mirror):
    value = dict(geometry)
    for key in ('level','invalidation','origin_region','shape_extreme'):
        if isinstance(value.get(key),(int,float)):
            value[key] = mirror-value[key]
    value['region'] = sorted(mirror-price for price in value['region'])
    for key in ('micro_line','neckline'):
        if value.get(key):
            line = dict(value[key]);line['reference_price'] = mirror-line['reference_price']
            line['slope_per_second'] *= -1
            if 'level' in line:line['level'] = mirror-line['level']
            if line.get('kind') in ('low','high'):line['kind'] = 'high' if line['kind']=='low' else 'low'
            value[key] = line
    return value


def evaluate_all(candles, *, keys=None):
    keys = tuple(CATALOG) if keys is None else tuple(keys)
    if any(key not in CATALOG for key in keys):
        raise ValueError('Estratégia do manual desconhecida.')
    output = {key:(None,'Histórico fechado, válido e contínuo insuficiente.') for key in keys}
    if len(candles) < RULES.minimum_candles or any(not valid_candle(c) or type(c.t) is not int or c.t<=0 for c in candles):
        return output
    interval = candles[-1].t-candles[-2].t
    if (interval not in (60,120,180,300) or any(type(c.t) is not int or c.t<=0 or c.t%interval for c in candles)
            or any(b.t-a.t != interval for a,b in zip(candles,candles[1:]))):
        return output
    average = atr(candles[:-1])
    if average is None or not math.isfinite(average) or average <= 0 or candles[-1].h-candles[-1].l > RULES.maximum_confirmation_range_atr*average:
        return {key:(None,'Amplitude incompatível com a confirmação do manual.') for key in keys}
    rows = candles[-RULES.lookback:]
    mirror = 2*max(c.h for c in rows)
    if not math.isfinite(mirror):
        return {key:(None,'Amplitude numérica inválida.') for key in keys}
    candidates = {key:[] for key in keys}
    failures = {}
    for direction in ('CALL','PUT'):
        oriented = rows if direction == 'CALL' else [Candle(c.t,mirror-c.o,mirror-c.l,mirror-c.h,mirror-c.c,c.v) for c in rows]
        zones = _zones(oriented[:-4],average)
        for key in keys:
            geometry = _detect(key,oriented,zones,average)
            if geometry is None:
                continue
            room, distance = _room(oriented,zones,geometry,average)
            if not room:
                failures[key] = 'Outra região próxima limita o espaço do movimento (zona de interferência).'
                continue
            geometry['space_atr'] = distance/average if distance is not None else None
            if direction == 'PUT':
                geometry = _restore_geometry(geometry,mirror)
            label,pages,family = CATALOG[key]
            last = rows[-1]
            # Confirmation N is known at N's close. Entry must remain inside N+1.
            end = last.t+2*interval
            anchors = geometry.get('anchors',[]) or [last.t]
            movement_family = 'continuation' if family in ('flow','breakout') else 'region_reaction'
            movement = sha256(repr((movement_family,direction,geometry.get('zone_id'),
                                   sorted(anchors[:-1]),geometry.get('start_at'))).encode()).hexdigest()
            mandatory = [
                {'criterion':'Região e referências disponíveis antes da decisão','passed':True},
                {'criterion':label+' confirmado em candles fechados','passed':True},
                {'criterion':'Espaço até outra região respeitado','passed':True},
            ]
            contract = {'version':VERSION,'engine':key,'direction':direction,'confirmed_at':last.t+interval,
                        'confirmation_candle_at':last.t,'anchors':anchors,'movement_family':movement_family,
                        'levels':{'manual_region':geometry['level']},'geometry':geometry,
                        'invalidations':[{'level':'manual_region_boundary','price':geometry['invalidation'],
                                          'mode':'close_below' if direction == 'CALL' else 'close_above','inclusive':False}],
                        'valid_until':end-1,'validity':'inside_original_next_analysis_candle',
                        'entry_window_start':last.t+interval,'entry_window_end':end,
                        'entry_kind':geometry['entry_kind'],'manual_source_pages':pages}
            candidates[key].append({'direction':direction,'score':80,'score_kind':'mandatory_criteria; not probability',
                                    'reasons':[label+' com sequência obrigatória fechada (80 pts)',
                                               'Região original preservada; espaço de reação verificado'],
                                    'setup_candle':last.t,'strategy_resolved':key,'apex_setup':label,
                                    'manual':True,'manual_atr':average,'manual_source_pages':pages,'mandatory_criteria':mandatory,
                                    'manual_rules_version':VERSION,'manual_geometry':geometry,'setup_contract':contract,
                                    'setup_identity':movement,'regime':structural_context(rows)['regime']})
    for key in keys:
        found = candidates[key]
        output[key] = ((found[0],'') if len(found) == 1 else
                       (None,'CALL e PUT no mesmo padrão do manual; decisão ambígua.') if found else
                       (None,failures.get(key,'Sem sequência e contexto confirmados para '+CATALOG[key][0]+'.')))
    return output


def evaluate(key,candles):
    return evaluate_all(candles,keys=(key,))[key]


def catalog():
    return {'version':VERSION,'source':SOURCE,'rules':snapshot(),'strategies':[
        {'key':key,'label':spec[0],'pages':spec[1],'family':spec[2],'operational_enabled':True}
        for key,spec in CATALOG.items()], 'interference_filter_pages':[48,81,82],
        'temporal_requirement':'Closed confirmation + minimum 60-second lead makes next-candle M1 entry unactionable. M2/M3/M5 require the original window to remain open.',
        'performance':'Unvalidated hypotheses; record outcomes by original context.'}
