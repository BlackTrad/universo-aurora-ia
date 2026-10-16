"""Observation-only context from quotes already available; no provider calls."""
from collections import defaultdict
from .market import Candle, valid_candle
from .strategies.helpers import atr, closes, ema_series

CONTEXT_VERSION='1.26.2-context-1'


def structure_context(rows,interval,cutoff):
    closed=[c for c in rows if c.t+interval<=cutoff]
    unavailable={'version':CONTEXT_VERSION,'status':'insufficient','regime':'transicao',
                 'direction':None,'cutoff':cutoff,'operational_filter':False}
    if len(closed)<30:return unavailable
    volatility=atr(closed)
    if not volatility or volatility<=0:return unavailable
    highs=[];lows=[]
    for i in range(2,len(closed)-2):
        c=closed[i];neighbours=closed[i-2:i]+closed[i+1:i+3]
        proof={'at':c.t,'price':c.h,'confirmed_at':closed[i+2].t+interval}
        if all(c.h>x.h for x in neighbours):highs.append(proof)
        if all(c.l<x.l for x in neighbours):lows.append({**proof,'price':c.l})
    # A very old swing does not define the current regime.
    highs=[p for p in highs if p['at']>=closed[-1].t-40*interval][-2:]
    lows=[p for p in lows if p['at']>=closed[-1].t-40*interval][-2:]
    result={**unavailable,'highs':highs,'lows':lows,'atr':volatility,
            'confirmation':'fractal 2+2; confirmed_at <= cutoff'}
    if len(highs)<2 or len(lows)<2:return result
    dh=(highs[-1]['price']-highs[-2]['price'])/volatility
    dl=(lows[-1]['price']-lows[-2]['price'])/volatility
    direction='CALL' if dh>.1 and dl>.1 else 'PUT' if dh<-.1 and dl<-.1 else None
    width=(min(p['price'] for p in highs)-max(p['price'] for p in lows))/volatility
    lateral=abs(dh)<=.25 and abs(dl)<=.25 and width>=1.5
    return {**result,'status':'available','direction':direction,
            'regime':'tendencial' if direction else 'lateral' if lateral else 'transicao',
            'high_change_atr':dh,'low_change_atr':dl,'range_width_atr':width,
            'note':'Classificação estrutural experimental; não substitui o regime operacional.'}


def higher_context(rows,interval,cutoff):
    target={60:300,120:600,180:900,300:900}.get(interval)
    base={'version':CONTEXT_VERSION,'status':'unavailable','cutoff':cutoff,
          'timeframe':f'M{target//60}' if target else None,'operational_filter':False,
          'provenance':'aggregated_same_source','additional_api_calls':0}
    if not target:return {**base,'reason':'Intervalo não suportado para agregação.'}
    closed=[c for c in rows if c.t+interval<=cutoff]
    if (any(not valid_candle(c) or type(c.t) is not int or c.t%interval for c in closed)
            or any(b.t-a.t!=interval for a,b in zip(closed,closed[1:]))):
        return {**base,'reason':'Dados base com lacunas, duplicidade ou fora de ordem.'}
    groups=defaultdict(list)
    for c in closed:groups[c.t//target*target].append(c)
    candles=[]
    for start,group in sorted(groups.items()):
        if start+target>cutoff or [c.t for c in group]!=list(range(start,start+target,interval)):continue
        volume=sum(c.v for c in group) if all(c.v is not None for c in group) else None
        candles.append(Candle(start,group[0].o,max(c.h for c in group),min(c.l for c in group),group[-1].c,volume))
    if len(candles)<22 or candles[-1].t+target!=int(cutoff//target)*target:
        return {**base,'reason':'Candles superiores completos insuficientes ou última vela fechada ausente.',
                'closed_candles':len(candles),'minimum_candles':22}
    values=ema_series(closes(candles),21);delta=values[-1]-values[-2]
    direction='CALL' if delta>0 and candles[-1].c>values[-1] else 'PUT' if delta<0 and candles[-1].c<values[-1] else None
    return {**base,'status':'available','direction':direction,'ema21_delta':delta,
            'closed_candles':len(candles),'last_close_at':candles[-1].t+target,
            'candles':[c.json() for c in candles],
            'reason':'Contexto superior fechado, disponível somente para observação.'}


def feature_status():
    return {'news':{'state':'informative','active_filter':False,'source':'Federal Reserve RSS',
                    'reason':'Sem calendário confiável de eventos e impactos; notícias não autorizam ou vetam entradas.'},
            'higher_timeframe':{'state':'observation_only','active_filter':False,
                    'reason':'Agregação de janela acumulada de candles fechados já recebidos, sem novas consultas; aquecimento, lacunas e revisões registrados.'},
            'regime':{'state':'operational_heuristic','operational_method':'EMA9/21 normalizada por ATR',
                    'experimental_method':'pivôs 2+2 confirmados: máximas e mínimas'},
            'learning':{'state':'limited_empirical_preference','maximum_bonus':3,
                    'minimum_train':60,'minimum_validation':30,'autonomous_ai':False}}
