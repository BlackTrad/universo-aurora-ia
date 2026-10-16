"""Fontes reais explícitas. Sem fallback, candles sintéticos ou equivalência de corretora."""
import math
from dataclasses import dataclass, asdict
from collections import defaultdict

class DataError(Exception):
    def __init__(self,message,code='invalid_data'):
        super().__init__(message)
        self.code=code

@dataclass(frozen=True)
class Candle:
    t: int  # abertura UTC, epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float | None = None  # Source-provided volume only; never infer from OHLC.

    def json(self):
        return asdict(self)

def price(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):
        raise ValueError('Preço inválido.')
    number=float(value)
    if not math.isfinite(number) or number<=0: raise ValueError('Preço inválido.')
    return number

def valid_candle(c):
    return (all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x > 0 for x in (c.o,c.h,c.l,c.c))
            and (c.v is None or (isinstance(c.v,(int,float)) and not isinstance(c.v,bool) and math.isfinite(c.v) and c.v >= 0))
            and c.l <= min(c.o,c.c) <= max(c.o,c.c) <= c.h)

def validate(candles, now, max_age=330, interval=300):
    if len(candles) < 40:
        raise DataError('Histórico incompleto: são necessários 40 candles fechados.')
    if any(not valid_candle(c) or not isinstance(c.t,int) or isinstance(c.t,bool) or c.t <= 0 or c.t % interval or c.t > now for c in candles):
        raise DataError('Cotações inconsistentes ou horário inválido.')
    if any(b.t-a.t != interval for a,b in zip(candles,candles[1:])):
        raise DataError('Histórico com lacunas, duplicidades ou fora de ordem.')
    closed = [c for c in candles if c.t+interval <= now]
    if len(closed) < 40:
        raise DataError('Candles fechados insuficientes.')
    age = now - closed[-1].t - interval
    if age > max_age:
        raise DataError('Dados atrasados ou mercado fechado; emissão bloqueada.','stale_data')
    if all(c.h == c.l for c in closed[-5:]):
        raise DataError('Cotações sem variação; emissão bloqueada.')
    return closed


def validate_latest(candles, now, max_age=330, interval=300):
    closed = validate(candles, now, max_age, interval)
    if closed[-1].t + interval != int(now // interval) * interval:
        raise DataError('A última vela fechada ainda não foi recebida.', 'missing_latest_candle')
    return closed

class CandleBatch(list):
    """Quotes and their immutable request provenance travel together."""
    def __init__(self, rows, source_identity):
        super().__init__(rows)
        self.source_identity = source_identity


def aggregate_minutes(rows, interval):
    """Complete UTC-aligned M1 groups only, without filling gaps."""
    if type(interval) is not int or interval<60 or interval%60:raise ValueError('Intervalo inválido.')
    groups=defaultdict(list)
    for c in rows:groups[c.t//interval*interval].append(c)
    result=[]
    for start,group in sorted(groups.items()):
        group=sorted(group,key=lambda c:c.t)
        if [c.t for c in group]!=list(range(start,start+interval,60)):continue
        volume=sum(c.v for c in group) if all(c.v is not None for c in group) else None
        result.append(Candle(start,group[0].o,max(c.h for c in group),min(c.l for c in group),group[-1].c,volume))
    return result
