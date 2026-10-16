"""Decision-time geometry from existing predicates, not a new strategy.

Frozen levels do not move during revalidation. Indicator-only triggers keep a
candle anchor; regional engines keep their causal event/region anchors.
"""
import hashlib
import json
import math
from .market import DataError
from .strategies.helpers import atr, bollinger, closes, ema_series

SETUP_VERSION='1.26.2-setup-1'


def capture_setup(key, candidate, rows, interval):
    if not candidate or not rows:
        return candidate
    if candidate.get('manual') and candidate.get('setup_contract'):
        return candidate
    up=candidate['direction']=='CALL'
    last=rows[-1]
    close_at=last.t+interval
    contract={'version':SETUP_VERSION,'engine':key,'direction':candidate['direction'],
        'confirmed_at':close_at,'confirmation_candle_at':last.t,
        'anchors':[candidate['setup_candle']],'movement_family':key,'levels':{},'invalidations':[],
        'validity':'original_scheduled_entry_only','valid_until':None}
    average=atr(rows)
    def level(name, price, anchor=None, *, wick=False, equal=False):
        if type(price) not in (int,float) or not math.isfinite(price) or price<=0:
            raise DataError('Referência técnica inválida.','setup_geometry_invalid')
        mode=('wick_below' if up else 'wick_above') if wick else ('close_below' if up else 'close_above')
        contract['levels'][name]=price
        contract['invalidations'].append({'level':name,'price':price,'mode':mode,'inclusive':equal})
        if anchor is not None:contract['anchors']=[anchor]
    def reaction_start(price,tolerance):
        first=len(rows)-1
        while first>0:
            prior=rows[first-1]
            if not (prior.l<=price+tolerance if up else prior.h>=price-tolerance):break
            first-=1
        return rows[first].t
    if key=='ema_pullback':
        level('frozen_ema21',ema_series(closes(rows),21)[-1])
        # A contiguous visit is one pullback; a later separate visit is distinct.
        fast=ema_series(closes(rows),9)
        first=len(rows)-1
        while first>21 and rows[first-1].l<=fast[first-1]<=rows[first-1].h:first-=1
        contract['anchors']=[rows[first].t]
    elif key=='bb_reversion':
        band=bollinger(closes(rows))
        level('frozen_bollinger_boundary',band[0] if up else band[2],rows[-2].t)
    elif key in ('range_pivot','candlestick_sr','consolidation','sr_volume_breakout'):
        window={'range_pivot':rows[-21:-1],'candlestick_sr':rows[-22:-2],
                'consolidation':rows[-13:-1],
                'sr_volume_breakout':rows[-21:-1]}[key]
        breakout=key in ('consolidation','sr_volume_breakout')
        anchor=(max(window,key=lambda c:c.h) if up else min(window,key=lambda c:c.l)) if breakout else (
                min(window,key=lambda c:c.l) if up else max(window,key=lambda c:c.h))
        boundary=(anchor.h if up else anchor.l) if breakout else (anchor.l if up else anchor.h)
        level('original_region_boundary',boundary,anchor.t)
        if breakout:contract['anchors']=[anchor.t,last.t]
        else:
            tolerance=(.1 if key=='range_pivot' else .2 if key=='candlestick_sr' else .05)*average
            contract['anchors']=[anchor.t,reaction_start(boundary,tolerance)]
        # Different candle patterns in S/R remain distinct engines, not votes.
    elif key=='rsi_divergence':
        attr='l' if up else 'h'
        pivots=[]
        for i in range(max(15,len(rows)-22),len(rows)-2):
            value=getattr(rows[i],attr)
            neighbours=(getattr(rows[i-1],attr),getattr(rows[i+1],attr))
            if all(value<n if up else value>n for n in neighbours):pivots.append(i)
        if len(pivots)>=2:
            level('second_divergence_pivot',getattr(rows[pivots[-1]],attr))
            contract['anchors']=[rows[i].t for i in pivots[-2:]]
    elif key=='fibonacci':
        window=rows[-35:-2]
        bottom=min(window,key=lambda c:c.l);top=max(window,key=lambda c:c.h)
        for ratio in (.5,.618,.786):
            price=top.h-ratio*(top.h-bottom.l) if up else bottom.l+ratio*(top.h-bottom.l)
            if last.l<=price+.15*average and last.h>=price-.15*average and (last.c>price if up else last.c<price):
                level('frozen_fibonacci_level',price)
                contract['anchors']=[bottom.t,top.t]
                contract['anchors'].append(reaction_start(price,.15*average))
                contract['retracement_ratio']=ratio
                break
    elif key=='ichimoku':
        from .strategies.apex_engines import _ichimoku_line
        n=len(rows)-1
        cloud_a=(_ichimoku_line(rows,n-26,9)+_ichimoku_line(rows,n-26,26))/2
        cloud_b=_ichimoku_line(rows,n-26,52)
        level('frozen_cloud_boundary',max(cloud_a,cloud_b) if up else min(cloud_a,cloud_b))
    elif key=='vwap':
        window=rows[-21:]
        price=sum((c.h+c.l+c.c)/3*c.v for c in window)/sum(c.v for c in window)
        level('frozen_vwap21',price)
    else:
        # CCI, stochastic, EMA/MACD and HA have event triggers, not S/R zones.
        # Freeze their confirming candle's opposite extreme as failure of the
        # original directional recovery, in addition to the generic ATR guard.
        level('confirmation_extreme',last.l if up else last.h)
    return {**candidate,'setup_contract':contract}


def movement_id(signal):
    contract=signal.get('setup_contract')
    from .manual_price_action import VERSION as MANUAL_VERSION
    from .manual_book import SUPPORTED_VERSIONS
    if not contract or contract.get('version') not in (SETUP_VERSION,MANUAL_VERSION,*SUPPORTED_VERSIONS):return None
    value={'version':contract['version'],'source_identity':signal['source_identity'],'source_key':signal['source_key'],
        'asset':signal['asset'],'timeframe':signal['timeframe'],'direction':signal['direction'],
        'family':contract['movement_family'],'anchors':contract['anchors']}
    if contract.get('retracement_ratio') is not None:value['retracement_ratio']=contract['retracement_ratio']
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def check_setup(signal, closed, now):
    contract=signal.get('setup_contract')
    if contract is None:
        return {'setup_contract_status':'legacy_generic_only'},''
    from .manual_price_action import VERSION as MANUAL_VERSION
    from .manual_book import SUPPORTED_VERSIONS
    if contract.get('version') not in (SETUP_VERSION,MANUAL_VERSION,*SUPPORTED_VERSIONS) or contract.get('direction')!=signal['direction']:
        raise DataError('Contrato original do setup inválido.','setup_geometry_invalid')
    deadline=contract.get('valid_until')
    if deadline is not None and (type(deadline) not in (int,float) or not math.isfinite(deadline)):
        raise DataError('Validade técnica inválida.','setup_geometry_invalid')
    audit={'setup_contract_version':contract['version'],'original_levels':contract['levels'],
           'setup_valid_until':deadline,'setup_contract_status':'checked'}
    if deadline is not None and (now>deadline or signal['entry_at']>deadline):
        return audit,'Setup vencido antes da entrada programada.'
    fresh=[c for c in closed if c.t>=contract['confirmed_at']]
    for rule in contract['invalidations']:
        price=rule.get('price');mode=rule.get('mode')
        if (type(price) not in (int,float) or not math.isfinite(price) or price<=0
                or mode not in ('close_below','close_above','wick_below','wick_above')):
            raise DataError('Regra original de invalidação inválida.','setup_geometry_invalid')
        for candle in fresh:
            actual=candle.c if mode.startswith('close') else candle.l if mode=='wick_below' else candle.h
            crossed=(actual<=price if rule.get('inclusive') else actual<price) if mode.endswith('below') else (
                actual>=price if rule.get('inclusive') else actual>price)
            if crossed:
                audit.update(invalidated_level=rule['level'],invalidation_candle=candle.json())
                return audit,'Região original do setup invalidada antes da entrada: '+rule['level']+'.'
    return audit,''
