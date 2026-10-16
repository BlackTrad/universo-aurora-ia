"""Single-filter hypotheses for isolated research, never the live selector."""
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo
from .market import DataError, validate
from .strategies.helpers import atr, closes, ema_series
from .strategies.regime import classify
from .experiments import _space
from .market import Candle

FILTERS=('regime_alignment','htf_alignment','volatility_band','location_room','stretch_ema','opposing_wick')
BRASILIA=ZoneInfo('America/Sao_Paulo')
PARAMETERS={'volatility_ratio_min':.5,'volatility_ratio_max':2.,
            'maximum_ema_distance_atr':1.5,'maximum_opposing_wick_ratio':.25,
            'higher_multiplier':5,'hour_minimum_evaluated':100}


def context(rows,now,interval):
    prior=rows[:-1];reference=atr(prior)
    ranges=[atr(prior[:i+1]) for i in range(max(15,len(prior)-20),len(prior))]
    available=[x for x in ranges if x is not None and x>0]
    normal=median(available) if available else None
    last=rows[-1];width=last.h-last.l
    ema21=ema_series(closes(prior),21)[-1] if len(prior)>=21 else None
    return {'regime':classify(rows) or 'transicao','hour_brasilia':datetime.fromtimestamp(now,BRASILIA).hour,
            'weekday_brasilia':datetime.fromtimestamp(now,BRASILIA).weekday(),'atr':reference,
            'volatility_ratio':reference/normal if reference and normal else None,
            'ema_distance_atr':abs(last.c-ema21)/reference if reference and ema21 else None,
            'upper_wick_ratio':(last.h-max(last.o,last.c))/width if width else None,
            'lower_wick_ratio':(min(last.o,last.c)-last.l)/width if width else None,
            'news':{'status':'unavailable','reason':'RSS de comunicados não fornece calendário completo com surpresa/impacto por ativo.'},
            'hour_filter':{'status':'not_activated','reason':'Faixas horárias não selecionadas sem amostra independente.'},
            'higher_interval':interval*PARAMETERS['higher_multiplier']}


def apply_filter(name,rows,candidate,*,now,interval,higher=None):
    if name not in FILTERS:raise ValueError('Filtro de pesquisa desconhecido.')
    result={'filter':name,'status':'rejected','passed':False,'score_bonus':0,'parameters':PARAMETERS}
    ctx=context(rows,now,interval);up=candidate['direction']=='CALL'
    if name=='regime_alignment':
        values=closes(rows);fast=ema_series(values,9)[-1];slow=ema_series(values,21)[-1]
        passed=ctx['regime']!='tendencial' or ((fast>slow)==up)
        result.update(regime=ctx['regime'],detail='Bloqueio somente contra tendência EMA/ATR forte em expansão.')
    elif name=='htf_alignment':
        higher_interval=ctx['higher_interval']
        try:
            if not higher:raise DataError('Histórico superior fechado insuficiente.')
            # Higher candles may include one open candle; future bars never confirm.
            closed=validate(higher,now,max_age=higher_interval+30,interval=higher_interval)
            if closed[-1].t+higher_interval!=int(now//higher_interval)*higher_interval:
                raise DataError('Última vela superior fechada ausente.')
            average=ema_series(closes(closed),21);delta=average[-1]-average[-2]
            passed=delta>0 if up else delta<0
            result.update(cutoff=now,last_closed_at=closed[-1].t+higher_interval,ema21_delta=delta,
                          higher_interval=higher_interval,candle_times=[c.t for c in closed],detail='Inclinação EMA21 superior em candles já fechados.')
        except DataError as error:
            result.update(status='unavailable',detail=str(error));return result
    elif name=='volatility_band':
        value=ctx['volatility_ratio'];passed=value is not None and .5<=value<=2.
        result.update(value=value,detail='ATR anterior/mediana de 20 ATR anteriores entre 0,5 e 2, por ativo/timeframe.')
    elif name=='location_room':
        mirror=2*max(c.h for c in rows)
        oriented=rows if up else [Candle(c.t,mirror-c.o,mirror-c.l,mirror-c.h,mirror-c.c) for c in rows]
        passed=_space(oriented[:-1],oriented[-1].c,ctx['atr'])
        result.update(detail='Espaço mínimo de 0,5 ATR até pivô contrário confirmado ainda não rompido.')
    elif name=='stretch_ema':
        value=ctx['ema_distance_atr'];passed=value is not None and value<=1.5
        result.update(value=value,detail='Distância à EMA21 anterior até 1,5 ATR; sem pontos adicionais.')
    else:
        value=ctx['upper_wick_ratio' if up else 'lower_wick_ratio'];passed=value is not None and value<=.25
        result.update(value=value,detail='Pavio contrário da confirmação até 25% da amplitude.')
    result.update(passed=bool(passed),status='confirmed' if passed else 'rejected')
    return result
