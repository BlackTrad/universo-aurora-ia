"""Causal M1–M5 technical setups. Never infer missing volume or future candles."""
from math import isfinite
import re
from .helpers import atr, body_ratio, closes, rsi, score_result, valid_setup_candle


def _base(candles, count=40):
    if len(candles) < count:
        return None, f'Histórico insuficiente: são necessários {count} candles fechados.'
    average = atr(candles)
    if average is None or candles[-1].h-candles[-1].l > 2.5*average:
        return None, 'Volatilidade incompatível com o setup.'
    return average, ''


def _signal(candles, direction, label, reasons, average):
    last = candles[-1]
    width = last.h-last.l
    location = ((last.c-last.l) if direction=='CALL' else (last.h-last.c))/width if width else 0
    body_points = round(10*body_ratio(last), 2)
    close_points = round(10*max(0,min(1,location)), 2)
    range_points = 10 if valid_setup_candle(last,average) else 0
    # Mandatory setup conditions earn 70 heuristic points; no estimated probability.
    points = round(70+body_points+close_points+range_points, 2)
    reasons = [re.sub(r' \(\d+ pts\)', '', reason) for reason in reasons]
    reasons += [f'Condições obrigatórias confirmadas (70 pts); corpo ({body_points} pts); fechamento direcional ({close_points} pts); amplitude ATR ({range_points} pts). Pontuação heurística, não probabilidade.']
    return score_result(direction, min(points,100), [label+': '+reasons[0]]+reasons[1:], last), ''


def _bull(c): return c.c > c.o and body_ratio(c) >= .45

def _bear(c): return c.c < c.o and body_ratio(c) >= .45


def _rsi_series(candles):
    values = closes(candles)
    return [rsi(values[:i+1]) for i in range(len(values))]


def rsi_divergence(candles):
    average, error = _base(candles)
    if error: return None, error
    values = _rsi_series(candles)
    # Only pivots with a completed candle on each side are confirmed.
    lows = [i for i in range(max(15,len(candles)-22),len(candles)-2)
            if candles[i].l < candles[i-1].l and candles[i].l < candles[i+1].l]
    highs = [i for i in range(max(15,len(candles)-22),len(candles)-2)
             if candles[i].h > candles[i-1].h and candles[i].h > candles[i+1].h]
    for direction, pivots, compare, confirm in (
        ('CALL',lows,lambda a,b: candles[b].l < candles[a].l and values[b] > values[a]+2,_bull),
        ('PUT',highs,lambda a,b: candles[b].h > candles[a].h and values[b] < values[a]-2,_bear)):
        if len(pivots)>=2 and pivots[-1]>=len(candles)-6 and compare(pivots[-2],pivots[-1]) and confirm(candles[-1]):
            return _signal(candles,direction,'RSI + divergência',
                ['Dois pivôs confirmados de preço e RSI(14) em divergência (40 pts)',
                 'Segundo pivô recente com reversão direcional fechada (45 pts)'],average)
    return None, 'Sem dois pivôs confirmados de divergência RSI e candle de reversão.'


def _stoch(candles, index):
    # Slow stochastic (14,3,3): raw K averaged over 3, then D over 3.
    def raw(i):
        window=candles[i-13:i+1]; lo=min(c.l for c in window); hi=max(c.h for c in window)
        return 50 if hi==lo else 100*(candles[i].c-lo)/(hi-lo)
    def slow(i): return sum(raw(j) for j in range(i-2,i+1))/3
    return slow(index),sum(slow(j) for j in range(index-2,index+1))/3


def stochastic(candles):
    average,error=_base(candles)
    if error: return None,error
    previous, current = _stoch(candles,len(candles)-2),_stoch(candles,len(candles)-1)
    if previous[0]<=previous[1] and current[0]>current[1] and previous[0]<=20 and _bull(candles[-1]):
        return _signal(candles,'CALL','Estocástico 14/3/3',
            ['Cruzamento ascendente K/D após sobrevenda ≤20 (45 pts)','Confirmação de alta no último candle (40 pts)'],average)
    if previous[0]>=previous[1] and current[0]<current[1] and previous[0]>=80 and _bear(candles[-1]):
        return _signal(candles,'PUT','Estocástico 14/3/3',
            ['Cruzamento descendente K/D após sobrecompra ≥80 (45 pts)','Confirmação de baixa no último candle (40 pts)'],average)
    return None,'Sem cruzamento Estocástico 14/3/3 no extremo com confirmação.'


def _cci(candles, end):
    typical=[(c.h+c.l+c.c)/3 for c in candles[end-13:end+1]]
    mean=sum(typical)/14; deviation=sum(abs(v-mean) for v in typical)/14
    return (typical[-1]-mean)/(.015*deviation) if deviation else 0


def cci(candles):
    average,error=_base(candles)
    if error:return None,error
    previous,current=_cci(candles,len(candles)-2),_cci(candles,len(candles)-1)
    if previous<=-100 and current>-100 and _bull(candles[-1]):
        return _signal(candles,'CALL','CCI(14)', ['CCI cruzou acima de −100 (45 pts)','Candle direcional de recuperação (40 pts)'],average)
    if previous>=100 and current<100 and _bear(candles[-1]):
        return _signal(candles,'PUT','CCI(14)', ['CCI cruzou abaixo de +100 (45 pts)','Candle direcional de rejeição (40 pts)'],average)
    return None,'CCI(14) não retornou de ±100 com candle de confirmação.'


def consolidation(candles):
    average,error=_base(candles)
    if error:return None,error
    window=candles[-13:-1]; high=max(c.h for c in window); low=min(c.l for c in window)
    if high-low>4*average: return None,'Últimos 12 candles não formam consolidação estreita.'
    last=candles[-1]
    if last.c>high+.05*average and _bull(last):direction='CALL'
    elif last.c<low-.05*average and _bear(last):direction='PUT'
    else:return None,'Sem rompimento de consolidação por fechamento e corpo direcional.'
    return _signal(candles,direction,'Rompimento de consolidação',
        ['Faixa dos 12 candles anteriores ≤4 ATR(14) (35 pts)',
         'Fechamento além da faixa com corpo ≥45% (50 pts)'],average)


def _ichimoku_line(candles,end,period):
    window=candles[end-period+1:end+1]
    return (max(c.h for c in window)+min(c.l for c in window))/2


def ichimoku(candles):
    average,error=_base(candles,80)
    if error:return None,error
    n=len(candles)-1
    tenkan=_ichimoku_line(candles,n,9); kijun=_ichimoku_line(candles,n,26)
    prior_tenkan=_ichimoku_line(candles,n-1,9); prior_kijun=_ichimoku_line(candles,n-1,26)
    # The cloud observed at n was projected 26 completed candles ago.
    cloud_a=(_ichimoku_line(candles,n-26,9)+_ichimoku_line(candles,n-26,26))/2
    cloud_b=_ichimoku_line(candles,n-26,52)
    last=candles[-1]
    if prior_tenkan<=prior_kijun and tenkan>kijun and last.c>max(cloud_a,cloud_b) and last.c>candles[n-26].h and _bull(last):
        return _signal(candles,'CALL','Ichimoku', ['Tenkan cruzou acima de Kijun (30 pts)',
            'Fechamento acima da nuvem projetada há 26 candles (30 pts)',
            'Chikou acima do candle de 26 períodos atrás e confirmação (25 pts)'],average)
    if prior_tenkan>=prior_kijun and tenkan<kijun and last.c<min(cloud_a,cloud_b) and last.c<candles[n-26].l and _bear(last):
        return _signal(candles,'PUT','Ichimoku', ['Tenkan cruzou abaixo de Kijun (30 pts)',
            'Fechamento abaixo da nuvem projetada há 26 candles (30 pts)',
            'Chikou abaixo do candle de 26 períodos atrás e confirmação (25 pts)'],average)
    return None,'Sem cruzamento Ichimoku com nuvem e Chikou alinhados.'


def fibonacci(candles):
    average,error=_base(candles)
    if error:return None,error
    window=candles[-35:-2]
    low=min(range(len(window)),key=lambda i:window[i].l)
    high=max(range(len(window)),key=lambda i:window[i].h)
    if abs(high-low)<4:return None,'Sem impulso identificável para retração Fibonacci.'
    up=low<high
    bottom,top=window[low].l,window[high].h
    if top-bottom<3*average:return None,'Amplitude do impulso inferior a 3 ATR.'
    last=candles[-1]
    for ratio in (.5,.618,.786):
        level=top-ratio*(top-bottom) if up else bottom+ratio*(top-bottom)
        if last.l<=level+.15*average and last.h>=level-.15*average and (
            _bull(last) and last.c>level if up else _bear(last) and last.c<level):
            return _signal(candles,'CALL' if up else 'PUT','Fibonacci',
                [f'Retração {ratio*100:g}% de impulso confirmado dos últimos 33 candles (40 pts)',
                 'Toque no nível e candle fechado de rejeição (45 pts)'],average)
    return None,'Sem reação fechada em Fibonacci 50%, 61,8% ou 78,6%.'


def heikin_ashi(candles):
    average,error=_base(candles)
    if error:return None,error
    ha=[]; ha_open=(candles[0].o+candles[0].c)/2
    for c in candles:
        ha_close=(c.o+c.h+c.l+c.c)/4
        ha.append((ha_open,ha_close))
        ha_open=(ha_open+ha_close)/2
    if ha[-3][1]<ha[-3][0] and all(x[1]>x[0] for x in ha[-2:]) and _bull(candles[-1]):direction='CALL'
    elif ha[-3][1]>ha[-3][0] and all(x[1]<x[0] for x in ha[-2:]) and _bear(candles[-1]):direction='PUT'
    else:return None,'Sem reversão Heikin Ashi por dois candles fechados e confirmação OHLC.'
    return _signal(candles,direction,'Heikin Ashi',
        ['Mudança de cor seguida por dois candles HA fechados na nova direção (45 pts)',
         'Candle OHLC final confirma direção e corpo (40 pts)'],average)




def candlestick_sr(candles):
    average,error=_base(candles)
    if error:return None,error
    previous,last=candles[-2:];window=candles[-22:-2]
    support=min(c.l for c in window);resistance=max(c.h for c in window)
    for direction,level in (('CALL',support),('PUT',resistance)):
        up=direction=='CALL'
        near=(min(previous.l,last.l)<=level+.2*average if up else max(previous.h,last.h)>=level-.2*average)
        engulf=(previous.c<previous.o and last.c>=previous.o and last.o<=previous.c if up else
                previous.c>previous.o and last.c<=previous.o and last.o>=previous.c)
        wick=(min(last.o,last.c)-last.l>=2*abs(last.c-last.o) if up else
              last.h-max(last.o,last.c)>=2*abs(last.c-last.o))
        # Pin bars and hammers may have a small body. Direction and candle
        # confirmation are checked separately below.
        pin=wick and last.h>last.l and abs(last.c-last.o)/(last.h-last.l)<=.35
        directional=(last.c>last.o) if up else (last.c<last.o)
        close=directional and (body_ratio(last)>=.45 or pin)
        doji=body_ratio(previous)<=.1 and directional and (last.c>previous.h if up else last.c<previous.l)
        if near and close and (engulf or pin or doji) and (last.c>level if up else last.c<level):
            pattern='Engolfo' if engulf else 'Martelo / Pin Bar' if pin and up else 'Estrela Cadente / Pin Bar' if pin else 'Doji confirmado'
            return _signal(candles,direction,'Candlestick em S/R',
                ['Suporte/resistência dos 20 candles anteriores tocado (35 pts)',
                 f'{pattern} confirmado por fechamento na direção (50 pts)'],average)
    return None,'Sem engolfo, pin bar, martelo, estrela cadente ou doji confirmado em S/R.'




def _volume(candles):
    values=[getattr(c,'v',None) for c in candles[-21:]]
    if any(v is None or not isinstance(v,(int,float)) or isinstance(v,bool) or not isfinite(v) or v<=0 for v in values):
        return None,'Volume real indisponível nos candles da fonte; estratégia bloqueada.'
    return values,''


def sr_volume_breakout(candles):
    average,error=_base(candles)
    if error:return None,error
    volume,error=_volume(candles)
    if error:return None,error
    last=candles[-1];previous=candles[-21:-1]
    if volume[-1]<1.5*sum(volume[:-1])/20:return None,'Rompimento sem volume relativo ≥1,5.'
    if last.c>max(c.h for c in previous) and _bull(last):direction='CALL'
    elif last.c<min(c.l for c in previous) and _bear(last):direction='PUT'
    else:return None,'Sem rompimento por fechamento de S/R dos últimos 20 candles.'
    return _signal(candles,direction,'Rompimento S/R + volume',
        ['Fechamento além de S/R dos 20 candles (40 pts)',
         'Volume real ≥1,5 vezes a média e candle direcional (45 pts)'],average)


def vwap(candles):
    average,error=_base(candles)
    if error:return None,error
    volume,error=_volume(candles)
    if error:return None,error
    window=candles[-21:]
    price=sum((c.h+c.l+c.c)/3*v for c,v in zip(window,volume))/sum(volume)
    last=window[-1]
    if last.l<=price<=last.h and last.c>price and _bull(last):direction='CALL'
    elif last.l<=price<=last.h and last.c<price and _bear(last):direction='PUT'
    else:return None,'Sem reação confirmada ao VWAP móvel de 21 candles.'
    return _signal(candles,direction,'VWAP móvel 21 candles',
        ['VWAP dos últimos 21 candles ponderado por volume real (40 pts)',
         'Toque no VWAP e candle direcional fechou além dele (45 pts)'],average)


