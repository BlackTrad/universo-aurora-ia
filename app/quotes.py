"""Public quotes and percentage against the last close before Brasília midnight."""
import asyncio
from collections import OrderedDict
import time
from datetime import datetime
from .cache_locks import KeyedLocks
from .logging_setup import BRASILIA
from .market import Candle,DataError,valid_candle

def midnight_at(timestamp):
    return int(datetime.fromtimestamp(timestamp,BRASILIA).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())

class Quotes:
    def __init__(self):
        self.capacity=256
        self.cache=OrderedDict();self.references=OrderedDict();self.reference_retries=OrderedDict()
        self.locks=KeyedLocks();self.slots=asyncio.Semaphore(2)

    async def get(self,provider,asset):
        key=(getattr(provider,'identity',id(provider)),asset)
        async with self.locks.hold(key):
            now=time.time();midnight=midnight_at(now)
            cached=self.cache.get(key)
            if cached and time.monotonic()-cached[0]<30 and cached[1].get('reference_day_at')==midnight:
                self.cache.move_to_end(key)
                return cached[1]
            async with self.slots:
                now=time.time();midnight=midnight_at(now)
                result={'asset':asset,'source':getattr(provider,'name','Fonte pública'),'source_identity':getattr(provider,'identity',None),'price':None,'change_pct':None,'status':'unavailable','received_at':now,
                        'reference_day_at':midnight,
                        'change_basis':'Comparação com o último fechamento M1 anterior a 00h de Brasília. Em feriados e fins de semana pode ser o último dia negociado.'}
                try:
                    closed=False
                    try:await asyncio.wait_for(provider.check_asset(asset),10)
                    except DataError as error:
                        if error.code!='market_closed':raise
                        closed=True;result['status']='closed'
                    rows=await asyncio.wait_for(provider.candles(asset,60,2),10)
                    if not rows or any(not valid_candle(c) or not isinstance(c.t,int) or isinstance(c.t,bool) or c.t<=0 or c.t%60 or c.t>time.time() for c in rows) or any(a.t>=b.t for a,b in zip(rows,rows[1:])):
                        raise DataError('Cotação inconsistente.')
                    last=rows[-1]
                    result.update(price=last.c,data_at=last.t,received_at=time.time(),status='closed' if closed else 'stale' if time.time()-last.t>120 else 'live')
                    midnight=midnight_at(result['received_at']);result['reference_day_at']=midnight
                    reference=self.references.get(key)
                    if not reference or reference[0]!=midnight:
                        if time.monotonic()<self.reference_retries.get(key,0):
                            raise DataError('Referência aguardando nova consulta.')
                        self.reference_retries[key]=time.monotonic()+300
                        while len(self.reference_retries)>self.capacity:self.reference_retries.popitem(last=False)
                        if hasattr(provider,'reference_candle'):
                            candle=await asyncio.wait_for(provider.reference_candle(asset,midnight),10)
                        else:
                            raw=await asyncio.wait_for(provider.request({'ticks_history':provider.symbol(asset),'end':midnight-60,'style':'candles','granularity':60,'count':2},'candles'),10)
                            eligible=[r for r in raw['candles'] if isinstance(r,dict) and not isinstance(r.get('epoch'),bool) and float(r['epoch'])+60<=midnight]
                            row=max(eligible,key=lambda r:float(r['epoch']))
                            if any(isinstance(row[k],bool) for k in ('epoch','open','high','low','close')):raise ValueError()
                            epoch=float(row['epoch'])
                            if not epoch.is_integer():raise ValueError()
                            candle=Candle(int(epoch),*[float(row[k]) for k in ('open','high','low','close')])
                        if not valid_candle(candle) or candle.t<=0 or candle.t%60 or candle.t+60>midnight or candle.t>last.t:raise ValueError()
                        reference=(midnight,candle.c,candle.t+60)
                        self.references[key]=reference
                        self.references.move_to_end(key)
                        while len(self.references)>self.capacity:self.references.popitem(last=False)
                        self.reference_retries.pop(key,None)
                    result.update(change_pct=round(100*(last.c/reference[1]-1),4),reference_price=reference[1],reference_at=reference[2])
                except (DataError,asyncio.TimeoutError,KeyError,IndexError,ValueError,TypeError,OverflowError,AttributeError):
                    result['detail']='Variação indisponível: fechamento de referência não confirmado.' if result['price'] is not None else 'Cotação indisponível na fonte selecionada.'
                self.cache[key]=(time.monotonic(),result)
                self.cache.move_to_end(key)
                while len(self.cache)>self.capacity:
                    evicted,_=self.cache.popitem(last=False)
                    self.references.pop(evicted,None)
                    self.reference_retries.pop(evicted,None)
                return result
