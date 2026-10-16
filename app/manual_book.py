"""Versioned book rules: closed references and separately observed live triggers.

The numeric tolerances translate drawings. They are declared hypotheses, not
performance estimates. The 1.26.11 module remains a legacy compatibility reader.
"""
from dataclasses import asdict
from hashlib import sha256
import json
import math
from . import manual_price_action as base
from .indicators import atr
from .market import Candle, valid_candle
from .live_price import PricePoint, MAX_PRICE_AGE_SECONDS, POLL_SECONDS, validate_point, point_after
from .market import DataError

VERSION = '1.26.13-book-1'
SUPPORTED_VERSIONS = ('1.26.12-book-1', VERSION)
SOURCE = base.SOURCE
RULES = base.RULES
CATALOG = {**base.CATALOG,
    'pa_flow_harami': ('Fluxo após Harami', [51], 'flow'),
    'pa_flow_engulfing': ('Fluxo após Engolfo', [52], 'flow'),
    'pa_flow_hammer': ('Fluxo após Martelo', [53], 'flow'),
    'pa_flow_star': ('Fluxo após Estrela da manhã / tarde', [54], 'flow'),
    'pa_breakout_pattern': ('Rompimento por padrão de continuidade', [63], 'breakout'),
    'pa_breakout_near_miss': ('Rompimento após rejeição do toque', [65], 'breakout'),
    'pa_correction_flow': ('Fluxo da correção até a microtendência', [66], 'flow'),
    'pa_battle_body': ('Reversão em marcação de corpo', [73], 'reversal'),
    'pa_battle_wick': ('Reversão em três pavios simétricos', [73, 74], 'reversal'),
    'pa_micro_fourth': ('Quarto toque com confluências adicionais', [68, 69, 70], 'reversal'),
}
CATALOG['pa_false_breakout'] = ('Falso rompimento por figura e retorno', [67], 'reversal')
CATALOG['pa_breakout_pressure'] = ('Rompimento por pressão e leitura das pernadas', [62], 'breakout')
FLOW_PATTERN = {'pa_flow_harami':'pa_harami', 'pa_flow_engulfing':'pa_engulfing',
                'pa_flow_hammer':'pa_hammer', 'pa_flow_star':'pa_star'}
LIVE_KEYS = frozenset((*FLOW_PATTERN, 'pa_flow_zone', 'pa_flow_micro', 'pa_flow_recovery',
                       'pa_flow_continuation', 'pa_flow_compression', 'pa_wick_retracement',
                       'pa_correction_flow'))
CLOSED_KEYS = tuple(k for k in CATALOG if k not in LIVE_KEYS)
PARAMETERS = {
    **asdict(RULES), 'wick_region_minimum_visits':3, 'near_miss_distance_atr':.40,
    'near_miss_retreat_atr':.50, 'micro_maximum_target_distance_atr':2.0,
    'micro_maximum_slope_atr_per_bar':1.0, 'live_reaction_atr':.05,
    'live_maximum_entry_distance_atr':.40, 'live_price_maximum_age_seconds':MAX_PRICE_AGE_SECONDS,
    'live_poll_seconds':POLL_SECONDS, 'live_same_analysis_candle':True,
    'continuation_small_body_maximum':.35, 'continuation_force_body_minimum':.55,
    'micro_reference_minimum_examples':1,
    'flow_region_minimum_visits':3, 'public_tick_streams':True,
    'entry_watch_until_scheduled_entry':True,
    'live_transport':'public_websocket_ticks','compatibility_poll_seconds':POLL_SECONDS,
}


def snapshot():
    value = {'version':VERSION, 'source':SOURCE, 'parameters':PARAMETERS,
             'rule_origin':'PDF drawings + explicit numerical engineering definitions',
             'closed_references':True, 'forming_candles_used_as_closed':False,
             'percentage_basis':'real-body part beyond original marking; not Fibonacci',
             'live_price_source':'timestamped original public tick/trade, no OHLC-derived quote',
             'live_timing':'observed during original candle; planned entry must still fit manual lead and send budget',
             'intrabar_immediate_broker_execution':False, 'independent_votes':False,
             'interference_pages':[48,81,82], 'smc_active':False}
    value['sha256'] = sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    return value


def catalog():
    return {'version':VERSION,'source':SOURCE,'rules':snapshot(), 'strategies':[
        {'key':k,'label':s[0],'pages':s[1],'family':s[2], 'operational_enabled':True,
         'timing':'intrabar_observed' if k in LIVE_KEYS else 'closed_confirmation',
         'live_retest_available':k in RETEST_KEYS} for k,s in CATALOG.items()],
        'interference_filter_pages':[48,81,82],
        'temporal_requirement':'Same original window + manual lead; M1 may have no actionable time.',
        'performance':'Unvalidated book translations; tests validate software only.'}


def checked_rows(candles):
    if len(candles)<RULES.minimum_candles or any(not valid_candle(c) or type(c.t) is not int
                                              or c.t<=0 for c in candles):
        return None
    interval=candles[-1].t-candles[-2].t
    if interval not in (60,120,180,300) or any(c.t%interval for c in candles) or any(
            b.t-a.t!=interval for a,b in zip(candles,candles[1:])):
        return None
    average=atr(candles[:-1])
    if average is None or not math.isfinite(average) or average<=0:
        return None
    rows=list(candles[-RULES.lookback:]);mirror=2*max(c.h for c in rows)
    if not math.isfinite(mirror):return None
    return rows,interval,average,mirror


def orient(rows,mirror,direction):
    return rows if direction=='CALL' else [Candle(c.t,mirror-c.o,mirror-c.l,mirror-c.h,mirror-c.c,c.v) for c in rows]


def micro_lines(rows,average):
    """Ascending support lines already confirmed; the opposite case is mirrored."""
    lows=[p for p in base.pivots(rows) if p['kind']=='low'][-7:]
    lines=[];tol=RULES.zone_tolerance_atr*average
    for i,a in enumerate(lows):
        for b in lows[i+1:]:
            distance=b['index']-a['index'];rise=b['price']-a['price']
            if distance<3 or rise<=0 or rise/distance>PARAMETERS['micro_maximum_slope_atr_per_bar']*average:continue
            line={'reference_at':a['at'],'reference_price':a['price'],
                  'slope_per_second':rise/(b['at']-a['at']), 'known_at':b['confirmed_at'],
                  'first_at':a['at'],'second_at':b['at'],'kind':'low'}
            if any(c.c<base._line_at(line,c.t)-tol for c in rows[b['index']+1:]):continue
            visits=[];touching=False
            for j in range(a['index']+3,len(rows)):
                contact=abs(rows[j].l-base._line_at(line,rows[j].t))<=tol
                if contact and not touching and (not visits or j-visits[-1]>=3):visits.append(j)
                touching=contact
            after=rows[b['index']:]
            peak=b['index']+max(range(len(after)),key=lambda j:after[j].h)
            line.update(visits=visits,level=base._line_at(line,rows[-1].t),
                        force_leg={'start_at':b['at'],'end_at':rows[peak].t,
                                   'distance_atr':(rows[peak].h-b['price'])/average},
                        correction_leg={'start_at':rows[peak].t,'end_at':rows[-1].t,
                                        'distance_atr':(rows[peak].h-rows[-1].c)/average},
                        correcting=peak<len(rows)-1 and rows[-1].c<rows[peak].c)
            lines.append(line)
    return sorted(lines,key=lambda l:l['known_at'],reverse=True)


def near_miss(rows,level,average):
    """Observed approach that did not touch, a distinct retreat, then a return."""
    tol=RULES.zone_tolerance_atr*average
    for i in range(max(1,len(rows)-12),len(rows)-2):
        gap=level-rows[i].h
        if not tol<gap<=PARAMETERS['near_miss_distance_atr']*average:continue
        if rows[i].c<=rows[i-1].c:continue
        retreat=next((j for j in range(i+1,len(rows)-1)
                      if rows[i].h-rows[j].l>=PARAMETERS['near_miss_retreat_atr']*average),None)
        if retreat is not None and all(c.h<level-tol for c in rows[i:retreat+1]):
            return {'approach_at':rows[i].t,'retreat_at':rows[retreat].t,
                    'gap_atr':gap/average,'retreat_atr':(rows[i].h-rows[retreat].l)/average}
    return None


def continuation_pattern(rows):
    """The four distinct candle drawings on pages 58/63, on completed bars."""
    if len(rows)<3:return None
    a,b=rows[-2:];small=PARAMETERS['continuation_small_body_maximum']
    force=PARAMETERS['continuation_force_body_minimum']
    if not base._bull(b,force):return None
    # Force/force, force with long rejection wick, indecision/force and
    # an earlier force followed by indecision and a new force.
    if base._bull(a,force) and b.c>a.c:return 'duas velas de força'
    if a.c>a.o and base._wick(a)>=abs(a.c-a.o) and b.c>a.h:return 'rejeição e força'
    if base.body_ratio(a)<=small and b.c>a.h:return 'indecisão e força'
    if base._bull(rows[-3],force) and base.body_ratio(a)<=small and b.c>rows[-3].c:
        return 'força, alívio e retomada'
    return None


def compression_context(rows,zone,average):
    before=rows[-9:]
    if len(before)<9:return False
    level=zone['level']
    return (sum(abs(c.h-level)<=.35*average for c in before)>=2
            and min(c.l for c in before[-4:])>min(c.l for c in before[:4])+.35*average
            and all(c.c<=level+.15*average for c in before))


def restore_geometry(geometry,mirror):
    value=base._restore_geometry(geometry,mirror)
    if isinstance(value.get('target'),(int,float)):value['target']=mirror-value['target']
    if 'interference_levels' in value:value['interference_levels']=[mirror-p for p in value['interference_levels']]
    for key in ('correction_line','book_pressure','arrival_filter'):
        if isinstance(value.get(key),dict) and 'reference_price' in value[key]:
            line={**value[key]};line['reference_price']=mirror-line['reference_price']
            line['slope_per_second']=-line['slope_per_second']
            if 'level' in line:line['level']=mirror-line['level']
            line['kind']='high' if line.get('kind')=='low' else 'low'
            value[key]=line
    return value


def reverse_guard(rows,zone,average):
    """Pages 44/62/63/65/66: do not reverse blindly into a leg targeting its line."""
    mirrored=orient(rows,2*max(c.h for c in rows),'PUT')
    miss=near_miss(mirrored,2*max(c.h for c in rows)-zone['level'],average)
    if miss:
        return False,'Retorno após rejeição do toque; região sujeita a rompimento (p.44/65).',miss
    for line in micro_lines(rows[:-1],average):
        target=base._line_at(line,rows[-1].t)
        if (line['correcting'] and target<zone['level']-.15*average
                and 0<rows[-2].c-target<=2*average and rows[-2].c>zone['level']):
            return False,'Perna de correção busca a microtendência além da região (p.62/66).',line
    incoming=orient(rows[:-1],2*max(c.h for c in rows),'PUT')
    pattern=continuation_pattern(incoming)
    if pattern and 0<=rows[-2].c-zone['level']<=.5*average:
        if all(abs(c.c-zone['level'])<=average for c in rows[-3:-1]):
            return False,'Padrão de continuidade chega à marcação; reversão bloqueada (p.63).',{'pattern':pattern}
    return True,'Pressão, pernadas e rejeição do toque verificadas.',{}


def room_at(zones,geometry,average,price):
    # Body/wick alternatives with the same confirmed pivot anchors describe one
    # structural region. A genuinely different region remains an obstacle.
    origin=geometry.get('zone_id')
    region=lambda value:':'.join(value.split(':')[-2:]) if isinstance(value,str) else None
    obstacles=[z['level'] for z in zones if z['level']>price and z.get('zone_id')!=origin
               and (origin is None or region(z.get('zone_id'))!=region(origin))]
    distance=min(obstacles)-price if obstacles else None
    return distance is None or distance>=.5*average,distance


def freeze_interference(zones, geometry, price):
    origin = geometry.get('zone_id')
    region = lambda value: ':'.join(value.split(':')[-2:]) if isinstance(value, str) else None
    return sorted({z['level'] for z in zones if z['level'] > price and
                   (origin is None or region(z.get('zone_id')) != region(origin))})


def region_checks(rows, zone, average, *, arrival=False, strong=False):
    checks = []
    if zone['basis'] == 'wick':
        checks.append(('Região de pavio possui três visitas distintas em simetria', len(zone['visits']) >= 3, [73]))
    if strong:
        tol = RULES.zone_tolerance_atr*average
        prior = rows[:-3]
        visits = base._visits(prior, zone['level'], tol)
        distant = bool(visits and len(prior)-1-visits[-1] >= 13 and
                       max(c.h for c in prior[visits[-1]:])-zone['level'] >= 3*average and
                       zone['level'] <= min(c.l for c in prior)+tol)
        checks.append(('Fluxo exige região forte ou extremo distante confirmado', len(zone['visits']) >= 3 or distant, [50,51]))
    if arrival:
        passed, reason, _ = reverse_guard(rows, zone, average)
        checks.append((reason, passed, [44,62,63,65,66]))
    return checks


def fibonacci_context(candidate, candles):
    """Apply the same causal interference gate to the complementary engine."""
    contract=candidate.get('setup_contract') or {}
    level=contract.get('levels',{}).get('frozen_fibonacci_level')
    if level is None:return candidate,''
    info=checked_rows(candles)
    if info is None:return None,'Fibonacci sem referências fechadas contínuas.'
    original,_,average,mirror=info;direction=candidate['direction']
    rows=orient(original,mirror,direction);level=level if direction=='CALL' else mirror-level
    zones=base._zones(rows[:-4],average)
    origin=min((z for z in zones if abs(z['level']-level)<=.15*average),
               key=lambda z:abs(z['level']-level),default=None)
    geometry={'level':level,'invalidation':level-.15*average,
              'region':[level-.15*average,level+.15*average],
              'zone_id':origin['zone_id'] if origin else None}
    room,distance=room_at(zones,geometry,average,rows[-1].c)
    if not room:return None,'Fibonacci bloqueado por zona de interferência sem espaço.'
    geometry.update(interference_levels=freeze_interference(zones,geometry,rows[-1].c),
                    space_atr=distance/average if distance is not None else None)
    if direction=='PUT':geometry=restore_geometry(geometry,mirror)
    return {**candidate,'manual_atr':average,'setup_contract':{**contract,'geometry':geometry},
            'fibonacci_context':{'interference_checked':True,'minimum_room_atr':.5,
                                 'space_atr':geometry['space_atr'],'rules_version':VERSION}},''


def _candidate(key,rows,geometry,average,interval,direction,mirror,checks=(),*,live=False,point=None):
    if geometry is None:return None
    zone_basis=geometry.get('zone_basis')
    label,pages,family=CATALOG[key]
    raw={**geometry}
    if direction=='PUT':geometry=restore_geometry(geometry,mirror)
    start=rows[-1].t+interval
    end=start+interval
    # A live trigger has no future closing candle. Its original references alone
    # are closed; the observed trade is retained separately.
    mandatory=[{'criterion':'Referências fechadas e anteriores ao gatilho','passed':True},
               {'criterion':label+' com sequência obrigatória','passed':True},
               *({'criterion':c[0],'passed':c[1],'pages':c[2]} for c in checks),
               {'criterion':'Espaço até outra região preservado','passed':True}]
    contract={'version':VERSION,'engine':key,'direction':direction,'confirmed_at':start,
              'confirmation_candle_at':rows[-1].t,'anchors':geometry.get('anchors',[]),
              'movement_family':'continuation' if family in ('flow','breakout') else 'region_reaction',
              'levels':{'manual_region':geometry['level']},'geometry':geometry,
              'invalidations':[{'level':'manual_region_boundary','price':geometry['invalidation'],
                                'mode':'close_below' if direction=='CALL' else 'close_above','inclusive':False}],
              'entry_window_start':start,'entry_window_end':end,'valid_until':end-1,
              'validity':'inside_original_analysis_candle' if live else 'inside_original_next_analysis_candle',
              'entry_kind':geometry['entry_kind'],'manual_source_pages':pages}
    result={'direction':direction,'score':80,'score_kind':'mandatory criteria, not probability',
            'manual':True,'manual_intrabar':live,'manual_atr':average,'manual_rules_version':VERSION,
            'manual_source_pages':pages,'manual_geometry':geometry,'mandatory_criteria':mandatory,
            'setup_candle':rows[-1].t,'setup_contract':contract,'strategy_resolved':key,
            'apex_setup':label,'reasons':[label+'; critérios do manual confirmados',
                'Cotação real observada dentro da vela original' if live else 'Confirmação em vela fechada'],
            'regime':base.structural_context(rows)['regime'],
            'setup_identity':sha256(repr((key,direction,raw.get('zone_id'),rows[-1].t)).encode()).hexdigest()}
    if live:
        result['live_observation']=point.json()
        result['reasons'].append('Entrada programada sujeita à antecedência e revalidação; não é execução imediata na corretora')
    if zone_basis:result['manual_zone_basis']=zone_basis
    return result


def _closed_geometry(key,rows,zones,average):
    checks=[];last=rows[-1];tol=RULES.zone_tolerance_atr*average
    if key=='pa_restricted_zone':
        zone=base._zone_rejection(rows,zones,average)
        if zone is None or not (base._bull(last) or any(base._pattern(p,rows) for p in base.PATTERNS)):return None,checks
        visits=base._visits(rows[:-1],zone['level'],tol)
        if not visits:return None,checks
        gap=len(rows)-1-visits[-1];departure=max(c.h for c in rows[visits[-1]:-1])-zone['level']
        checks=[('Ausência e saída forte da região',gap>=13 and departure>=3*average,[41,43])]
        geometry=base._geometry(zone,last=last,average=average,anchors=[rows[visits[-1]].t,last.t])
        geometry.update(absence_candles=gap,departure_atr=departure/average)
    elif key in ('pa_battle_body','pa_battle_wick'):
        basis='body' if key=='pa_battle_body' else 'wick'
        zone=base._zone_rejection(rows,[z for z in zones if z['basis']==basis],average,minimum_visits=3)
        if zone is None or not (base._bull(last) or any(base._pattern(p,rows) for p in base.PATTERNS)):return None,checks
        geometry=base._geometry(zone,last=last,average=average,anchors=[last.t])
        checks=[('Marcação de corpo ou três contatos de pavio distintos',len(zone['visits'])>=3,[73,74])]
    elif key=='pa_micro_fourth':
        line=base._micro(rows,average,touch_number=4)
        if line is None:return None,checks
        zone=base._zone_rejection(rows,zones,average,minimum_visits=3)
        if zone is None:return None,checks
        other=any(z['basis']!=zone['basis'] and len(z['visits'])>=3 and abs(z['level']-zone['level'])<=tol for z in zones)
        pattern=any(base._pattern(p,rows) for p in base.PATTERNS)
        checks=[('Quarto toque exige região forte, marcações confluentes e padrão',other and pattern,[68,69,70])]
        geometry=base._geometry(zone,last=last,average=average,anchors=[line['first_at'],line['second_at'],last.t])
        geometry.update(micro_line=line,touch_number=4)
    elif key in ('pa_breakout_pattern','pa_breakout_near_miss','pa_breakout_pressure'):
        geometry=None
        for zone in zones:
            fraction=base._fraction(last,zone['level'])
            if (fraction is None or fraction<.05 or not base._bull(last) or rows[-2].c>zone['level']
                    or last.c-zone['level']>average):continue
            evidence=(continuation_pattern(rows[:-1]) if key=='pa_breakout_pattern'
                      else near_miss(rows[:-1],zone['level'],average) if key=='pa_breakout_near_miss'
                      else next((l for l in micro_lines(rows[:-1],average)
                                 if l['force_leg']['distance_atr']>=1 and
                                 0<zone['level']-base._line_at(l,last.t)<=2*average),None))
            if not evidence:continue
            geometry=base._geometry(zone,last=last,average=average,entry_kind='continuation',anchors=[last.t])
            geometry.update(breakout_fraction=fraction,book_pressure=evidence)
            checks=[('Padrão, rejeição anterior ou pernadas comprovadas antes da ruptura',True,CATALOG[key][1])]
            break
    elif key in ('pa_head_shoulders','pa_double_extreme'):
        geometry=None
        for frozen in shape_contexts(rows[:-1],average,key):
            level=frozen['level']
            if base._bull(last) and abs(last.l-level)<=tol and last.c>level:
                geometry={**frozen,'entry_kind':'retest','anchors':[*frozen['anchors'],last.t]}
                checks=[('Forma confirmada, ruptura anterior e primeiro retorno distinto',True,CATALOG[key][1])]
                break
    elif key=='pa_false_breakout':
        geometry=None
        # A downward figure drives price outside support; relief then CALL re-entry.
        reflected=orient(rows,2*max(c.h for c in rows),'PUT')
        for index in range(max(4,len(rows)-11),len(rows)-2):
            figures=[p for p in base.PATTERNS if base._pattern(p,reflected[:index+1])]
            if not figures:continue
            for zone in zones:
                level=zone['level'];formation=rows[index]
                if zone['known_at']>formation.t or not (formation.c<level-tol and rows[index-1].c>=level-tol):continue
                relief=rows[index+1:-1]
                if (not relief or any(abs(c.c-c.o)>abs(formation.c-formation.o) for c in relief)
                        or any(c.c>level for c in relief)):continue
                if rows[-2].c<level and base._bull(last) and last.c>level and last.c-level<=average:
                    geometry=base._geometry(zone,last=last,average=average,anchors=[formation.t,last.t])
                    geometry.update(false_breakout_pattern=figures[0],breakout_at=formation.t,
                                    relief_candles=len(relief),invalidation=min(formation.l,last.l)-tol)
                    checks=[('Figura antes da saída, alívio distinto e retorno confirmado',True,[67])]
                    break
            if geometry:break
    else:
        geometry=base._detect(key,rows,zones,average)
    if geometry is None:return None,checks
    zone=next((z for z in zones if z['zone_id']==geometry.get('zone_id')),None)
    if key=='pa_pullback_harami':
        line=base._micro(rows,average)
        checks.append(('Harami + microtendência no mini pullback',line is not None,[46]))
        if line:geometry['micro_line']=line
    if key=='pa_v_after_trend':
        contacts=base._visits(rows[:-1],geometry['level'],tol)
        prior=rows[max(0,contacts[0]-15):contacts[0]+1] if contacts else []
        checks.append(('V de reversão após deslocamento anterior contrário',bool(prior and prior[0].c-prior[-1].c>=3*average),[40]))
    if zone:
        checks.extend(region_checks(rows,zone,average,arrival=CATALOG[key][2]=='reversal' and key!='pa_false_breakout'))
    return geometry,checks


def evaluate_all(candles,*,keys=None):
    keys=tuple(CATALOG) if keys is None else tuple(keys)
    if any(k not in CATALOG for k in keys):raise ValueError('Estratégia do livro desconhecida.')
    output={k:(None,'Histórico fechado válido e contínuo insuficiente.') for k in keys}
    info=checked_rows(candles)
    if info is None:return output
    rows,interval,average,mirror=info
    if rows[-1].h-rows[-1].l>2.5*average:return {k:(None,'Amplitude incompatível com confirmação.') for k in keys}
    found={k:[] for k in keys};reasons={}
    for direction in ('CALL','PUT'):
        oriented=orient(rows,mirror,direction);zones=base._zones(oriented[:-4],average)
        for key in keys:
            if key in LIVE_KEYS:
                reasons[key]='Referências fechadas; gatilho exige cotação real durante a vela.';continue
            geometry,checks=_closed_geometry(key,oriented,zones,average)
            failure=next((c[0] for c in checks if not c[1]),None)
            if failure:reasons[key]=failure;continue
            if geometry is None:continue
            room,distance=room_at(zones,geometry,average,oriented[-1].c)
            if not room:reasons[key]='Zona de interferência sem espaço (p.48/81/82).';continue
            geometry['space_atr']=distance/average if distance is not None else None
            geometry['interference_levels']=freeze_interference(zones,geometry,oriented[-1].c)
            found[key].append(_candidate(key,oriented,geometry,average,interval,direction,mirror,checks))
    for key in keys:
        output[key]=((found[key][0],'') if len(found[key])==1 else
                     (None,'CALL e PUT elegíveis; padrão ambíguo.') if found[key] else
                     (None,reasons.get(key,'Sequência do livro ainda não confirmada para '+CATALOG[key][0]+'.')))
    return output


def evaluate(key,candles):
    return evaluate_all(candles,keys=(key,))[key]


RETEST_KEYS=frozenset(('pa_pullback_micro','pa_pullback_harami','pa_breakout_50','pa_breakout_90',
                       'pa_compression_breakout','pa_head_shoulders','pa_double_extreme'))


def _plan(key,rows,geometry,average,interval,direction,mirror,checks=(),**extras):
    return {'key':key,'direction':direction,'geometry':geometry,'average':average,'interval':interval,
            'mirror':mirror,'rows':rows,'checks':list(checks),'starts_at':rows[-1].t+interval,
            'ends_at':rows[-1].t+2*interval,
            'id':sha256(repr((key,direction,geometry.get('zone_id'),rows[-1].t)).encode()).hexdigest(),**extras}


def _historical_micro_reference(rows,average,current_line):
    for end in range(max(8,len(rows)-25),len(rows)-1):
        prefix=rows[:end+1]
        earlier=base._micro(prefix,average)
        interval=rows[-1].t-rows[-2].t
        if (any(base._pattern(p,prefix) for p in base.PATTERNS) and earlier
                and abs(base._line_at(earlier,rows[end].t)-base._line_at(current_line,rows[end].t))<=.15*average
                and abs(earlier['slope_per_second']-current_line['slope_per_second'])*interval<=.05*average
                and rows[end+1].c>rows[end].c+.05*average):return rows[end].t
    return None


def shape_contexts(rows,average,key):
    """Completed shape and breakout; no imaginary retest candle is appended."""
    tol=.15*average;found=[];interval=rows[-1].t-rows[-2].t
    for i in range(len(rows)-1,max(7,len(rows)-12),-1):
        before=rows[:i];pivots=base.pivots(before)
        lows=[p for p in pivots if p['kind']=='low'];shapes=[]
        if key=='pa_head_shoulders':
            for left,head,right in zip(lows,lows[1:],lows[2:]):
                highs1=[p for p in pivots if p['kind']=='high' and left['index']<p['index']<head['index']]
                highs2=[p for p in pivots if p['kind']=='high' and head['index']<p['index']<right['index']]
                if (not highs1 or not highs2 or abs(left['price']-right['price'])>.5*average
                        or head['price']>=min(left['price'],right['price'])-.5*average):continue
                a=max(highs1,key=lambda p:p['price']);b=max(highs2,key=lambda p:p['price'])
                line={'reference_at':a['at'],'reference_price':a['price'],
                      'slope_per_second':(b['price']-a['price'])/(b['at']-a['at'])}
                shapes.append(([left,head,right,a,b],line,head['price']))
        else:
            for first,second in zip(lows,lows[1:]):
                between=[p for p in pivots if p['kind']=='high' and first['index']<p['index']<second['index']]
                if (not between or not 4<=second['index']-first['index']<=30
                        or abs(first['price']-second['price'])>.3*average):continue
                top=max(between,key=lambda p:p['price']);prior=before[max(0,first['index']-15):first['index']]
                if (top['price']-max(first['price'],second['price'])<average or not prior
                        or prior[0].c-first['price']<2*average):continue
                shapes.append(([first,second,top],{'reference_at':top['at'],'reference_price':top['price'],
                                                 'slope_per_second':0},min(first['price'],second['price'])))
        for shape,line,extreme in shapes:
            breaking=rows[i];level=base._line_at(line,breaking.t)
            if (max(p['confirmed_at'] for p in shape)>breaking.t or not base._bull(breaking)
                    or rows[i-1].c>base._line_at(line,rows[i-1].t)+tol or breaking.c<=level+tol):continue
            relief=rows[i+1:];fraction=base._fraction(breaking,level)
            if fraction is not None and fraction>=.85:
                if len(relief)<4 or any(abs(c.c-c.o)>.65*abs(breaking.c-breaking.o) for c in relief):continue
            if any(c.c<base._line_at(line,c.t)-tol or c.l<=base._line_at(line,c.t)+tol for c in relief):continue
            current=base._line_at(line,rows[-1].t+interval)
            geometry=base._geometry({'level':current,'start_at':min(p['at'] for p in shape),
                'known_at':max(p['confirmed_at'] for p in shape),
                'zone_id':'shape:'+str(min(p['at'] for p in shape))},last=rows[-1],average=average,
                entry_kind='live_retest',anchors=[p['at'] for p in shape]+[breaking.t])
            geometry.update(neckline=line,shape_extreme=extreme,breakout_at=breaking.t,
                            breakout_fraction=fraction,relief_candles=len(relief))
            found.append(geometry)
    return found


def prepare_live(candles):
    """Freeze eligible contexts before any future tick is requested."""
    info=checked_rows(candles)
    if info is None:return []
    original,interval,average,mirror=info;plans=[]
    for direction in ('CALL','PUT'):
        rows=orient(original,mirror,direction);zones=base._zones(rows[:-3],average);last=rows[-1]
        patterns=[p for p in base.PATTERNS if base._pattern(p,rows)]
        zone=base._zone_rejection(rows,zones,average)
        if patterns:
            for key in ('pa_flow_zone',*FLOW_PATTERN,'pa_flow_micro'):
                wanted=FLOW_PATTERN.get(key)
                if wanted and not (wanted in patterns or wanted=='pa_engulfing' and 'pa_engulfing_three' in patterns):continue
                checks=[];selected_zone=zone
                if key=='pa_flow_micro':
                    line=base._micro(rows,average)
                    reference=_historical_micro_reference(rows[:-1],average,line) if line else None
                    if not line or reference is None:continue
                    selected_zone=zone or {'level':line['level'],'start_at':line['first_at'],
                        'known_at':line['known_at'],'zone_id':'micro:'+str(line['first_at'])}
                if selected_zone is None:continue
                geometry=base._geometry(selected_zone,last=last,average=average,entry_kind='live_flow',anchors=[last.t])
                if key=='pa_flow_micro':
                    geometry.update(micro_line=line,previous_flow_reference_at=reference)
                    checks=[('Microtendência com exemplo anterior de padrão e continuidade',True,[55])]
                geometry['pattern']=wanted or patterns[0]
                plans.append(_plan(key,rows,geometry,average,interval,direction,mirror,checks,
                                   trigger='flow',baseline=last.c,zones=zones))
        uncertain=rows[-5:-2]
        if (sum(base.body_ratio(c)<=.35 and max(base._wick(c),c.h-max(c.o,c.c))>=.35*(c.h-c.l)
                for c in uncertain)>=2 and base._bull(rows[-2],.55) and base._bull(last,.55)
                and last.c>rows[-2].h):
            zone=base._zone_rejection(rows[:-2],zones,average)
            if zone:
                geometry=base._geometry(zone,last=last,average=average,entry_kind='live_flow',anchors=[rows[-2].t,last.t])
                plans.append(_plan('pa_flow_recovery',rows,geometry,average,interval,direction,mirror,
                    [('Indecisão, retomada e confirmação em velas distintas',True,[56,57])],
                    trigger='flow',baseline=last.c,zones=zones))
        pattern=continuation_pattern(rows)
        target=min((z for z in zones if .5*average<=z['level']-last.c<=2*average),
                   key=lambda z:z['level'],default=None)
        if pattern and target:
            geometry=base._geometry({'level':last.o,'start_at':last.t,'known_at':last.t+interval,
                'zone_id':target['zone_id']},last=last,average=average,entry_kind='live_flow',anchors=[last.t])
            geometry.update(target=target['level'],continuation_pattern=pattern)
            plans.append(_plan('pa_flow_continuation',rows,geometry,average,interval,direction,mirror,
                [('Padrão de continuidade e alvo próximo ainda disponível',True,[58,59])],
                trigger='flow',baseline=last.c,zones=zones))
        for zone in zones:
            if compression_context(rows,zone,average):
                line=next((l for l in micro_lines(rows,average) if 0<zone['level']-l['level']<=2*average),None)
                if line:
                    geometry=base._geometry(zone,last=last,average=average,entry_kind='live_flow',anchors=[last.t])
                    geometry['micro_line']=line
                    geometry['invalidation']=base._line_at(line,last.t+interval)-.15*average
                    plans.append(_plan('pa_flow_compression',rows,geometry,average,interval,direction,mirror,
                        [('Afunilamento de microtendência com suporte/resistência',True,[60])],
                        trigger='compression',baseline=zone['level'],zones=zones))
        # Four completed wick bars arm a touch in the current candle, never a
        # fabricated closed reaction candle.
        if base.structural_context(rows)['regime']!='tendencial':
            for zone in zones:
                prior=rows[-4:]
                if all(abs(c.l-zone['level'])<=.15*average and c.h>c.l and
                       base._wick(c)>=.35*(c.h-c.l) for c in prior):
                    geometry=base._geometry(zone,last=last,average=average,entry_kind='live_retracement',anchors=[c.t for c in prior])
                    geometry['symmetric_candles']=4
                    plans.append(_plan('pa_wick_retracement',rows,geometry,average,interval,direction,mirror,
                        [('Quatro pavios anteriores em simetria e mercado lateral',True,[75])],
                        trigger='touch',baseline=zone['level'],zones=zones))
        # Original breakout is closed, relief bars are closed; the next return
        # can now be observed before that return candle closes.
        for index in range(len(rows)-1,max(5,len(rows)-12),-1):
            breaking=rows[index]
            for zone in zones:
                level=zone['level']
                if zone['known_at']>breaking.t or not base._break_strong(breaking,level):continue
                fraction=base._fraction(breaking,level);relief=rows[index+1:]
                if any(c.c<level-.15*average or c.l<=level+.15*average for c in relief):continue
                deep=fraction>=.85
                if deep and (len(relief)<4 or any(abs(c.c-c.o)>.65*(breaking.c-breaking.o) for c in relief)):continue
                if not deep and len(relief)>4:continue
                for key in ('pa_breakout_50','pa_breakout_90','pa_pullback_micro','pa_pullback_harami','pa_compression_breakout'):
                    if key=='pa_breakout_50' and not .45<=fraction<=.55:continue
                    if key=='pa_breakout_90' and not deep:continue
                    lines=micro_lines(rows,average)
                    line=next((l for l in lines if abs(base._line_at(l,last.t+interval)-level)<=.35*average),None)
                    if key in ('pa_pullback_micro','pa_pullback_harami','pa_compression_breakout') and line is None:continue
                    if key=='pa_pullback_harami' and not base._pattern('pa_harami',rows[:index]):continue
                    if key=='pa_compression_breakout' and not base._compression(rows[:index+1],zone,average):continue
                    geometry=base._geometry(zone,last=last,average=average,entry_kind='live_retest',anchors=[breaking.t])
                    geometry.update(breakout_at=breaking.t,breakout_fraction=fraction,relief_candles=len(relief))
                    if line:geometry['micro_line']=line
                    plans.append(_plan(key,rows,geometry,average,interval,direction,mirror,
                        [('Ruptura fechada, primeiro retorno e alívio profundo respeitado',True,CATALOG[key][1])],
                        trigger='touch',baseline=level,zones=zones))
        for key in ('pa_head_shoulders','pa_double_extreme'):
            for geometry in shape_contexts(rows,average,key):
                plans.append(_plan(key,rows,geometry,average,interval,direction,mirror,
                    [('Forma conhecida, ruptura fechada e primeiro retorno pendente',True,CATALOG[key][1])],
                    trigger='touch',baseline=geometry['level'],zones=zones))
        # Page 66 is a correction leg towards its micro line, not a generic
        # liquidity sweep. Its direction is opposite to that ascending line.
        for line in micro_lines(rows,average):
            if not line['correcting'] or line['force_leg']['distance_atr']<1:continue
            target=base._line_at(line,last.t+interval)
            for zone in zones:
                if not target+.5*average<zone['level']<last.c:continue
                opposite='PUT' if direction=='CALL' else 'CALL'
                # Build an oriented down-leg by mirroring the already known rows.
                down=orient(original,mirror,opposite);down_level=mirror-zone['level']
                opposite_id=zone['zone_id'].replace(':low:',':high:') if ':low:' in zone['zone_id'] else zone['zone_id'].replace(':high:',':low:')
                geometry=base._geometry({'level':down_level,'zone_id':opposite_id,
                    'known_at':zone['known_at'],'start_at':zone['start_at']},last=down[-1],average=average,
                    entry_kind='live_flow',anchors=[line['force_leg']['end_at'],last.t])
                opposite_line={**line,'reference_price':mirror-line['reference_price'],
                    'slope_per_second':-line['slope_per_second'],'level':mirror-line['level'],'kind':'high'}
                geometry.update(target=mirror-target,correction_line=opposite_line,correction_evidence=line['correction_leg'])
                geometry['invalidation']=down[-1].l-.15*average
                downzones=base._zones(down[:-3],average)
                plans.append(_plan('pa_correction_flow',down,geometry,average,interval,opposite,mirror,
                    [('Perna de força e correção com microtendência como alvo',True,[66])],
                    trigger='cross',baseline=down_level,zones=downzones))
    eligible=[]
    for plan in plans:
        zone=next((z for z in plan['zones'] if z['zone_id']==plan['geometry'].get('zone_id')),None)
        if zone:
            arrival=plan['key'] in (*FLOW_PATTERN,'pa_flow_zone','pa_flow_micro','pa_flow_recovery','pa_wick_retracement')
            strong=plan['key'] in (*FLOW_PATTERN,'pa_flow_zone','pa_flow_recovery')
            plan['checks'].extend(region_checks(plan['rows'],zone,plan['average'],arrival=arrival,strong=strong))
        if all(c[1] for c in plan['checks']):eligible.append(plan)
    unique={p['id']:p for p in eligible}
    return list(unique.values())


def evaluate_live(plans,points,*,now,forming_open=None,dead=None,asset=None,identity=None,touches=None):
    """No interpolation: a touch requires an actual timestamped observed point."""
    if not points:return [],[]
    point=points[-1]
    if not isinstance(point,PricePoint):return [],[{'reason':'Cotação sem prova de horário.'}]
    try:
        for item in points:
            if item.received_at>now:raise DataError('Cotação futura.','live_price_future')
            validate_point(item,asset=asset or point.asset,identity=identity or point.source_identity,now=item.received_at)
        if any(not point_after(b,a) for a,b in zip(points,points[1:])):raise DataError('Ticks fora de ordem.','live_price_order')
        validate_point(point,asset=asset or point.asset,identity=identity or point.source_identity,now=now)
    except (DataError,AttributeError) as error:
        return [],[{'reason':str(error),'code':getattr(error,'code','live_price_invalid')}]
    dead=dead if dead is not None else set();found=[];rejections=[]
    touches=touches if touches is not None else {}
    for plan in plans:
        key=plan['key'];direction=plan['direction'];average=plan['average'];mirror=plan['mirror']
        if any(not c[1] for c in plan['checks']):continue
        if plan['id'] in dead or not plan['starts_at']<=point.at<=now<plan['ends_at']:continue
        values=[p if direction=='CALL' else PricePoint(mirror-p.price,p.at,p.received_at,p.asset,p.source_identity,p.trade_id)
                for p in points if plan['starts_at']<=p.at<=now]
        if not values:continue
        value=values[-1].price;geometry={**plan['geometry']};level=geometry['level'];tol=.15*average
        if value<geometry['invalidation']:
            dead.add(plan['id']);rejections.append({'strategy':key,'reason':'Preço observado invalidou a região original.'});continue
        if geometry.get('target') is not None and geometry['target']-value<.5*average:
            rejections.append({'strategy':key,'reason':'Alvo/interferência sem espaço restante.'});continue
        trigger=plan['trigger'];passed=False;extra={}
        if trigger=='touch':
            original_touch=touches.get(plan['id'])
            if original_touch is None:
                original_touch=next((p for p in points if plan['starts_at']<=p.at<=now and
                                    abs((p.price if direction=='CALL' else mirror-p.price)-level)<=tol),None)
                if original_touch is not None:touches[plan['id']]=original_touch
            passed=(original_touch is not None and point_after(point,original_touch) and
                    level+.05*average<=value<=level+.40*average)
            if original_touch:extra={'touch_observation':original_touch.json()}
        elif trigger=='flow':
            passed=plan['baseline']+.05*average<=value<=plan['baseline']+.75*average
        elif trigger=='cross':
            previous=next((p for p in reversed(values[:-1]) if p.price<=plan['baseline']),None)
            passed=previous is not None and value>plan['baseline']+.05*average and value-plan['baseline']<=average
            if previous:extra={'cross_before_observation':next(p.json() for p in points if (p.at,p.received_at)==(previous.at,previous.received_at))}
        elif trigger=='compression' and forming_open is not None:
            opening=forming_open if direction=='CALL' else mirror-forming_open
            fraction=(value-level)/(value-opening) if value>opening and opening<=level<value else None
            passed=fraction is not None and fraction>=.05 and value-level<=average
            extra={'forming_open_from_source':forming_open,'observed_body_fraction':fraction}
        if not passed:continue
        # Recompute room against the observed price, not a future candle close.
        room,distance=room_at(plan['zones'],geometry,average,value)
        if not room:
            rejections.append({'strategy':key,'reason':'Zona de interferência na cotação observada.'});continue
        geometry.update(extra,space_atr=distance/average if distance is not None else None)
        geometry['interference_levels']=freeze_interference(plan['zones'],geometry,value)
        checks=[*plan['checks'],('Gatilho por preço real observado durante a vela',True,CATALOG[key][1])]
        result=_candidate(key,plan['rows'],geometry,average,plan['interval'],direction,mirror,checks,live=True,point=point)
        result['live_plan_id']=plan['id'];result['live_trigger_observations']=[p.json() for p in points[-12:]]
        if extra.get('touch_observation') and extra['touch_observation'] not in result['live_trigger_observations']:
            result['live_trigger_observations'].insert(0,extra['touch_observation'])
        found.append(result)
    return found,rejections
