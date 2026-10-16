"""Synthetic integration server. Never imported by the production application."""
import time
from fastapi import HTTPException
from app.config import Config
from app.main import create_app
from app.market import Candle, CandleBatch
from tests.test_system import Feed, TG, fixture


_test_epoch=int(time.time()//300)*300+5
_test_started=time.monotonic()
def test_clock():
    return _test_epoch+time.monotonic()-_test_started


class ReleaseFeed(Feed):
    name='TESTE SINTÉTICO'
    identity='test:release'
    final_price=101
    clock=staticmethod(test_clock)

    async def check_asset(self, asset):
        pass

    def symbol(self, asset):
        return 'frx'+asset.replace('/','')

    async def request(self, payload, expected):
        t=int(payload['end']//60)*60
        return {'candles':[{'epoch':t,'open':100,'high':101,'low':99,'close':100}]}

    async def candles(self, asset, interval=300, count=100):
        source=fixture();last=int(self.clock()//interval)*interval-interval
        return CandleBatch([Candle(last-(len(source)-1-i)*interval,c.o,c.h,c.l,c.c)
                            for i,c in enumerate(source)][-count:],self.identity)

    async def settlement_candles(self, asset, entry, expiry):
        p=self.final_price
        return CandleBatch([Candle(entry,100,100,100,100),Candle(expiry,p,p,p,p)],self.identity)


feed=ReleaseFeed()
import tempfile
from pathlib import Path
_test_dir=tempfile.TemporaryDirectory()
app=create_app(Config(database=':memory:'),feed,TG(),credentials_path=Path(_test_dir.name)/'.env',clock=test_clock)

@app.post('/_test/public_sources')
async def public_sources():
    class Binance(ReleaseFeed):
        name='Binance';identity='binance:test';assets=['BTC/USDT','ETH/USDT'];timeframes=['M1','M5']
        async def reference_candle(self,asset,midnight):return Candle(midnight-60,100,101,99,100)
    class Coinbase(Binance):
        name='Coinbase';identity='coinbase:test';assets=['BTC/USD','ETH/USD']
    app.state.service.providers.update(binance=Binance(),coinbase=Coinbase())
    return {'status':'test_sources_added'}


@app.post('/_test/expire')
async def expire():
    service=app.state.service
    await service.stop_scan()
    signal=service.store.current_signal()
    if not signal:raise HTTPException(409,'No test signal')
    # Synthetic public-price proof is installed only for this settlement stage;
    # boot/initial scan fixtures retain their original closed-only behavior.
    from app.live_price import PricePoint
    async def synthetic_live_price(asset):
        s=next(s for s in service.store.active() if s['asset']==asset)
        p=(s.get('live_observation') or {}).get('price',s['evidence']['candles'][-1]['c'])
        now=service.clock()
        return PricePoint(p,now,now,asset,s['source_identity'])
    feed.live_price=synthetic_live_price
    service.clock=lambda:signal['entry_at']-45
    feed.clock=lambda:service.clock()
    await service.tick()
    while service.tasks:
        import asyncio
        await asyncio.gather(*list(service.tasks))
    for active in service.store.active():
        if active.get('entry_validation',{}).get('status')!='confirmada':
            raise HTTPException(409,'Synthetic entry did not pass real validation path')
    pending=list(service.store.active())
    for entry in sorted({s['entry_at'] for s in pending}):
        service.clock=lambda at=entry:at-1
        for active in pending:
            if active['entry_at']==entry:
                service.watch_pending_price((active['source_key'],active['asset']),await feed.live_price(active['asset']))
        service.clock=lambda at=entry:at
        await service.tick()
    feed.final_price=101 if signal['direction']=='CALL' else 99
    service.clock=lambda:signal['expiry_at']+65
    await service.tick()
    return {'status':'test_clock_advanced'}

@app.post('/_test/emit_catalogue_signal')
async def emit_catalogue_signal(source:str='coinbase',timeframe:str='M5',entry_offset_seconds:int=600):
    import uuid
    service=app.state.service;now=service.clock();sid=uuid.uuid4().hex
    asset='BTC/USD' if source=='coinbase' else 'BTC/USDT' if source=='binance' else 'GBP/USD'
    s={'id':sid,'asset':asset,'source_key':source,'source':service.providers[source].name,
       'source_identity':getattr(service.providers[source],'identity','test:release'),'direction':'CALL','score':90,
       'created_at':now,'emitted_at':now,'entry_at':int(now//60)*60+entry_offset_seconds,'expiry_at':int(now//60)*60+entry_offset_seconds+300,
       'timeframe':timeframe,'duration_seconds':300,'strategy_resolved':'cci','strategy':'scan_all','reasons':['Synthetic test only'],
       'state':'aguardando entrada','result':'Resultado indisponível','settlement_pending':True,
       'result_kind':'indicativo','broker_confirmed':False,'telegram':{'status':'enviado'},'evidence':{}}
    from app.explanations import build_explanation
    s['engine_explanation']=build_explanation(s)
    service.store.add_signal(s,sid)
    return {'signal':s}

@app.post('/_test/resolve_catalogue_signal')
async def resolve_catalogue_signal(sid:str):
    service=app.state.service;s=service.store.signal(sid)
    service.clock=lambda:s['expiry_at']+65
    service.store.patch(sid,{'state':'encerrado','result':'WIN','settlement_pending':False,
                             'settled_at':service.clock(),'entry_price':100,'expiry_price':101,
                             'settlement_source_identity':s['source_identity'],'settlement_entry_at':s['entry_at'],
                             'settlement_expiry_at':s['expiry_at'],'settlement_evidence':[Candle(s['entry_at'],100,101,99,100).json(),Candle(s['expiry_at'],101,102,100,101).json()]})
    return {'ok':True}
