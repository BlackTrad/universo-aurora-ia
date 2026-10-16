"""Local UI fixture exercising the production stream callback, never production."""
import asyncio
import tempfile
import time
from pathlib import Path
from app.config import Config
from app.live_price import PricePoint
from app.main import create_app
from app.market import Candle,CandleBatch
from tests.book_fixtures import scenario
from tests.test_system import TG

epoch=int(time.time()//300)*300+5
started=time.monotonic()
offset=0


def clock():return epoch+time.monotonic()-started+offset


class StreamFeed:
    name='TESTE SINTÉTICO';identity='test:stream-browser';ready=True
    assets=['EUR/USD'];timeframes=['M1','M5']
    def __init__(self):self.queues={};self.opened=0;self.closed=0;self.value=100.25
    async def check_asset(self,asset):pass
    async def candles(self,asset,interval=300,count=100):
        last=int(clock()//interval)*interval-interval
        if interval==300:
            source=scenario('pa_flow_hammer')['rows']
            return CandleBatch([Candle(last-(len(source)-1-i)*300,c.o,c.h,c.l,c.c) for i,c in enumerate(source)],self.identity)
        return CandleBatch([Candle(last-(3-i)*60,100.25,100.3,100.2,100.25) for i in range(4)],self.identity)
    async def live_price(self,asset):return PricePoint(self.value,clock(),clock(),asset,self.identity)
    async def stream_prices(self,assets):
        self.opened+=1;queue=self.queues.setdefault(assets[0],asyncio.Queue())
        try:
            while True:
                point=await queue.get()
                try:yield point
                finally:queue.task_done()
        finally:self.closed+=1


feed=StreamFeed();temp=tempfile.TemporaryDirectory();tg=TG();tg.configured=False
app=create_app(Config(database=':memory:',assets=['EUR/USD']),feed,tg,clock=clock,credentials_path=Path(temp.name)/'.env')


@app.get('/_test/streams')
async def streams():return {'opened':feed.opened,'closed':feed.closed,'synthetic':True}


@app.post('/_test/stream_price')
async def stream_price(price:float=100.25):
    feed.value=price;t=clock();queue=feed.queues['EUR/USD']
    await queue.put(PricePoint(price,t,t,'EUR/USD',feed.identity))
    await asyncio.wait_for(queue.join(),5)
    return {'synthetic':True}


@app.post('/_test/confirm_entry')
async def confirm_entry():
    global offset
    service=app.state.service;signal=service.store.active()[0]
    offset+=signal['entry_at']-40-clock()
    await service.revalidate_entry(signal)
    return {'synthetic':True,'status':service.store.signal(signal['id'])['entry_validation']['status']}
