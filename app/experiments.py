"""Isolated, causal OHLC research. Never part of the production registry."""
from dataclasses import dataclass
import hashlib
from .market import Candle, DataError, validate
from .strategies.helpers import atr, body_ratio, closes, ema_series
from .strategies.regime import classify

VERSION = 'research-1.2.0'
KEYS = ('structure_pullback', 'breakout_retest', 'range_reversal')
LEGACY_KEYS = KEYS
KEYS = (*LEGACY_KEYS, 'third_touch')


@dataclass(frozen=True)
class Rules:
    # Predeclared hypotheses, not optimized against the user's losses.
    pivot_sides: int = 2
    tolerance_atr: float = .15
    minimum_body: float = .5
    maximum_range_atr: float = 1.5
    maximum_extension_atr: float = 1.0
    minimum_room_atr: float = .5
    range_min_atr: float = 2.0
    range_max_atr: float = 6.0
    maximum_efficiency: float = .35


RULES = Rules()


def pivots(rows, sides=2):
    """A pivot is usable only after both right-hand bars have closed."""
    result = {'high': [], 'low': []}
    interval = rows[1].t - rows[0].t if len(rows) > 1 else 0
    for i in range(sides, len(rows)-sides):
        neighbors = rows[i-sides:i] + rows[i+1:i+sides+1]
        for key, attr, compare in (('high', 'h', lambda a,b:a>b), ('low','l',lambda a,b:a<b)):
            if all(compare(getattr(rows[i],attr),getattr(c,attr)) for c in neighbors):
                result[key].append({'index':i,'price':getattr(rows[i],attr),
                                    'at':rows[i].t,'confirmed_at':rows[i+sides].t + interval})
    return result


def _check(audit, code, passed, detail):
    audit.append({'code':code, 'passed':bool(passed), 'detail':detail})
    return bool(passed)


def _touches(rows, level, tolerance, low=True):
    # Adjacent candles at one touch do not count as two independent visits.
    selected=[]; in_visit=False
    for i,c in enumerate(rows):
        touching = abs((c.l if low else c.h)-level)<=tolerance
        if touching and not in_visit and (not selected or i-selected[-1]>=3):
            selected.append(i)
        in_visit=touching
    return selected


def _space(rows, close, average):
    obstacles=[p['price'] for p in pivots(rows)['high'] if p['price']>=close
               and not any(c.c>p['price'] for c in rows[p['index']+1:])]
    return not obstacles or min(obstacles)-close >= RULES.minimum_room_atr*average


def _pullback(rows, average, audit):
    before=rows[:-2]; prior,last=rows[-2:]; swings=pivots(before)
    highs,lows=swings['high'],swings['low']
    trending=(len(highs)>=2 and len(lows)>=2 and highs[-1]['price']>highs[-2]['price']
              and lows[-1]['price']>lows[-2]['price'] and lows[-1]['index']>=len(rows)-25)
    if not _check(audit,'higher_highs_lows',trending,'Dois topos e dois fundos confirmados ascendentes antes da retração.'):
        return None
    anchor_atr=atr(before)
    level=ema_series(closes(before),21)[-1]; tolerance=.25*anchor_atr
    if not _check(audit,'predefined_zone',prior.l<=level+tolerance and prior.h>=level-tolerance,
                  'Retração N-1 toca EMA21 calculada até N-2, com zona de 0,25 ATR.'):
        return None
    if not _check(audit,'structure_intact',all(c.c>lows[-1]['price'] for c in rows[lows[-1]['index']+1:]),
                  'Nenhum fechamento rompe o último fundo estrutural.'):
        return None
    if not _check(audit,'resumption',last.c>last.o and last.c>prior.h and last.c>level,
                  'N fecha na direção da tendência acima da máxima de N-1.'):
        return None
    if not _check(audit,'extension',0<last.c-level<=RULES.maximum_extension_atr*average,
                  'Confirmação até 1 ATR da zona, sem perseguir uma pernada esticada.'):
        return None
    if not _check(audit,'room',_space(before,last.c,average),'Pelo menos 0,5 ATR até resistência confirmada conhecida, se existente.'):
        return None
    return {'region':[level-tolerance,level+tolerance], 'invalidation':[lows[-1]['price']],
            'anchor_atr':anchor_atr,
            'sequence':{'trend_high_at':highs[-1]['at'],'trend_low_at':lows[-1]['at'],
                        'zone_defined_at':before[-1].t,'pullback_at':prior.t,'confirmation_at':last.t}}




def _breakout(rows, average, audit):
    last=rows[-1]
    for k in range(len(rows)-2,len(rows)-7,-1):
        anchor_atr=atr(rows[:k])
        window=rows[k-20:k];low=min(c.l for c in window); high=max(c.h for c in window)
        if not RULES.range_min_atr*anchor_atr<=high-low<=RULES.range_max_atr*anchor_atr:continue
        if len(_touches(window,low,RULES.tolerance_atr*anchor_atr))<2 or len(_touches(window,high,RULES.tolerance_atr*anchor_atr,False))<2:continue
        breakout=rows[k]
        if not (breakout.c>high+.15*anchor_atr and breakout.c>breakout.o and body_ratio(breakout)>=.6
                and breakout.h-breakout.l<=RULES.maximum_range_atr*anchor_atr):continue
        if any(c.c<high-.15*anchor_atr for c in rows[k+1:-1]):continue
        if not (last.l<=high+.15*anchor_atr and last.l>=high-.35*anchor_atr and last.c>high
                and last.c>last.o and last.c-high<=RULES.maximum_extension_atr*average):continue
        if not _space(rows[:k],last.c,average):continue
        _check(audit,'breakout_then_retest',True,'Faixa definida em 20 velas, dois toques separados por borda, rompimento fechado e reteste posterior até 5 velas.')
        _check(audit,'breakout_intact',True,'Nenhum fechamento intermediário retorna mais de 0,15 ATR; penetração final máxima 0,35 ATR.')
        return {'region':[high-.15*anchor_atr,high+.15*anchor_atr], 'invalidation':[high-.35*anchor_atr],
                'anchor_atr':anchor_atr,
                'sequence':{'zone_defined_at':window[-1].t,'breakout_at':breakout.t,'confirmation_at':last.t}}
    _check(audit,'breakout_then_retest',False,'Sem faixa de 2–6 ATR e sequência de rompimento/reteste separada, íntegra e sem esticamento.')
    return None


def _range(rows, average, audit):
    window=rows[-22:-2]; prior,last=rows[-2:]
    anchor_atr=atr(rows[:-2])
    low=min(c.l for c in window);high=max(c.h for c in window)
    path=sum(abs(b.c-a.c) for a,b in zip(window,window[1:]))
    efficiency=abs(window[-1].c-window[0].c)/path if path else 0
    lateral=(classify(rows[:-2])=='lateral' and classify(rows)!='tendencial' and efficiency<=RULES.maximum_efficiency
             and RULES.range_min_atr*anchor_atr<=high-low<=RULES.range_max_atr*anchor_atr
             and len(_touches(window,low,.15*anchor_atr))>=2 and len(_touches(window,high,.15*anchor_atr,False))>=2)
    if not _check(audit,'predefined_range',lateral,'Faixa anterior de 2–6 ATR com dois toques por borda, EMA lateral e eficiência ≤0,35.'):
        return None
    width=prior.h-prior.l
    reject=(low-.35*anchor_atr<=prior.l<=low+.15*anchor_atr and prior.c>low and width>0
            and min(prior.o,prior.c)-prior.l>=.35*width)
    if not _check(audit,'edge_rejection',reject,'N-1 rejeita suporte com pavio ≥35% e penetração máxima 0,35 ATR.'):
        return None
    if not _check(audit,'range_confirmation',last.c>last.o and last.c>prior.h and low<last.c<(low+high)/2
                  and high-last.c>=RULES.minimum_room_atr*average,
                  'N confirma acima de N-1, dentro da metade inferior e com espaço até a borda oposta.'):
        return None
    # No intermediate/confirmation close may invalidate the pre-existing range.
    if not _check(audit,'range_intact',all(low-.35*anchor_atr<c.c<high+.35*anchor_atr for c in rows[-2:]),
                  'Fechamentos posteriores preservam os extremos da faixa anterior.'):
        return None
    return {'region':[low,high],'invalidation':[low-.35*anchor_atr],'anchor_atr':anchor_atr,
            'sequence':{'zone_defined_at':window[-1].t,'rejection_at':prior.t,'confirmation_at':last.t}}


from .third_touch import pattern as third_touch_pattern, VERSION as THIRD_TOUCH_VERSION

PATTERNS={'structure_pullback':_pullback,'breakout_retest':_breakout,'range_reversal':_range,'third_touch':third_touch_pattern}


def rule_snapshot(key):
    from dataclasses import asdict
    if key == 'third_touch':
        from .third_touch import snapshot
        return snapshot()
    return {'version': VERSION, 'parameters': asdict(RULES), 'operational_enabled': False}


def evaluate(key, candles, *, now, interval):
    """Return full accepted/rejected diagnostics, never an operational signal."""
    if key not in PATTERNS:raise ValueError('Experimento desconhecido.')
    result={'strategy':key,'version':THIRD_TOUCH_VERSION if key == 'third_touch' else VERSION,'experimental':True,'can_emit':False,
            'rule_snapshot':rule_snapshot(key),
            'accepted':False,'decision_at':now,'interval_seconds':interval,'checks':[]}
    if interval not in (60,300):
        result['reason']='Experimentos disponíveis somente em M1/M5.';return result
    try:
        rows=validate(candles,now,max_age=interval+30,interval=interval)
        if rows[-1].t+interval != int(now//interval)*interval:
            raise DataError('Último candle fechado ausente.','missing_latest_candle')
    except DataError as error:
        result.update(reason=str(error),code=error.code);return result
    result.update(regime=classify(rows) or 'indefinido',setup_candle=rows[-1].t,
                  valid_until=rows[-1].t+interval+180)
    average=atr(rows[:-1])  # Confirmation cannot enlarge its own volatility limit.
    if average is None or average<=0:
        result['reason']='ATR anterior à confirmação indisponível.';return result
    last=rows[-1]
    geometry_ok=.3*average<=last.h-last.l<=RULES.maximum_range_atr*average
    if key!='third_touch':geometry_ok=geometry_ok and body_ratio(last)>=RULES.minimum_body
    geometry_detail=('Vela do terceiro toque fechada; amplitude de 0,3–1,5 ATR até N-1; cor, corpo e pavio não confirmam a regra isoladamente.'
                     if key=='third_touch' else 'Candle fechado com corpo ≥50% e amplitude de 0,3–1,5 ATR calculado até N-1.')
    if not _check(result['checks'],'confirmation_geometry',geometry_ok,geometry_detail):
        result['reason']='Confirmação fraca ou amplitude excessiva.';return result
    matches=[];direction_checks={};mirror=2*max(c.h for c in rows)
    for direction in ('CALL','PUT'):
        oriented=rows if direction=='CALL' else [Candle(c.t,mirror-c.o,mirror-c.l,mirror-c.h,mirror-c.c) for c in rows]
        audit=[];geometry=PATTERNS[key](oriented,average,audit);direction_checks[direction]=audit
        if geometry:
            if direction=='PUT':
                for name in ('region','invalidation'):
                    geometry[name]=sorted(mirror-price for price in geometry[name])
                if 'swept_level' in geometry:geometry['swept_level']=mirror-geometry['swept_level']
                if key == 'third_touch':
                    geometry['level_kind'] = 'LTB' if geometry['level_kind'] == 'LTA' else 'resistência'
                    for name in ('line','invalidation_line'):
                        geometry[name]['reference_price'] = mirror - geometry[name]['reference_price']
                        geometry[name]['slope_per_second'] *= -1
                        geometry[name]['side'] = 'resistance'
                    for name in ('first_anchor','second_anchor'):
                        geometry['line'][name]['price'] = mirror - geometry['line'][name]['price']
            matches.append({'direction':direction,**geometry})
    result['direction_checks']=direction_checks
    if len(matches)!=1:
        result['reason']='Sem sequência objetiva confirmada.' if not matches else 'CALL/PUT simultâneos; ambiguidade bloqueada.'
        return result
    result.update(matches[0],accepted=True,atr=average,score=80,
                  score_kind='heurístico; não probabilidade',reason='Setup experimental confirmado; emissão desativada.')
    if key=='third_touch':result['valid_until']=result['entry_window_end']
    result['score_composition']=[{'criterion':'Sequência obrigatória e confirmação causal','points':80}]
    result['mandatory_criteria']=result['checks']+direction_checks[result['direction']]
    anchor={k:v for k,v in result['sequence'].items() if k!='confirmation_at'}
    if key=='structure_pullback':anchor={k:v for k,v in anchor.items() if k.startswith('trend_')}
    if key=='range_reversal':anchor={'range':result['region']}
    if key=='third_touch':anchor={k:v for k,v in anchor.items() if k in ('first_touch_at','second_touch_at','zone_defined_at')}
    result['setup_identity']=hashlib.sha256(repr((key,result['direction'],sorted(anchor.items()))).encode()).hexdigest()
    return result


def shadow(candles, *, now, interval, keys=KEYS):
    results=[evaluate(key,candles,now=now,interval=interval) for key in keys]
    for result in results:
        result['conflict_policy']='Hipóteses isoladas; não são agregadas nem contadas como confirmações independentes.'
        result['opposing_experiments']=[other['strategy'] for other in results if result['accepted'] and other['accepted']
                                         and other['direction']!=result['direction']]
    return results
